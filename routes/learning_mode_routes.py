"""
routes/learning_mode_routes.py
C89 — Learning Mode Toggle API Harness

Endpoints:
  GET  /api/learning-mode          — current state
  POST /api/learning-mode/enable   — enable learning mode
  POST /api/learning-mode/disable  — disable learning mode
  POST /api/learning-mode/toggle   — toggle (flip current state)

All mutations accept an optional JSON body: { "actor": "username" }
for audit-trail attribution.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import learning_mode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning-mode", tags=["learning-mode"])


class ActorPayload(BaseModel):
    actor: Optional[str] = "api"


@router.get("", summary="Get current learning mode state")
async def get_learning_mode_state():
    """
    Returns the current learning mode state including:
    - enabled (bool)
    - activated_at / deactivated_at (ISO timestamps)
    - reflection_cycle_count, proposals_generated, skills_improved (counters)
    """
    try:
        state = learning_mode.get_state()
        return {"success": True, "learning_mode": state}
    except Exception as e:
        logger.error(f"[LearningModeAPI] GET error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enable", summary="Enable learning mode")
async def enable_learning_mode(payload: ActorPayload = ActorPayload()):
    """
    Enables continuous reflection loop.
    From this point, all skill execution traces are queued for ReflectionEngine
    analysis and Skill Factory improvement proposals.
    """
    try:
        state = learning_mode.enable(activated_by=payload.actor)
        return {
            "success": True,
            "message": "Learning mode enabled. Continuous reflection is now active.",
            "learning_mode": state,
        }
    except Exception as e:
        logger.error(f"[LearningModeAPI] enable error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/disable", summary="Disable learning mode")
async def disable_learning_mode(payload: ActorPayload = ActorPayload()):
    """
    Disables continuous reflection loop.
    ReflectionEngine completes its current cycle then stops queuing new proposals.
    """
    try:
        state = learning_mode.disable(deactivated_by=payload.actor)
        return {
            "success": True,
            "message": "Learning mode disabled. Reflection will complete its current cycle then pause.",
            "learning_mode": state,
        }
    except Exception as e:
        logger.error(f"[LearningModeAPI] disable error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/toggle", summary="Toggle learning mode")
async def toggle_learning_mode(payload: ActorPayload = ActorPayload()):
    """
    Flips the current learning mode state.
    Returns the new state after toggle.
    """
    try:
        state = learning_mode.toggle(actor=payload.actor)
        new_status = "enabled" if state["enabled"] else "disabled"
        return {
            "success": True,
            "message": f"Learning mode {new_status}.",
            "learning_mode": state,
        }
    except Exception as e:
        logger.error(f"[LearningModeAPI] toggle error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
