# core/compaction_middleware.py
# Auto-compaction middleware — fires when a session's token count crosses
# the profile's compaction_limit (default 80% of model context window).
#
# What it does:
#   1. Intercepts outbound /api/chat (and compatible) requests.
#   2. Reads the current session token count from TokenCounter.
#   3. If tokens >= profile.compaction_limit, calls the summarise helper
#      to compress conversation history and resets the session counter.
#   4. Injects the summary as a system message before the request proceeds.
#
# Registration (in app.py / main FastAPI app):
#   from core.compaction_middleware import CompactionMiddleware
#   app.add_middleware(CompactionMiddleware)

from __future__ import annotations

import json
import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.context_profiles import ContextProfile, get_profile
from core.token_counter import TokenCounter

logger = logging.getLogger(__name__)

# Routes that carry conversation payloads and may need compaction.
_CHAT_PATHS = {
    "/api/chat",
    "/api/chat/stream",
    "/api/agent/chat",
    "/api/codex/chat",
}


class CompactionMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that auto-compacts conversation context at 80%.

    Compaction strategy:
      - Reads JSON body to find `session_id` and `model` fields.
      - Looks up token count via TokenCounter singleton.
      - If over the compaction limit, generates a summary prompt and
        prepends it to the `messages` array as a system message, then
        clears the session token counter so the new window starts fresh.
      - Falls back gracefully on any parse/runtime error — the original
        request always passes through unmodified on failure.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path not in _CHAT_PATHS or request.method != "POST":
            return await call_next(request)

        try:
            body_bytes = await request.body()
            payload = json.loads(body_bytes)
        except Exception:
            return await call_next(request)

        session_id: Optional[str] = payload.get("session_id") or payload.get("conversation_id")
        model: str = payload.get("model", "gpt-4o")
        messages: list = payload.get("messages", [])

        if not session_id or not messages:
            return await call_next(request)

        counter = TokenCounter.get()
        stats = counter.snapshot(session_id)
        if stats is None:
            return await call_next(request)

        profile: ContextProfile = get_profile(model)

        if stats.total < profile.compaction_limit:
            # Under threshold — pass through unchanged
            return await call_next(request)

        logger.info(
            "[Compaction] session=%s tokens=%d limit=%d model=%s — compacting",
            session_id, stats.total, profile.compaction_limit, model,
        )

        try:
            summary = _build_summary_message(messages, profile)
            # Replace history with: summary system message + last user message
            last_user = next(
                (m for m in reversed(messages) if m.get("role") == "user"), None
            )
            compacted: list = [summary]
            if last_user:
                compacted.append(last_user)

            payload["messages"] = compacted
            payload["_compacted"] = True  # Flag for downstream logging

            # Reset session counter so next window starts from zero
            counter.reset_session(session_id)
            counter.record(
                session_id=session_id,
                tokens_in=profile.summary_budget,
                tokens_out=0,
                model=model,
                source="compaction",
            )

            # Rebuild request with modified body
            new_body = json.dumps(payload).encode()
            scope = request.scope.copy()

            async def receive():
                return {"type": "http.request", "body": new_body, "more_body": False}

            modified_request = Request(scope, receive)
            return await call_next(modified_request)

        except Exception as exc:
            logger.warning("[Compaction] failed, passing through unmodified: %s", exc)
            # Reconstitute original request
            scope = request.scope.copy()

            async def receive_original():
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            original_request = Request(scope, receive_original)
            return await call_next(original_request)


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary_message(messages: list, profile: ContextProfile) -> dict:
    """Build a system message summarising the conversation so far.

    In Sprint 7B the ReflectionEngine will replace this with an LLM-generated
    summary. For now we produce a structured text summary from the raw turns.
    """
    human_turns = [m for m in messages if m.get("role") == "user"]
    assistant_turns = [m for m in messages if m.get("role") == "assistant"]
    tool_turns = [m for m in messages if m.get("role") == "tool"]

    lines = [
        "[CONTEXT COMPACTED]",
        f"The conversation history was compacted after reaching the context limit.",
        f"Summary of prior conversation ({len(messages)} messages, "
        f"{len(human_turns)} user turns, {len(assistant_turns)} assistant turns, "
        f"{len(tool_turns)} tool calls):",
        "",
    ]

    # Include last N user+assistant pairs as a brief recap
    recap_pairs = 3
    recent_pairs = []
    for m in reversed(messages):
        if m.get("role") in ("user", "assistant"):
            recent_pairs.append(m)
        if len(recent_pairs) >= recap_pairs * 2:
            break
    recent_pairs.reverse()

    for m in recent_pairs:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Multi-part content: extract text parts
            content = " ".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        snippet = str(content)[:200].replace("\n", " ")
        lines.append(f"  [{role}]: {snippet}{'...' if len(str(content)) > 200 else ''}")

    lines += [
        "",
        "Continue from this point. The full history is no longer in context.",
    ]

    return {
        "role": "system",
        "content": "\n".join(lines),
    }
