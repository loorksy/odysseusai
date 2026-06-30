"""
C105 — Telemetry Collector
Structured metrics, span tracing, and event logging for agent observability.
Supports counters, gauges, histograms, and distributed trace spans.
"""
from __future__ import annotations

import contextlib
import logging
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Span tracing                                                        #
# ------------------------------------------------------------------ #

@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    tags: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def duration(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def finish(self, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        if error:
            self.error = error


# ------------------------------------------------------------------ #
#  Metrics                                                             #
# ------------------------------------------------------------------ #

@dataclass
class MetricSample:
    name: str
    value: float
    tags: dict = field(default_factory=dict)
    recorded_at: float = field(default_factory=time.time)


class Telemetry:
    """
    Central telemetry collector.

    Usage::

        tel = Telemetry(service="shadow-agent")
        tel.increment("llm.calls")
        tel.gauge("memory.mb", 512)

        with tel.span("agent.run") as s:
            s.tags["model"] = "gpt-4o"
            result = agent.run(input)

        report = tel.report()
    """

    def __init__(self, service: str = "agent"):
        self.service = service
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._spans: list[Span] = []
        self._events: list[dict] = []
        self._max_spans: int = 2000
        self._max_events: int = 2000

    # ------------------------------------------------------------------ #
    #  Metrics API                                                         #
    # ------------------------------------------------------------------ #

    def increment(self, name: str, value: float = 1.0, **tags) -> None:
        self._counters[name] += value

    def decrement(self, name: str, value: float = 1.0, **tags) -> None:
        self._counters[name] -= value

    def gauge(self, name: str, value: float, **tags) -> None:
        self._gauges[name] = value

    def histogram(self, name: str, value: float, **tags) -> None:
        self._histograms[name].append(value)

    def timing(self, name: str, seconds: float, **tags) -> None:
        self.histogram(name, seconds)

    # ------------------------------------------------------------------ #
    #  Span tracing                                                        #
    # ------------------------------------------------------------------ #

    def start_span(
        self,
        name: str,
        trace_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        **tags,
    ) -> Span:
        span = Span(
            name=name,
            trace_id=trace_id or uuid.uuid4().hex[:12],
            parent_id=parent_id,
            tags=dict(tags),
        )
        return span

    @contextlib.contextmanager
    def span(self, name: str, trace_id: Optional[str] = None, **tags) -> Iterator[Span]:
        s = self.start_span(name, trace_id=trace_id, **tags)
        try:
            yield s
            s.finish()
        except Exception as e:
            s.finish(error=str(e))
            raise
        finally:
            self._record_span(s)

    def _record_span(self, span: Span) -> None:
        self._spans.append(span)
        if len(self._spans) > self._max_spans:
            self._spans = self._spans[-self._max_spans:]
        if span.duration is not None:
            self.histogram(f"span.{span.name}.duration", span.duration)
        if span.error:
            self.increment(f"span.{span.name}.errors")

    # ------------------------------------------------------------------ #
    #  Events                                                              #
    # ------------------------------------------------------------------ #

    def event(self, name: str, data: Any = None, **tags) -> None:
        self._events.append({
            "name": name,
            "data": data,
            "tags": tags,
            "timestamp": time.time(),
            "service": self.service,
        })
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]

    # ------------------------------------------------------------------ #
    #  Reporting                                                           #
    # ------------------------------------------------------------------ #

    def counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def histogram_stats(self, name: str) -> dict:
        samples = self._histograms.get(name, [])
        if not samples:
            return {}
        return {
            "count": len(samples),
            "min": min(samples),
            "max": max(samples),
            "mean": statistics.mean(samples),
            "median": statistics.median(samples),
            "p95": self._percentile(samples, 95),
            "p99": self._percentile(samples, 99),
        }

    def report(self) -> dict:
        return {
            "service": self.service,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {k: self.histogram_stats(k) for k in self._histograms},
            "span_count": len(self._spans),
            "event_count": len(self._events),
        }

    def recent_spans(self, limit: int = 20) -> list[Span]:
        return self._spans[-limit:]

    def recent_events(self, limit: int = 20) -> list[dict]:
        return self._events[-limit:]

    def reset(self) -> None:
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._spans.clear()
        self._events.clear()

    @staticmethod
    def _percentile(data: list[float], pct: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * pct / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]


# Module-level default telemetry instance
telemetry = Telemetry()
