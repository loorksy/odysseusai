"""HealthChecker — Component health probes and aggregated status (C61).

Registers named health probes (callables) and runs them on demand
or on a background schedule.  Aggregates results into an overall
system status: healthy / degraded / unhealthy.

Probe contract:
  def my_probe() -> HealthResult | bool | str
  - Return HealthResult for full detail
  - Return True / False for simple pass/fail
  - Return a string message to surface as a warning

Public API:
  hc = HealthChecker()
  hc.register(name, probe_fn, *, critical, timeout_s, tags)
  result = hc.check(name)          -> HealthResult
  report = hc.check_all()          -> HealthReport
  hc.start_background(interval_s)  # poll in daemon thread
  hc.stop_background()
  hc.last_report()  -> HealthReport | None
"""
from __future__ import annotations
import logging, threading, time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATUS_HEALTHY   = "healthy"
_STATUS_DEGRADED  = "degraded"
_STATUS_UNHEALTHY = "unhealthy"


@dataclass
class HealthResult:
    name:     str
    status:   str          # healthy | degraded | unhealthy
    message:  str = ""
    duration_ms: float = 0.0
    checked_at:  float = field(default_factory=time.time)
    tags:     List[str] = field(default_factory=list)
    detail:   Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    status:   str
    checked_at: float
    results:  List[HealthResult] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "status":     self.status,
            "checked_at": self.checked_at,
            "components": [
                {"name": r.name, "status": r.status,
                 "message": r.message, "duration_ms": round(r.duration_ms, 2),
                 "tags": r.tags}
                for r in self.results
            ],
        }


@dataclass
class _ProbeEntry:
    name:      str
    fn:        Callable
    critical:  bool
    timeout_s: float
    tags:      List[str]


class HealthChecker:
    """Aggregated health probe runner."""

    def __init__(self):
        self._probes: Dict[str, _ProbeEntry] = {}
        self._last_report: Optional[HealthReport] = None
        self._lock   = threading.Lock()
        self._bg_thread: Optional[threading.Thread] = None
        self._stop   = threading.Event()

    def register(
        self,
        name:      str,
        probe_fn:  Callable,
        *,
        critical:  bool  = True,
        timeout_s: float = 5.0,
        tags:      Optional[List[str]] = None,
    ) -> None:
        self._probes[name] = _ProbeEntry(
            name=name, fn=probe_fn, critical=critical,
            timeout_s=timeout_s, tags=tags or [],
        )

    def check(self, name: str) -> HealthResult:
        entry = self._probes.get(name)
        if not entry:
            return HealthResult(name=name, status=_STATUS_UNHEALTHY,
                                message=f"probe '{name}' not registered")
        return self._run_probe(entry)

    def check_all(self) -> HealthReport:
        results = [self._run_probe(e) for e in self._probes.values()]
        overall = self._aggregate(results)
        report  = HealthReport(status=overall, checked_at=time.time(), results=results)
        with self._lock:
            self._last_report = report
        return report

    def last_report(self) -> Optional[HealthReport]:
        return self._last_report

    def start_background(self, interval_s: float = 30.0) -> None:
        self._stop.clear()
        self._bg_thread = threading.Thread(
            target=self._bg_loop, args=(interval_s,), daemon=True
        )
        self._bg_thread.start()

    def stop_background(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_probe(self, entry: _ProbeEntry) -> HealthResult:
        start = time.time()
        result_holder = [None]
        exc_holder    = [None]

        def _run():
            try:
                result_holder[0] = entry.fn()
            except Exception as e:
                exc_holder[0] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=entry.timeout_s)
        duration_ms = (time.time() - start) * 1000

        if t.is_alive():
            return HealthResult(
                name=entry.name, status=_STATUS_UNHEALTHY,
                message=f"probe timed out after {entry.timeout_s}s",
                duration_ms=duration_ms, tags=entry.tags,
            )
        if exc_holder[0]:
            return HealthResult(
                name=entry.name, status=_STATUS_UNHEALTHY,
                message=str(exc_holder[0]),
                duration_ms=duration_ms, tags=entry.tags,
            )

        raw = result_holder[0]
        if isinstance(raw, HealthResult):
            raw.duration_ms = duration_ms
            return raw
        if raw is True or raw is None:
            status, msg = _STATUS_HEALTHY, ""
        elif raw is False:
            status, msg = _STATUS_UNHEALTHY, "probe returned False"
        elif isinstance(raw, str):
            status, msg = _STATUS_DEGRADED, raw
        else:
            status, msg = _STATUS_HEALTHY, str(raw)

        return HealthResult(
            name=entry.name, status=status, message=msg,
            duration_ms=duration_ms, tags=entry.tags,
        )

    @staticmethod
    def _aggregate(results: List[HealthResult]) -> str:
        statuses = {r.status for r in results}
        if _STATUS_UNHEALTHY in statuses:
            return _STATUS_UNHEALTHY
        if _STATUS_DEGRADED in statuses:
            return _STATUS_DEGRADED
        return _STATUS_HEALTHY

    def _bg_loop(self, interval_s: float) -> None:
        while not self._stop.is_set():
            try:
                self.check_all()
            except Exception as e:
                logger.warning(f"HealthChecker background error: {e}")
            self._stop.wait(interval_s)
