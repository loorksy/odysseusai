"""
core/learning_mode.py
C89 — Learning Mode Toggle

Activates continuous reflection loop when enabled.
Feeds the Skill Factory loop via ReflectionEngine integration.

Design Rules:
 - State is persisted to disk (learning_mode.json) so it survives restarts.
 - All agents share the same learning mode state (no per-agent siloing).
 - When active, every skill execution trace is automatically queued for nightly
   ReflectionEngine analysis and improvement proposal generation.
 - Toggle is exposed via /api/learning-mode REST endpoint.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LEARNING_MODE_STATE_FILE = Path(os.getenv("LEARNING_MODE_STATE_FILE", "data/learning_mode.json"))

_DEFAULT_STATE = {
    "enabled": False,
    "activated_at": None,
    "deactivated_at": None,
    "activated_by": None,
    "reflection_cycle_count": 0,
    "proposals_generated": 0,
    "skills_improved": 0,
}


def _load_state() -> dict:
    """Load persisted learning mode state from disk."""
    try:
        if LEARNING_MODE_STATE_FILE.exists():
            with open(LEARNING_MODE_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                # Merge with defaults to handle missing keys from older versions
                return {**_DEFAULT_STATE, **state}
    except Exception as e:
        logger.warning(f"[LearningMode] Could not load state file: {e}. Using defaults.")
    return dict(_DEFAULT_STATE)


def _save_state(state: dict) -> None:
    """Persist learning mode state to disk."""
    try:
        LEARNING_MODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LEARNING_MODE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"[LearningMode] Could not save state: {e}")


# In-memory cache (loaded once at import, kept in sync on writes)
_state: dict = _load_state()


def is_enabled() -> bool:
    """Return True if learning mode is currently active."""
    return bool(_state.get("enabled", False))


def get_state() -> dict:
    """Return a copy of the full learning mode state."""
    return dict(_state)


def enable(activated_by: Optional[str] = "system") -> dict:
    """
    Enable learning mode.
    - Sets enabled=True and records activation timestamp.
    - From this point forward, all skill traces are queued for ReflectionEngine.
    Returns the updated state.
    """
    global _state
    if _state["enabled"]:
        logger.info("[LearningMode] Already enabled — no-op.")
        return get_state()

    _state["enabled"] = True
    _state["activated_at"] = datetime.now(timezone.utc).isoformat()
    _state["deactivated_at"] = None
    _state["activated_by"] = activated_by
    _save_state(_state)

    logger.info(f"[LearningMode] ENABLED by '{activated_by}' at {_state['activated_at']}")
    _notify_reflection_engine(enabled=True)
    return get_state()


def disable(deactivated_by: Optional[str] = "system") -> dict:
    """
    Disable learning mode.
    - Sets enabled=False, records deactivation timestamp.
    - ReflectionEngine will complete its current cycle then stop queuing new proposals.
    Returns the updated state.
    """
    global _state
    if not _state["enabled"]:
        logger.info("[LearningMode] Already disabled — no-op.")
        return get_state()

    _state["enabled"] = False
    _state["deactivated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(_state)

    logger.info(f"[LearningMode] DISABLED by '{deactivated_by}' at {_state['deactivated_at']}")
    _notify_reflection_engine(enabled=False)
    return get_state()


def toggle(actor: Optional[str] = "system") -> dict:
    """Toggle learning mode. Returns updated state."""
    if is_enabled():
        return disable(deactivated_by=actor)
    return enable(activated_by=actor)


def record_reflection_cycle() -> None:
    """Called by ReflectionEngine after each nightly cycle completes."""
    global _state
    _state["reflection_cycle_count"] = _state.get("reflection_cycle_count", 0) + 1
    _save_state(_state)
    logger.info(f"[LearningMode] Reflection cycle #{_state['reflection_cycle_count']} recorded.")


def record_proposal_generated(count: int = 1) -> None:
    """Called by ReflectionEngine when improvement proposals are generated."""
    global _state
    _state["proposals_generated"] = _state.get("proposals_generated", 0) + count
    _save_state(_state)


def record_skill_improved(count: int = 1) -> None:
    """Called when a skill is patched/improved via the Skill Factory loop."""
    global _state
    _state["skills_improved"] = _state.get("skills_improved", 0) + count
    _save_state(_state)


def _notify_reflection_engine(enabled: bool) -> None:
    """
    Notify the ReflectionEngine of the new learning mode state.
    Non-blocking — logs a warning if the engine is not yet available (Sprint 8B C85).
    Designed to be replaced with a direct call once ReflectionEngine is wired in C85.
    """
    try:
        # Import guard: ReflectionEngine is scaffolded in C85.
        # This import will succeed once C85 lands; until then it silently skips.
        from core.reflection_engine import ReflectionEngine  # noqa: PLC0415
        ReflectionEngine.set_learning_mode(enabled)
        logger.info(f"[LearningMode] ReflectionEngine notified: learning_mode={enabled}")
    except ImportError:
        logger.debug(
            "[LearningMode] ReflectionEngine not yet available (pending C85). "
            "Learning mode state saved; engine will pick it up on next startup."
        )
    except Exception as e:
        logger.warning(f"[LearningMode] ReflectionEngine notification failed: {e}")
