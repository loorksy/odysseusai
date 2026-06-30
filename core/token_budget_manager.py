"""TokenBudgetManager — Per-user / per-session token budget enforcement (C22).

Manages three budget scopes:
  - session  : tokens used in the current conversation session
  - daily    : rolling 24-hour window per owner
  - model    : per-model cap (some models cost more per token)

Each scope has a configurable soft limit (warn) and hard limit (block).
The hard limit is enforced at check_budget(); callers should call it
before every LLM call and respect the BudgetExceeded exception.

Budgets are persisted to a sidecar JSON file under data_dir so they
survive process restarts.  File I/O uses atomic_write_json.

Public API:
  mgr = TokenBudgetManager(data_dir, owner="alice")
  mgr.check_budget(estimated_tokens, model="gpt-4o", session_id="s1")
  mgr.record(tokens_used, model="gpt-4o", session_id="s1")
  mgr.status(session_id="s1")   → {session, daily, model} usage snapshot
  mgr.reset_session(session_id) → clear session counter
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (all overridable via constructor)
# ---------------------------------------------------------------------------
_DEFAULT_SESSION_SOFT = 50_000
_DEFAULT_SESSION_HARD = 100_000
_DEFAULT_DAILY_SOFT   = 500_000
_DEFAULT_DAILY_HARD   = 1_000_000
_DEFAULT_MODEL_HARD: Dict[str, int] = {
    "gpt-4o":            300_000,
    "gpt-4-turbo":       200_000,
    "o1":                150_000,
    "claude-3-5-sonnet": 300_000,
    "claude-3-opus":     150_000,
    "gemini-1.5-pro":    400_000,
}
_SECS_PER_DAY = 86_400


class BudgetExceeded(Exception):
    """Raised when a hard token budget limit is hit."""
    def __init__(self, scope: str, used: int, limit: int):
        self.scope = scope
        self.used = used
        self.limit = limit
        super().__init__(f"{scope} budget exceeded: {used}/{limit} tokens")


class BudgetWarning(UserWarning):
    """Issued (not raised) when a soft token budget threshold is crossed."""


class TokenBudgetManager:
    """Tracks and enforces token budgets across session / daily / model scopes."""

    def __init__(
        self,
        data_dir: str,
        owner: Optional[str] = None,
        *,
        session_soft: int = _DEFAULT_SESSION_SOFT,
        session_hard: int = _DEFAULT_SESSION_HARD,
        daily_soft: int   = _DEFAULT_DAILY_SOFT,
        daily_hard: int   = _DEFAULT_DAILY_HARD,
        model_hard: Optional[Dict[str, int]] = None,
    ):
        self.owner = owner or "__global__"
        self._session_soft = session_soft
        self._session_hard = session_hard
        self._daily_soft   = daily_soft
        self._daily_hard   = daily_hard
        self._model_hard   = {**_DEFAULT_MODEL_HARD, **(model_hard or {})}

        safe_owner = "".join(c for c in self.owner if c.isalnum() or c in "-_")
        self._budget_file = os.path.join(data_dir, "token_budgets", f"{safe_owner}.json")
        os.makedirs(os.path.dirname(self._budget_file), exist_ok=True)

        self._data = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> Dict:
        if not os.path.exists(self._budget_file):
            return {"sessions": {}, "daily": {}, "model": {}}
        try:
            with open(self._budget_file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {"sessions": {}, "daily": {}, "model": {}}
        except Exception as e:
            logger.warning(f"TokenBudgetManager: failed to load {self._budget_file}: {e}")
            return {"sessions": {}, "daily": {}, "model": {}}

    def _save(self) -> None:
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self._budget_file, self._data, indent=2)
        except Exception as e:
            logger.warning(f"TokenBudgetManager: failed to save budgets: {e}")

    # ------------------------------------------------------------------
    # Internal counters
    # ------------------------------------------------------------------

    def _session_used(self, session_id: str) -> int:
        return int(self._data.get("sessions", {}).get(session_id, {}).get("tokens", 0))

    def _daily_used(self) -> int:
        """Sum tokens recorded in the last 24 hours from the daily ledger."""
        cutoff = time.time() - _SECS_PER_DAY
        total = 0
        for entry in self._data.get("daily", {}).values():
            if isinstance(entry, dict) and entry.get("ts", 0) >= cutoff:
                total += int(entry.get("tokens", 0))
        return total

    def _model_used(self, model: str) -> int:
        return int(self._data.get("model", {}).get(model, {}).get("tokens", 0))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_budget(
        self,
        estimated_tokens: int,
        *,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict:
        """Raise BudgetExceeded if a hard limit would be crossed.

        Returns a dict of current usage (safe to log/surface to UI).
        Emits a BudgetWarning (via warnings.warn) when crossing a soft limit.
        """
        import warnings

        session_used = self._session_used(session_id) if session_id else 0
        daily_used   = self._daily_used()
        model_used   = self._model_used(model) if model else 0
        model_hard   = self._model_hard.get(model, 0) if model else 0

        # Hard limits
        if session_id and (session_used + estimated_tokens) > self._session_hard:
            raise BudgetExceeded("session", session_used + estimated_tokens, self._session_hard)
        if (daily_used + estimated_tokens) > self._daily_hard:
            raise BudgetExceeded("daily", daily_used + estimated_tokens, self._daily_hard)
        if model_hard and (model_used + estimated_tokens) > model_hard:
            raise BudgetExceeded(f"model:{model}", model_used + estimated_tokens, model_hard)

        # Soft warnings
        if session_id and (session_used + estimated_tokens) > self._session_soft:
            warnings.warn(
                f"Session token budget soft limit approaching ({session_used + estimated_tokens}/{self._session_soft})",
                BudgetWarning, stacklevel=2,
            )
        if (daily_used + estimated_tokens) > self._daily_soft:
            warnings.warn(
                f"Daily token budget soft limit approaching ({daily_used + estimated_tokens}/{self._daily_soft})",
                BudgetWarning, stacklevel=2,
            )

        return {
            "session": {"used": session_used, "hard": self._session_hard},
            "daily":   {"used": daily_used,   "hard": self._daily_hard},
            "model":   {"used": model_used,   "hard": model_hard} if model else {},
        }

    def record(
        self,
        tokens_used: int,
        *,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "chat",
    ) -> None:
        """Record actual tokens used after an LLM call."""
        now = time.time()

        if session_id:
            sess = self._data.setdefault("sessions", {}).setdefault(session_id, {"tokens": 0, "started_at": now})
            sess["tokens"] = int(sess.get("tokens", 0)) + tokens_used
            sess["last_used"] = now

        # Daily ledger: keyed by a compact timestamp bucket (minute-level)
        # so entries can be GC'd efficiently and the file doesn't grow forever.
        bucket = str(int(now // 60))  # one bucket per minute
        day = self._data.setdefault("daily", {})
        entry = day.setdefault(bucket, {"tokens": 0, "ts": now})
        entry["tokens"] = int(entry.get("tokens", 0)) + tokens_used
        self._gc_daily()

        if model:
            mdl = self._data.setdefault("model", {}).setdefault(model, {"tokens": 0})
            mdl["tokens"] = int(mdl.get("tokens", 0)) + tokens_used
            mdl["last_used"] = now

        self._save()

    def reset_session(self, session_id: str) -> None:
        """Clear the session counter (call at start of new conversation)."""
        self._data.setdefault("sessions", {}).pop(session_id, None)
        self._save()

    def status(self, session_id: Optional[str] = None) -> Dict:
        """Return current usage snapshot for UI / Token Panel."""
        return {
            "owner":   self.owner,
            "session": {
                "id":   session_id,
                "used": self._session_used(session_id) if session_id else 0,
                "soft": self._session_soft,
                "hard": self._session_hard,
            },
            "daily":  {
                "used": self._daily_used(),
                "soft": self._daily_soft,
                "hard": self._daily_hard,
            },
            "models": {
                m: self._model_used(m)
                for m in self._data.get("model", {})
            },
        }

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------

    def _gc_daily(self) -> None:
        """Remove daily ledger entries older than 25 hours."""
        cutoff = time.time() - (_SECS_PER_DAY + 3_600)
        day = self._data.get("daily", {})
        stale = [k for k, v in day.items() if isinstance(v, dict) and v.get("ts", 0) < cutoff]
        for k in stale:
            del day[k]
