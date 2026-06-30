"""TraceLogger — Structured per-request distributed trace recorder (C42).

Builds lightweight OpenTelemetry-compatible spans without requiring the
full OTel SDK.  Each agent request becomes a root Trace containing child
Spans for each sub-step (LLM call, tool call, skill load, memory read, …).

Spans are stored in memory (ring-buffer, configurable size) and can be:
  - Queried by trace_id or session_id
  - Exported as OTLP-style JSON for forwarding to Jaeger / Zipkin
  - Used as a TelemetryCollector sink (each event auto-creates a span)

Public API:
  tl = TraceLogger.get()                         # singleton
  trace_id = tl.start_trace(name, *, owner, session_id, attrs)
  span_id  = tl.start_span(trace_id, name, *, parent_span_id, attrs)
  tl.end_span(span_id, *, error, attrs)
  tl.end_trace(trace_id, *, error)
  tl.get_trace(trace_id)                         # Trace | None
  tl.spans_for_session(session_id)               # list[Span]
  tl.export_trace(trace_id)                      # dict (OTLP-style)
  tl.ingest(events)                              # sink for TelemetryCollector
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_DEFAULT_TRACE_RING = 2_048
_DEFAULT_SPAN_RING  = 16_384

# Map TelemetryEvent kinds to span operation names
_KIND_OP = {
    "llm_call":      "llm.call",
    "tool_call":     "tool.call",
    "skill_load":    "skill.load",
    "skill_miss":    "skill.miss",
    "session_start": "session.start",
    "session_end":   "session.end",
    "memory_write":  "memory.write",
    "memory_read":   "memory.read",
    "reflection":    "reflection",
    "improvement":   "improvement",
    "audit":         "audit",
    "error":         "error",
}


@dataclass
class Span:
    span_id:       str
    trace_id:      str
    parent_id:     Optional[str]
    name:          str
    start_ts:      float
    end_ts:        Optional[float] = None
    duration_ms:   Optional[float] = None
    status:        str = "OK"         # "OK" | "ERROR"
    error:         Optional[str] = None
    attrs:         Dict[str, Any] = field(default_factory=dict)

    def finish(self, *, error: Optional[str] = None, attrs: Optional[Dict] = None) -> None:
        self.end_ts     = time.time()
        self.duration_ms = round((self.end_ts - self.start_ts) * 1000, 3)
        if error:
            self.status = "ERROR"
            self.error  = error
        if attrs:
            self.attrs.update(attrs)


@dataclass
class Trace:
    trace_id:   str
    name:       str
    owner:      Optional[str]
    session_id: Optional[str]
    start_ts:   float
    end_ts:     Optional[float] = None
    status:     str = "OK"
    error:      Optional[str] = None
    attrs:      Dict[str, Any] = field(default_factory=dict)
    spans:      List[Span] = field(default_factory=list)

    def duration_ms(self) -> Optional[float]:
        if self.end_ts:
            return round((self.end_ts - self.start_ts) * 1000, 3)
        return None


class TraceLogger:
    """Singleton distributed trace recorder."""

    _instance: Optional[TraceLogger] = None
    _lock = threading.Lock()

    def __init__(
        self,
        trace_ring: int = _DEFAULT_TRACE_RING,
        span_ring:  int = _DEFAULT_SPAN_RING,
    ):
        self._traces: OrderedDict[str, Trace] = OrderedDict()
        self._spans:  OrderedDict[str, Span]  = OrderedDict()
        self._trace_max = trace_ring
        self._span_max  = span_ring
        # session_id -> list of span_ids
        self._session_index: Dict[str, List[str]] = {}
        self._rlock = threading.Lock()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> TraceLogger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Trace lifecycle
    # ------------------------------------------------------------------

    def start_trace(
        self,
        name: str,
        *,
        owner:      Optional[str] = None,
        session_id: Optional[str] = None,
        attrs:      Optional[Dict] = None,
    ) -> str:
        trace_id = uuid.uuid4().hex[:16]
        trace = Trace(
            trace_id=trace_id,
            name=name,
            owner=owner,
            session_id=session_id,
            start_ts=time.time(),
            attrs=attrs or {},
        )
        with self._rlock:
            self._traces[trace_id] = trace
            if len(self._traces) > self._trace_max:
                self._traces.popitem(last=False)
        return trace_id

    def end_trace(
        self,
        trace_id: str,
        *,
        error: Optional[str] = None,
        attrs: Optional[Dict] = None,
    ) -> bool:
        with self._rlock:
            t = self._traces.get(trace_id)
        if not t:
            return False
        t.end_ts = time.time()
        if error:
            t.status = "ERROR"
            t.error  = error
        if attrs:
            t.attrs.update(attrs)
        return True

    # ------------------------------------------------------------------
    # Span lifecycle
    # ------------------------------------------------------------------

    def start_span(
        self,
        trace_id: str,
        name: str,
        *,
        parent_span_id: Optional[str] = None,
        attrs:          Optional[Dict] = None,
    ) -> str:
        span_id = uuid.uuid4().hex[:12]
        span = Span(
            span_id=span_id,
            trace_id=trace_id,
            parent_id=parent_span_id,
            name=name,
            start_ts=time.time(),
            attrs=attrs or {},
        )
        with self._rlock:
            self._spans[span_id] = span
            if len(self._spans) > self._span_max:
                self._spans.popitem(last=False)
            t = self._traces.get(trace_id)
            if t:
                t.spans.append(span)
            # session index
            if t and t.session_id:
                self._session_index.setdefault(t.session_id, []).append(span_id)
        return span_id

    def end_span(
        self,
        span_id: str,
        *,
        error: Optional[str] = None,
        attrs: Optional[Dict] = None,
    ) -> bool:
        with self._rlock:
            span = self._spans.get(span_id)
        if not span:
            return False
        span.finish(error=error, attrs=attrs)
        return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> Optional[Trace]:
        return self._traces.get(trace_id)

    def spans_for_session(self, session_id: str) -> List[Span]:
        with self._rlock:
            ids = list(self._session_index.get(session_id, []))
        return [self._spans[i] for i in ids if i in self._spans]

    # ------------------------------------------------------------------
    # OTLP-style export
    # ------------------------------------------------------------------

    def export_trace(self, trace_id: str) -> Optional[Dict]:
        t = self.get_trace(trace_id)
        if not t:
            return None
        return {
            "traceId":   t.trace_id,
            "name":      t.name,
            "owner":     t.owner,
            "sessionId": t.session_id,
            "startTs":   t.start_ts,
            "endTs":     t.end_ts,
            "durationMs": t.duration_ms(),
            "status":    t.status,
            "error":     t.error,
            "attrs":     t.attrs,
            "spans": [
                {
                    "spanId":     s.span_id,
                    "parentId":   s.parent_id,
                    "name":       s.name,
                    "startTs":    s.start_ts,
                    "endTs":      s.end_ts,
                    "durationMs": s.duration_ms,
                    "status":     s.status,
                    "error":      s.error,
                    "attrs":      s.attrs,
                }
                for s in t.spans
            ],
        }

    # ------------------------------------------------------------------
    # TelemetryCollector sink
    # ------------------------------------------------------------------

    def ingest(self, events: List[Any]) -> None:
        """Auto-create single-span traces from TelemetryEvents."""
        for ev in events:
            kind     = getattr(ev, "kind", "unknown")
            op       = _KIND_OP.get(kind, kind)
            sid      = getattr(ev, "session_id", None)
            owner    = getattr(ev, "owner", None)
            dur      = getattr(ev, "duration_ms", None)
            err      = getattr(ev, "error", None)
            data     = getattr(ev, "data", {})

            # Reuse existing trace for this session if available, else create one
            trace_id = None
            if sid:
                with self._rlock:
                    for tid, t in reversed(list(self._traces.items())):
                        if t.session_id == sid and t.end_ts is None:
                            trace_id = tid
                            break
            if not trace_id:
                trace_id = self.start_trace(op, owner=owner, session_id=sid)

            span_attrs = {"event_id": getattr(ev, "id", ""), **data}
            if dur is not None:
                span_attrs["duration_ms"] = dur

            span_id = self.start_span(trace_id, op, attrs=span_attrs)
            self.end_span(span_id, error=err)
