"""MetricAggregator — Rolling metric windows and percentile stats (C41).

Consumes TelemetryEvent batches (as a registered sink) and maintains
rolling counters, histograms, and rates for dashboards / health checks.

Metrics tracked per-kind (and optionally per-owner):
  count         total events seen
  error_count   events with error != None
  p50/p95/p99   latency percentiles (duration_ms)
  rate_1m       events per minute over last 60 s
  rate_5m       events per minute over last 300 s

The aggregator stores raw latency samples in a fixed-size ring per kind
(default 1000) to compute percentiles without unbounded memory growth.

Public API:
  agg = MetricAggregator(window_s=300)
  agg.ingest(events)                  # sink-compatible
  agg.get(kind)                       # MetricSnapshot
  agg.get_all()                       # dict[kind -> MetricSnapshot]
  agg.owner_summary(owner)            # dict
  agg.reset()
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DEFAULT_WINDOW   = 300    # seconds for rate calculation
_LATENCY_RING     = 1_000  # max latency samples per kind
_RATE_BUCKETS_1M  = 60
_RATE_BUCKETS_5M  = 300


@dataclass
class MetricSnapshot:
    kind:         str
    count:        int
    error_count:  int
    p50:          Optional[float]   # ms
    p95:          Optional[float]
    p99:          Optional[float]
    rate_1m:      float             # events/min
    rate_5m:      float
    last_seen:    Optional[float]   # unix ts


class _KindState:
    def __init__(self):
        self.count       = 0
        self.error_count = 0
        self.latencies:  deque = deque(maxlen=_LATENCY_RING)
        # timestamp rings for rate calc
        self.ts_1m: deque = deque()   # events in last 60 s
        self.ts_5m: deque = deque()   # events in last 300 s
        self.last_seen: Optional[float] = None


class MetricAggregator:
    """Rolling metric windows over TelemetryEvent streams."""

    def __init__(self, window_s: float = _DEFAULT_WINDOW):
        self._window  = window_s
        self._state:  Dict[str, _KindState] = defaultdict(_KindState)
        self._owners: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock    = threading.Lock()

    # ------------------------------------------------------------------
    # Sink interface
    # ------------------------------------------------------------------

    def ingest(self, events: List[Any]) -> None:
        """Process a batch of TelemetryEvents. Used as a TelemetryCollector sink."""
        now = time.time()
        with self._lock:
            for ev in events:
                kind  = getattr(ev, "kind", "unknown")
                owner = getattr(ev, "owner", None)
                dur   = getattr(ev, "duration_ms", None)
                err   = getattr(ev, "error", None)
                ts    = getattr(ev, "ts", now)

                s = self._state[kind]
                s.count      += 1
                s.last_seen   = ts
                if err:
                    s.error_count += 1
                if dur is not None:
                    s.latencies.append(float(dur))

                # Rate windows
                cutoff_1m = now - 60
                cutoff_5m = now - 300
                s.ts_1m.append(ts)
                s.ts_5m.append(ts)
                while s.ts_1m and s.ts_1m[0] < cutoff_1m:
                    s.ts_1m.popleft()
                while s.ts_5m and s.ts_5m[0] < cutoff_5m:
                    s.ts_5m.popleft()

                if owner:
                    self._owners[owner][kind] += 1

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, kind: str) -> Optional[MetricSnapshot]:
        with self._lock:
            if kind not in self._state:
                return None
            return self._snapshot(kind, self._state[kind])

    def get_all(self) -> Dict[str, MetricSnapshot]:
        with self._lock:
            return {k: self._snapshot(k, v) for k, v in self._state.items()}

    def owner_summary(self, owner: str) -> Dict:
        with self._lock:
            return dict(self._owners.get(owner, {}))

    def reset(self) -> None:
        with self._lock:
            self._state.clear()
            self._owners.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot(kind: str, s: _KindState) -> MetricSnapshot:
        lats = list(s.latencies)
        p50 = p95 = p99 = None
        if lats:
            lats_sorted = sorted(lats)
            p50 = _percentile(lats_sorted, 50)
            p95 = _percentile(lats_sorted, 95)
            p99 = _percentile(lats_sorted, 99)
        rate_1m = len(s.ts_1m) / 1.0     # per minute (already filtered to 60 s)
        rate_5m = len(s.ts_5m) / 5.0     # per minute (filtered to 300 s / 5 min)
        return MetricSnapshot(
            kind=kind,
            count=s.count,
            error_count=s.error_count,
            p50=round(p50, 2) if p50 is not None else None,
            p95=round(p95, 2) if p95 is not None else None,
            p99=round(p99, 2) if p99 is not None else None,
            rate_1m=round(rate_1m, 3),
            rate_5m=round(rate_5m, 3),
            last_seen=s.last_seen,
        )


def _percentile(sorted_data: list, pct: float) -> float:
    if not sorted_data:
        return 0.0
    idx = (pct / 100) * (len(sorted_data) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (idx - lo)
