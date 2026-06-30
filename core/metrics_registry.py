"""MetricsRegistry — In-process metrics: counters, gauges, histograms (C62).

Lightweight Prometheus-compatible metrics store that can be scraped
by an exporter or read directly.  No external dependencies required;
optional prometheus_client integration when available.

Metric types:
  Counter   : monotonically increasing value (requests, errors)
  Gauge     : point-in-time value (active connections, queue depth)
  Histogram : distribution of observed values (latencies, payload sizes)

Public API:
  reg = MetricsRegistry(namespace="sr")
  reg.counter(name, *, labels, help)    -> Counter
  reg.gauge(name, *, labels, help)      -> Gauge
  reg.histogram(name, buckets, *, help) -> Histogram
  reg.snapshot()  -> dict
  reg.prometheus_text()  -> str   (Prometheus exposition format)
"""
from __future__ import annotations
import math, threading, time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

class Counter:
    def __init__(self, name: str, help: str = "", labels: Tuple = ()):
        self.name   = name
        self.help   = help
        self._labels = labels
        self._values: Dict[Tuple, float] = defaultdict(float)
        self._lock  = threading.Lock()

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = tuple(label_values.get(l, "") for l in self._labels)
        with self._lock:
            self._values[key] += amount

    def get(self, **label_values) -> float:
        key = tuple(label_values.get(l, "") for l in self._labels)
        return self._values.get(key, 0.0)

    def reset(self, **label_values) -> None:
        key = tuple(label_values.get(l, "") for l in self._labels)
        with self._lock:
            self._values[key] = 0.0


class Gauge:
    def __init__(self, name: str, help: str = "", labels: Tuple = ()):
        self.name   = name
        self.help   = help
        self._labels = labels
        self._values: Dict[Tuple, float] = defaultdict(float)
        self._lock  = threading.Lock()

    def set(self, value: float, **label_values) -> None:
        key = tuple(label_values.get(l, "") for l in self._labels)
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = tuple(label_values.get(l, "") for l in self._labels)
        with self._lock:
            self._values[key] += amount

    def dec(self, amount: float = 1.0, **label_values) -> None:
        self.inc(-amount, **label_values)

    def get(self, **label_values) -> float:
        key = tuple(label_values.get(l, "") for l in self._labels)
        return self._values.get(key, 0.0)


class Histogram:
    _DEFAULT_BUCKETS = (.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, math.inf)

    def __init__(self, name: str, buckets: Optional[Tuple] = None, help: str = ""):
        self.name    = name
        self.help    = help
        self._buckets = tuple(sorted(buckets or self._DEFAULT_BUCKETS))
        self._counts: Dict[float, int] = {b: 0 for b in self._buckets}
        self._sum    = 0.0
        self._total  = 0
        self._lock   = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum   += value
            self._total += 1
            for b in self._buckets:
                if value <= b:
                    self._counts[b] += 1

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "buckets": dict(self._counts),
                "sum":     self._sum,
                "count":   self._total,
                "mean":    (self._sum / self._total) if self._total else 0.0,
            }

    def percentile(self, p: float) -> float:
        """Estimate p-th percentile (0-100) from bucket data."""
        if not self._total: return 0.0
        target = math.ceil(p / 100 * self._total)
        cumulative = 0
        prev_b = 0.0
        for b, count in sorted(self._counts.items()):
            cumulative += count
            if cumulative >= target:
                return b
            prev_b = b
        return prev_b


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class MetricsRegistry:
    """Central store for all application metrics."""

    def __init__(self, namespace: str = "sr"):
        self._ns       = namespace
        self._counters:   Dict[str, Counter]   = {}
        self._gauges:     Dict[str, Gauge]     = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def _full(self, name: str) -> str:
        return f"{self._ns}_{name}" if self._ns else name

    def counter(self, name: str, *, labels: Tuple = (), help: str = "") -> Counter:
        key = self._full(name)
        with self._lock:
            if key not in self._counters:
                self._counters[key] = Counter(key, help=help, labels=labels)
        return self._counters[key]

    def gauge(self, name: str, *, labels: Tuple = (), help: str = "") -> Gauge:
        key = self._full(name)
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = Gauge(key, help=help, labels=labels)
        return self._gauges[key]

    def histogram(self, name: str, buckets: Optional[Tuple] = None, *, help: str = "") -> Histogram:
        key = self._full(name)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = Histogram(key, buckets=buckets, help=help)
        return self._histograms[key]

    def snapshot(self) -> Dict:
        out: Dict = {"counters": {}, "gauges": {}, "histograms": {}}
        with self._lock:
            for k, c in self._counters.items():
                out["counters"][k] = dict(c._values)
            for k, g in self._gauges.items():
                out["gauges"][k] = dict(g._values)
            for k, h in self._histograms.items():
                out["histograms"][k] = h.snapshot()
        return out

    def prometheus_text(self) -> str:
        lines = []
        with self._lock:
            for k, c in self._counters.items():
                if c.help: lines.append(f"# HELP {k} {c.help}")
                lines.append(f"# TYPE {k} counter")
                for lv, val in c._values.items():
                    lines.append(f"{k} {val}")
            for k, g in self._gauges.items():
                if g.help: lines.append(f"# HELP {k} {g.help}")
                lines.append(f"# TYPE {k} gauge")
                for lv, val in g._values.items():
                    lines.append(f"{k} {val}")
            for k, h in self._histograms.items():
                if h.help: lines.append(f"# HELP {k} {h.help}")
                lines.append(f"# TYPE {k} histogram")
                snap = h.snapshot()
                for b, cnt in snap["buckets"].items():
                    le = "+Inf" if math.isinf(b) else str(b)
                    lines.append(f'{k}_bucket{{le="{le}"}} {cnt}')
                lines.append(f"{k}_sum {snap['sum']}")
                lines.append(f"{k}_count {snap['count']}")
        return "\n".join(lines) + "\n"
