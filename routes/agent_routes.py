# routes/agent_routes.py
"""Agent Routes — Trainable agent entry point (C21)."""

import logging
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field

from core.agent_harness import AgentHarness
from core.training_interface import TrainingInterface
from services.memory.skills import SkillsManager
from src.auth_helpers import get_current_user, effective_user
from core.context_window_calculator import ContextWindowCalculator

logger = logging.getLogger(__name__)

# In-memory dictionary for keeping harnesses and training interfaces alive.
_harnesses: Dict[str, AgentHarness] = {}
_training_interfaces: Dict[str, TrainingInterface] = {}

class AgentChatRequest(BaseModel):
    messages: List[Dict[str, Any]]
    model: Optional[str] = None
    skill: Optional[str] = None
    auto_skill: bool = True
    session_id: Optional[str] = None
    training_mode: Optional[bool] = None

class TrainStartRequest(BaseModel):
    goal: Optional[str] = None
    session_id: Optional[str] = None

class TrainCaptureRequest(BaseModel):
    user: str
    assistant: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    annotation: Optional[str] = None

class TrainCrystallizeRequest(BaseModel):
    goal: Optional[str] = None
    auto_generate: bool = False

def setup_agent_routes(skills_manager: SkillsManager) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"])

    def _get_harness(session_id: str, owner: Optional[str]) -> AgentHarness:
        if session_id in _harnesses:
            return _harnesses[session_id]
        harness = AgentHarness(skills_manager, owner=owner)
        harness.begin_session(session_id)
        _harnesses[session_id] = harness
        return harness

    def _get_training_interface(session_id: str, harness: AgentHarness) -> TrainingInterface:
        if session_id in _training_interfaces:
            return _training_interfaces[session_id]
        ti = TrainingInterface(harness)
        _training_interfaces[session_id] = ti
        return ti

    def _owner(request: Request) -> Optional[str]:
        return effective_user(request)

    @router.post("/chat")
    async def agent_chat(request: Request, body: AgentChatRequest):
        owner = _owner(request)
        messages = body.messages
        if not messages:
            raise HTTPException(400, "messages is required")

        model = body.model
        force_skill = body.skill
        auto_skill = body.auto_skill
        req_session_id = body.session_id
        training_flag = body.training_mode

        sid = req_session_id or str(uuid.uuid4())
        harness = _get_harness(sid, owner)

        if training_flag is not None:
            harness.set_training_mode(bool(training_flag))

        user_task = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        system_prompt = harness.build_system_prompt(
            _base_prompt(),
            task=user_task,
            inject_skill=force_skill,
        )

        skill_injected = None
        if force_skill:
            messages = harness.inject_skill(messages, force_skill)
            skill_injected = force_skill
        elif auto_skill and user_task:
            messages, skill_injected = harness.route_and_inject(messages, user_task)

        messages = harness.maybe_compact(messages, model=model)

        full_messages = [{"role": "system", "content": system_prompt}] + [
            m for m in messages if m.get("role") != "system"
        ]

        try:
            reply, tokens_used = await _run_chat(full_messages, session_id=sid, model=model, owner=owner)
        except Exception as e:
            logger.error(f"agent_chat LLM call failed: {e}")
            raise HTTPException(502, f"LLM call failed: {e}")

        harness.record_turn(tokens_used, skill_name=skill_injected, source="agent_chat")

        ti = _get_training_interface(sid, harness)
        if ti and ti.is_active:
            ti.capture_turn(user_task, reply, tool_calls=[])

        return {
            "reply": reply,
            "session_id": sid,
            "skill_injected": skill_injected,
            "tokens_used": tokens_used,
            "training": {
                "active": bool(ti and ti.is_active),
                "turns_captured": ti.turn_count if ti else 0,
            },
        }

    @router.post("/train/start")
    async def train_start(request: Request, body: TrainStartRequest):
        owner = _owner(request)
        sid = body.session_id or str(uuid.uuid4())

        harness = _get_harness(sid, owner)
        ti = _get_training_interface(sid, harness)
        ti.start(session_id=sid, goal=body.goal)

        return {"status": "training_started", "session_id": sid, "goal": body.goal}

    @router.post("/train/capture")
    async def train_capture(request: Request, body: TrainCaptureRequest):
        ti = None
        for active_ti in _training_interfaces.values():
            if active_ti.is_active:
                ti = active_ti
                break
        if ti is None:
            raise HTTPException(409, "No active training session found. Call /train/start first.")

        entry = ti.capture_turn(
            body.user, body.assistant,
            tool_calls=body.tool_calls or [],
            annotation=body.annotation
        )
        return {
            "status": "captured",
            "turn": entry.turn if entry else None,
            "turns_captured": ti.turn_count
        }

    @router.post("/train/stop")
    async def train_stop(request: Request):
        ti = None
        for active_ti in _training_interfaces.values():
            if active_ti.is_active:
                ti = active_ti
                break
        if ti is None:
            return {"status": "stopped", "was_active": False, "turns_captured": 0, "session_id": None}

        was_active = ti.is_active
        ti.stop()
        return {
            "status": "stopped",
            "was_active": was_active,
            "turns_captured": ti.turn_count,
            "session_id": ti._session_id,
        }

    @router.post("/train/crystallize")
    async def train_crystallize(request: Request, body: TrainCrystallizeRequest):
        owner = _owner(request)
        ti = None
        for active_ti in _training_interfaces.values():
            if active_ti.is_active or active_ti.turn_count > 0:
                ti = active_ti
                break
        if ti is None:
            raise HTTPException(400, "No training session with turns found to crystallize")
        if ti.turn_count == 0:
            raise HTTPException(422, "No turns captured; nothing to crystallize")

        payload = ti.crystallize(goal=body.goal)
        result = {"status": "crystallized", "payload": payload}

        if body.auto_generate:
            skill_result = await _auto_generate_skill(payload, owner=owner)
            result["skill_generation"] = skill_result

        return result

    @router.get("/status")
    async def agent_status(request: Request, session_id: Optional[str] = None):
        owner = _owner(request)
        harness = None
        ti = None
        if session_id:
            harness = _harnesses.get(session_id)
            if harness:
                ti = _training_interfaces.get(session_id)
        else:
            if _harnesses:
                session_id = list(_harnesses.keys())[-1]
                harness = _harnesses[session_id]
                ti = _training_interfaces.get(session_id)

        return {
            "harness": harness.status() if harness else None,
            "training": ti.status() if ti else None,
        }

    @router.get("/skills")
    async def agent_skills_index(request: Request, session_id: Optional[str] = None):
        owner = _owner(request)
        sid = session_id or str(uuid.uuid4())
        harness = _get_harness(sid, owner)
        idx = harness._registry.compact_index(owner=owner)
        return {
            "skills": idx,
            "count": len(idx),
            "estimated_tokens": harness._registry.estimated_tokens(owner=owner),
        }

    return router

def _base_prompt() -> str:
    try:
        from src.agent_loop import AGENT_SYSTEM_PROMPT
        return AGENT_SYSTEM_PROMPT
    except Exception:
        return (
            "You are ShadowRealm, a capable AI assistant. "
            "Follow instructions precisely. "
            "When a skill is provided, follow it step-by-step. "
            "Be concise and accurate."
        )

async def _run_chat(messages: List[Dict], session_id: str, model: Optional[str] = None, owner: Optional[str] = None):
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, resolved_model, headers = resolve_endpoint(model=model, owner=owner)
    reply = await llm_call_async(
        url,
        resolved_model,
        messages,
        headers=headers,
        session_id=session_id,
    )
    # Count the tokens accurately
    calc = ContextWindowCalculator()
    tokens_in = calc.count_tokens(messages)
    tokens_out = calc.count_tokens([{"role": "assistant", "content": reply}])
    return reply, (tokens_in + tokens_out)

async def _auto_generate_skill(payload: Dict, owner: Optional[str] = None) -> Dict:
    try:
        from routes.skills_routes import generate_skill_from_trace
        return await generate_skill_from_trace(payload, owner=owner)
    except Exception as e:
        logger.error(f"_auto_generate_skill failed: {e}")
        try:
            from src.constants import DATA_DIR
            sm = SkillsManager(DATA_DIR)
            sk = sm.add_skill(
                name=f"trace-{payload.get('session_id', 'unknown')[:8]}",
                description=payload.get("goal") or "Auto-crystallized from trace",
                when_to_use=payload.get("goal") or "",
                procedure=[f"Turn {t['turn']}: {t['assistant'][:120]}" for t in payload.get('turns', [])[:10]],
                source="trace-crystallize",
                owner=owner,
                status="draft",
            )
            return {"status": "draft_saved", "skill_name": sk.get("name")}
        except Exception as _e:
            logger.error(f"_auto_generate_skill fallback failed: {_e}")
    return {"status": "failed", "error": "No generator available"}
