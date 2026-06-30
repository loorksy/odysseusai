"""QuotaManager — Per-owner resource quota tracking and enforcement (C44).

Manages longer-horizon quotas (daily, monthly, per-session) on top of the
short-horizon RateLimiter.  Typical quotas:
  tokens_in / tokens_out   LLM token consumption
  llm_calls                number of LLM requests
  tool_calls               MCP tool invocations
  storage_bytes            file/memory storage

Quota state is persisted in a lightweight SQLite file (or in-memory dict
if no db_path given).  On startup the manager loads current usage;
updates are batched and written every flush_interval seconds.

Public API:
  qm = QuotaManager(db_path=None)          # None = in-memory only
  qm.set_quota(owner, resource, limit, *, period)   # configure
  qm.consume(owner, resource, amount)      # QuotaResult
  qm.get_usage(owner, resource)            # QuotaUsage
  qm.reset(owner, resource)               # clear usage
  qm.summary(owner)                       # dict
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

_PERIOD_SECONDS = {
    "minutely": 60,
    "hourly":   3_600,
    "daily":    86_400,
    "monthly":  2_592_000,   # 30 days
    "session":  0,           # reset on explicit call only
}


@dataclass
class QuotaResult:
    allowed:   bool
    used:      float
    limit:     float
    remaining: float
    resets_at: Optional[float]   # unix ts
    resource:  str
    owner:     str


@dataclass
class QuotaUsage:
    owner:     str
    resource:  str
    used:      float
    limit:     float
    period:    str
    window_start: float
    resets_at: Optional[float]


@dataclass
class _QuotaDef:
    limit:  float
    period: str   # key in _PERIOD_SECONDS


@dataclass
class _UsageState:
    used:         float = 0.0
    window_start: float = field(default_factory=time.time)

    def reset(self) -> None:
        self.used         = 0.0
        self.window_start = time.time()


class QuotaManager:
    """Per-owner resource quota tracker with optional SQLite persistence."""

    def __init__(self, db_path: Optional[str] = None, flush_interval: float = 30.0):
        # (owner, resource) -> QuotaDef
        self._defs:   Dict[Tuple, _QuotaDef]  = {}
        # (owner, resource) -> UsageState
        self._usage:  Dict[Tuple, _UsageState] = {}
        self._lock    = threading.Lock()
        self._db_path = db_path
        self._dirty:  set = set()
        self._flush_interval = flush_interval
        self._timer:  Optional[threading.Timer] = None
        if db_path:
            self._init_db()
            self._load_db()
            self._schedule()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_quota(
        self,
        owner:    str,
        resource: str,
        limit:    float,
        *,
        period: str = "daily",
    ) -> None:
        if period not in _PERIOD_SECONDS:
            raise ValueError(f"Unknown period '{period}'. Choose from {list(_PERIOD_SECONDS)}.")
        key = (owner, resource)
        with self._lock:
            self._defs[key] = _QuotaDef(limit=limit, period=period)
            if key not in self._usage:
                self._usage[key] = _UsageState()

    def remove_quota(self, owner: str, resource: str) -> None:
        key = (owner, resource)
        with self._lock:
            self._defs.pop(key, None)
            self._usage.pop(key, None)

    # ------------------------------------------------------------------
    # Consume / query
    # ------------------------------------------------------------------

    def consume(
        self,
        owner:    str,
        resource: str,
        amount:   float = 1.0,
    ) -> QuotaResult:
        key = (owner, resource)
        with self._lock:
            defn = self._defs.get(key)
            if defn is None:
                return QuotaResult(allowed=True, used=0, limit=float("inf"),
                                   remaining=float("inf"), resets_at=None,
                                   resource=resource, owner=owner)
            state = self._usage.setdefault(key, _UsageState())
            self._maybe_reset(state, defn)
            if state.used + amount > defn.limit:
                remaining = max(0.0, defn.limit - state.used)
                return QuotaResult(
                    allowed=False, used=state.used, limit=defn.limit,
                    remaining=remaining,
                    resets_at=self._resets_at(state, defn),
                    resource=resource, owner=owner,
                )
            state.used += amount
            self._dirty.add(key)
            return QuotaResult(
                allowed=True, used=state.used, limit=defn.limit,
                remaining=defn.limit - state.used,
                resets_at=self._resets_at(state, defn),
                resource=resource, owner=owner,
            )

    def get_usage(self, owner: str, resource: str) -> Optional[QuotaUsage]:
        key = (owner, resource)
        with self._lock:
            defn  = self._defs.get(key)
            state = self._usage.get(key)
        if not defn or not state:
            return None
        return QuotaUsage(
            owner=owner, resource=resource,
            used=state.used, limit=defn.limit,
            period=defn.period,
            window_start=state.window_start,
            resets_at=self._resets_at(state, defn),
        )

    def reset(self, owner: str, resource: str) -> None:
        key = (owner, resource)
        with self._lock:
            state = self._usage.get(key)
            if state:
                state.reset()
                self._dirty.add(key)

    def summary(self, owner: str) -> Dict:
        with self._lock:
            keys = [(o, r) for (o, r) in self._defs if o == owner]
        return {
            r: {
                "used":      self._usage.get((owner, r), _UsageState()).used,
                "limit":     self._defs[(owner, r)].limit,
                "period":    self._defs[(owner, r)].period,
                "remaining": max(0, self._defs[(owner, r)].limit
                                  - self._usage.get((owner, r), _UsageState()).used),
            }
            for (_, r) in keys
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_reset(state: _UsageState, defn: _QuotaDef) -> None:
        period_s = _PERIOD_SECONDS.get(defn.period, 0)
        if period_s and time.time() - state.window_start >= period_s:
            state.reset()

    @staticmethod
    def _resets_at(state: _UsageState, defn: _QuotaDef) -> Optional[float]:
        period_s = _PERIOD_SECONDS.get(defn.period, 0)
        return (state.window_start + period_s) if period_s else None

    # SQLite persistence (lightweight; skipped in memory-only mode)
    def _init_db(self) -> None:
        import sqlite3
        with sqlite3.connect(self._db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS quota_usage (
                    owner TEXT, resource TEXT, used REAL, window_start REAL,
                    PRIMARY KEY (owner, resource)
                )
            """)

    def _load_db(self) -> None:
        import sqlite3
        with sqlite3.connect(self._db_path) as con:
            for row in con.execute("SELECT owner, resource, used, window_start FROM quota_usage"):
                key = (row[0], row[1])
                self._usage[key] = _UsageState(used=row[2], window_start=row[3])

    def _flush_db(self) -> None:
        if not self._db_path or not self._dirty:
            return
        import sqlite3
        with self._lock:
            dirty = set(self._dirty)
            self._dirty.clear()
        with sqlite3.connect(self._db_path) as con:
            for key in dirty:
                state = self._usage.get(key)
                if state:
                    con.execute(
                        "INSERT OR REPLACE INTO quota_usage VALUES (?,?,?,?)",
                        (key[0], key[1], state.used, state.window_start),
                    )

    def _schedule(self) -> None:
        self._timer = threading.Timer(self._flush_interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        try:
            self._flush_db()
        finally:
            self._schedule()
