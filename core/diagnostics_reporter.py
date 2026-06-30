"""DiagnosticsReporter — System-wide diagnostic snapshot and report (C63).

Aggregates data from HealthChecker, MetricsRegistry, and other
runtime sources into a single structured diagnostic report suitable
for:
  - Admin /diagnostics API endpoint
  - Support bundles (dump to JSON file)
  - Automated alerting thresholds

Report sections:
  system      : Python version, platform, pid, uptime, memory/CPU (psutil if available)
  health      : latest HealthChecker report
  metrics     : MetricsRegistry snapshot
  config      : ConfigLoader to_dict(redact_secrets=True)
  plugins     : PluginRegistry health()
  errors      : last N error log entries (via DiagnosticsReporter log handler)
  environment : EnvironmentManager.environment()

Public API:
  dr = DiagnosticsReporter(health_checker, metrics_registry, **optional_sources)
  report = dr.report()        -> dict
  dr.dump(path)               # write JSON to file
  dr.attach_log_handler()     # capture ERROR+ log lines
  dr.check_thresholds()       # return list of threshold violations
  dr.add_threshold(metric_path, operator, value, *, severity)
"""
from __future__ import annotations
import json, logging, os, platform, sys, threading, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Threshold:
    metric_path: str           # dot-path into metrics snapshot, e.g. "counters.sr_errors"
    operator:    str           # ">", ">=", "<", "<=", "==", "!="
    value:       float
    severity:    str = "warning"  # warning | critical
    message:     str = ""


class _ErrorLogHandler(logging.Handler):
    def __init__(self, max_entries: int = 200):
        super().__init__(level=logging.ERROR)
        self._entries: List[Dict] = []
        self._max = max_entries
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self._entries.append({
                "ts":      record.created,
                "level":   record.levelname,
                "logger":  record.name,
                "message": self.format(record),
            })
            if len(self._entries) > self._max:
                self._entries = self._entries[-self._max:]

    def entries(self) -> List[Dict]:
        with self._lock:
            return list(self._entries)


class DiagnosticsReporter:
    """Unified diagnostic snapshot aggregator."""

    def __init__(
        self,
        health_checker   = None,
        metrics_registry = None,
        *,
        config_loader    = None,
        plugin_registry  = None,
        env_manager      = None,
        start_time:      Optional[float] = None,
    ):
        self._health   = health_checker
        self._metrics  = metrics_registry
        self._cfg      = config_loader
        self._plugins  = plugin_registry
        self._env      = env_manager
        self._start    = start_time or time.time()
        self._thresholds: List[Threshold] = []
        self._log_handler: Optional[_ErrorLogHandler] = None

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self) -> Dict[str, Any]:
        return {
            "generated_at": time.time(),
            "system":       self._system_info(),
            "health":       self._health_section(),
            "metrics":      self._metrics_section(),
            "config":       self._config_section(),
            "plugins":      self._plugins_section(),
            "environment":  self._env_section(),
            "errors":       self._errors_section(),
            "thresholds":   self.check_thresholds(),
        }

    def dump(self, path: str = "diagnostics.json") -> str:
        report = self.report()
        Path(path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        logger.info(f"DiagnosticsReporter: dumped report to '{path}'")
        return path

    # ------------------------------------------------------------------
    # Log capture
    # ------------------------------------------------------------------

    def attach_log_handler(self, max_entries: int = 200) -> None:
        self._log_handler = _ErrorLogHandler(max_entries)
        logging.getLogger().addHandler(self._log_handler)

    # ------------------------------------------------------------------
    # Thresholds
    # ------------------------------------------------------------------

    def add_threshold(
        self,
        metric_path: str,
        operator:    str,
        value:       float,
        *,
        severity: str = "warning",
        message:  str = "",
    ) -> None:
        self._thresholds.append(Threshold(
            metric_path=metric_path, operator=operator,
            value=value, severity=severity, message=message,
        ))

    def check_thresholds(self) -> List[Dict]:
        if not self._metrics or not self._thresholds:
            return []
        snap       = self._metrics.snapshot()
        violations = []
        for t in self._thresholds:
            current = _dot_get(snap, t.metric_path)
            if current is None: continue
            try:
                current = float(current)
            except (TypeError, ValueError):
                continue
            ops = {">":  current >  t.value, ">=": current >= t.value,
                   "<":  current <  t.value, "<=": current <= t.value,
                   "==": current == t.value, "!=": current != t.value}
            if ops.get(t.operator, False):
                violations.append({
                    "metric":   t.metric_path,
                    "operator": t.operator,
                    "threshold": t.value,
                    "current":  current,
                    "severity": t.severity,
                    "message":  t.message or f"{t.metric_path} {t.operator} {t.value}",
                })
        return violations

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _system_info(self) -> Dict:
        info = {
            "python":   sys.version,
            "platform": platform.platform(),
            "pid":      os.getpid(),
            "uptime_s": round(time.time() - self._start, 1),
        }
        try:
            import psutil
            proc = psutil.Process()
            info["memory_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
            info["cpu_pct"]   = proc.cpu_percent(interval=0.1)
        except ImportError:
            pass
        return info

    def _health_section(self) -> Optional[Dict]:
        if not self._health: return None
        report = self._health.last_report() or self._health.check_all()
        return report.to_dict()

    def _metrics_section(self) -> Optional[Dict]:
        return self._metrics.snapshot() if self._metrics else None

    def _config_section(self) -> Optional[Dict]:
        return self._cfg.to_dict(redact_secrets=True) if self._cfg else None

    def _plugins_section(self) -> Optional[Dict]:
        return self._plugins.health() if self._plugins else None

    def _env_section(self) -> Optional[str]:
        return self._env.environment() if self._env else None

    def _errors_section(self) -> List[Dict]:
        return self._log_handler.entries() if self._log_handler else []


def _dot_get(d: Any, key: str) -> Any:
    parts = key.split(".")
    cur = d
    for p in parts:
        if not isinstance(cur, dict): return None
        cur = cur.get(p)
    return cur
