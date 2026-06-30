# core/token_counter.py
# TokenCounter — tracks LLM token usage per session, per skill, per MCP call.
# Designed as a singleton stored on app.state so all routes share one instance.
#
# Usage:
#   counter = TokenCounter.get()          # from anywhere after app init
#   counter.record(session_id, tokens_in, tokens_out, model, source="skill:my_skill")
#   snapshot = counter.snapshot(session_id)
#   totals   = counter.totals()

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_instance: Optional["TokenCounter"] = None
_lock = threading.Lock()


@dataclass
class TokenEvent:
    ts: float
    session_id: str
    source: str          # e.g. "skill:web_search", "mcp:memory_server", "chat"
    model: str
    tokens_in: int
    tokens_out: int

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class SessionStats:
    session_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    events: List[TokenEvent] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out

    def by_source(self) -> Dict[str, int]:
        """Total tokens grouped by source label."""
        result: Dict[str, int] = defaultdict(int)
        for e in self.events:
            result[e.source] += e.total
        return dict(result)

    def by_model(self) -> Dict[str, int]:
        result: Dict[str, int] = defaultdict(int)
        for e in self.events:
            result[e.model] += e.total
        return dict(result)


class TokenCounter:
    """Thread-safe, in-process token usage tracker.

    Keeps a rolling window of the last `max_events` events globally and
    per-session stats. Cheap to record; snapshot is O(n) per session.
    """

    def __init__(self, max_events: int = 10_000):
        self._lock = threading.Lock()
        self._max_events = max_events
        self._events: List[TokenEvent] = []
        self._sessions: Dict[str, SessionStats] = {}
        # Aggregate lifetime counters
        self._total_in: int = 0
        self._total_out: int = 0

    # ------------------------------------------------------------------
    # Singleton factory
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> "TokenCounter":
        global _instance
        if _instance is None:
            with _lock:
                if _instance is None:
                    _instance = cls()
        return _instance

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        session_id: str,
        tokens_in: int,
        tokens_out: int,
        model: str = "unknown",
        source: str = "chat",
    ) -> TokenEvent:
        """Record a token usage event.

        Args:
            session_id: Conversation or agent session identifier.
            tokens_in:  Prompt tokens consumed.
            tokens_out: Completion tokens generated.
            model:      Model name (e.g. "gpt-4o", "claude-3-5-sonnet").
            source:     Human-readable label, e.g. "skill:web_search",
                        "mcp:memory_server", "chat", "compaction".
        Returns:
            The recorded TokenEvent.
        """
        event = TokenEvent(
            ts=time.time(),
            session_id=session_id,
            source=source,
            model=model,
            tokens_in=max(0, tokens_in),
            tokens_out=max(0, tokens_out),
        )
        with self._lock:
            # Rolling global event buffer
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

            # Per-session accumulation
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionStats(session_id=session_id)
            sess = self._sessions[session_id]
            sess.tokens_in += event.tokens_in
            sess.tokens_out += event.tokens_out
            sess.events.append(event)

            # Lifetime totals
            self._total_in += event.tokens_in
            self._total_out += event.tokens_out

        return event

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def snapshot(self, session_id: str) -> Optional[SessionStats]:
        """Return a copy of stats for one session, or None if unknown."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            return SessionStats(
                session_id=sess.session_id,
                tokens_in=sess.tokens_in,
                tokens_out=sess.tokens_out,
                events=list(sess.events),
            )

    def totals(self) -> dict:
        """Return lifetime aggregate stats."""
        with self._lock:
            return {
                "tokens_in": self._total_in,
                "tokens_out": self._total_out,
                "total": self._total_in + self._total_out,
                "sessions": len(self._sessions),
                "events_buffered": len(self._events),
            }

    def session_ids(self) -> list:
        with self._lock:
            return list(self._sessions.keys())

    def reset_session(self, session_id: str) -> None:
        """Clear stats for one session (e.g. on context compaction)."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def reset_all(self) -> None:
        """Clear all stats. Useful for testing."""
        with self._lock:
            self._events.clear()
            self._sessions.clear()
            self._total_in = 0
            self._total_out = 0
