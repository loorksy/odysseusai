# api/tokens.py
# Token panel API — exposes live usage stats, per-session breakdowns,
# and the active context profile for the frontend overlay panel.
#
# Mount in app.py:
#   from api.tokens import router as tokens_router
#   app.include_router(tokens_router, prefix="/api")

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.context_profiles import all_profiles, get_profile
from core.token_counter import TokenCounter

router = APIRouter(tags=["tokens"])


# ---------------------------------------------------------------------------
# GET /api/tokens
# ---------------------------------------------------------------------------

@router.get("/tokens")
async def get_token_totals(
    model: str = Query(default="gpt-4o", description="Model name for profile lookup"),
):
    """Return lifetime token usage totals and active context profile.

    Response:
      totals      — lifetime in/out/total across all sessions
      profile     — context profile for the requested model
      all_profiles — all built-in profiles for the UI selector
    """
    counter = TokenCounter.get()
    profile = get_profile(model)
    return {
        "totals": counter.totals(),
        "profile": profile.to_dict(),
        "all_profiles": all_profiles(),
    }


# ---------------------------------------------------------------------------
# GET /api/tokens/session/{session_id}
# ---------------------------------------------------------------------------

@router.get("/tokens/session/{session_id}")
async def get_session_tokens(session_id: str):
    """Return token usage for a single session.

    Response:
      session_id  — echoed back
      stats       — tokens_in, tokens_out, total
      by_source   — breakdown by source label (skill, mcp, chat, compaction)
      by_model    — breakdown by model
    """
    counter = TokenCounter.get()
    stats = counter.snapshot(session_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    return {
        "session_id": session_id,
        "stats": {
            "tokens_in": stats.tokens_in,
            "tokens_out": stats.tokens_out,
            "total": stats.total,
        },
        "by_source": stats.by_source(),
        "by_model": stats.by_model(),
    }


# ---------------------------------------------------------------------------
# GET /api/tokens/sessions
# ---------------------------------------------------------------------------

@router.get("/tokens/sessions")
async def list_sessions(
    model: Optional[str] = Query(default=None, description="Model for profile context"),
):
    """List all tracked session IDs with their token totals."""
    counter = TokenCounter.get()
    session_ids = counter.session_ids()

    sessions = []
    for sid in session_ids:
        snap = counter.snapshot(sid)
        if snap:
            sessions.append({
                "session_id": sid,
                "tokens_in": snap.tokens_in,
                "tokens_out": snap.tokens_out,
                "total": snap.total,
            })

    profile_data = None
    if model:
        profile_data = get_profile(model).to_dict()

    return {
        "sessions": sessions,
        "count": len(sessions),
        "profile": profile_data,
    }


# ---------------------------------------------------------------------------
# DELETE /api/tokens/session/{session_id}
# ---------------------------------------------------------------------------

@router.delete("/tokens/session/{session_id}")
async def reset_session_tokens(session_id: str):
    """Clear token stats for one session (e.g. after manual compaction)."""
    counter = TokenCounter.get()
    stats = counter.snapshot(session_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    counter.reset_session(session_id)
    return {"reset": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# GET /api/tokens/profiles
# ---------------------------------------------------------------------------

@router.get("/tokens/profiles")
async def get_context_profiles():
    """Return all built-in context profiles for the frontend profile selector."""
    return {"profiles": all_profiles()}
