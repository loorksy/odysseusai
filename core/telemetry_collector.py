"""TelemetryCollector — Low-overhead event ingestion pipeline (C40).

Every significant agent action (LLM call, tool call, skill load, session
start/end, error) emits a TelemetryEvent here.  The collector:
  - Buffers events in a bounded in-memory ring
  - Flushes to registered sinks (MetricAggregator, TraceLogger, remote)
    either on-demand or when the buffer reaches flush_threshold
  - Runs a background flush loop at configurable interval
  - Enforces per-owner sampling rates to cap volume in high-traffic deployments
  - Never raises — all sink errors are swallowed and counted

Event taxonomy (kind strings):
  llm_call      tool_call     skill_load    skill_miss
  session_start session_end   error         audit
  reflection    improvement   memory_write  memory_read

Public API:
  col = TelemetryCollector.get()          # singleton
  col.emit(kind, data, *, owner, session_id, duration_ms, error)
  col.flush()                             # drain buffer to sinks
  col.register_sink(sink_fn)             # sink_fn(events: list) -> None
  col.stats()                            # dict
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BUFFER    = 4_096
_DEFAULT_THRESHOLD = 256
_DEFAULT_INTERVAL  = 10.0    # seconds


@dataclass
class TelemetryEvent:
    id:           str
    kind:         str
    data:         Dict[str, Any]
    owner:        Optional[str]
    session_id:   Optional[str]
    duration_ms:  Optional[float]
    error:        Optional[str]
    ts:           float = field(default_factory=time.time)


class TelemetryCollector:
    """Singleton ingest pipeline for all agent telemetry events."""

    _instance: Optional[TelemetryCollector] = None
    _lock = threading.Lock()

    def __init__(
        self,
        buffer_size:     int   = _DEFAULT_BUFFER,
        flush_threshold: int   = _DEFAULT_THRESHOLD,
        flush_interval:  float = _DEFAULT_INTERVAL,
    ):
        self._buffer:    deque           = deque(maxlen=buffer_size)
        self._threshold: int             = flush_threshold
        self._interval:  float           = flush_interval
        self._sinks:     List[Callable]  = []
        self._sampling:  Dict[str, float] = {}   # owner -> rate 0.0-1.0
        self._dropped    = 0
        self._flushed    = 0
        self._errors     = 0
        self._rlock      = threading.Lock()
        self._timer:     Optional[threading.Timer] = None
        self._start_ts   = time.time()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> TelemetryCollector:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._schedule()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            if cls._instance:
                cls._instance._cancel_timer()
            cls._instance = None

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    def emit(
        self,
        kind: str,
        data: Optional[Dict] = None,
        *,
        owner:       Optional[str]   = None,
        session_id:  Optional[str]   = None,
        duration_ms: Optional[float] = None,
        error:       Optional[str]   = None,
    ) -> None:
        """Ingest one telemetry event.  Never raises."""
        try:
            if owner and owner in self._sampling:
                import random
                if random.random() > self._sampling[owner]:
                    with self._rlock:
                        self._dropped += 1
                    return
            event = TelemetryEvent(
                id=str(uuid.uuid4())[:8],
                kind=kind,
                data=data or {},
                owner=owner,
                session_id=session_id,
                duration_ms=duration_ms,
                error=error,
            )
            with self._rlock:
                self._buffer.append(event)
                buf_len = len(self._buffer)
            if buf_len >= self._threshold:
                self._flush_sync()
        except Exception as e:
            logger.debug(f"TelemetryCollector.emit failed: {e}")

    # ------------------------------------------------------------------
    # Sinks
    # ------------------------------------------------------------------

    def register_sink(self, sink_fn: Callable[[List[TelemetryEvent]], None]) -> None:
        with self._rlock:
            self._sinks.append(sink_fn)

    def set_sampling_rate(self, owner: str, rate: float) -> None:
        """Sample rate 0.0 (drop all) to 1.0 (keep all) for owner."""
        self._sampling[owner] = max(0.0, min(1.0, rate))

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> int:
        """Public flush — drains buffer to all sinks.  Returns event count."""
        return self._flush_sync()

    def _flush_sync(self) -> int:
        with self._rlock:
            if not self._buffer:
                return 0
            batch = list(self._buffer)
            self._buffer.clear()
            sinks = list(self._sinks)

        for sink in sinks:
            try:
                sink(batch)
            except Exception as e:
                logger.warning(f"TelemetryCollector sink error: {e}")
                with self._rlock:
                    self._errors += 1

        with self._rlock:
            self._flushed += len(batch)
        return len(batch)

    # ------------------------------------------------------------------
    # Background timer
    # ------------------------------------------------------------------

    def _schedule(self) -> None:
        self._timer = threading.Timer(self._interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        try:
            self._flush_sync()
        finally:
            self._schedule()

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict:
        with self._rlock:
            return {
                "buffered":  len(self._buffer),
                "flushed":   self._flushed,
                "dropped":   self._dropped,
                "sink_errors": self._errors,
                "sinks":     len(self._sinks),
                "uptime_s":  round(time.time() - self._start_ts, 1),
            }
