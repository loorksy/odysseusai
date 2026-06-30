"""
C96 · PipelineScheduler
========================
Schedules and runs PipelineDefinition objects via DataTransformer.

Schedule types
--------------
* ONE_SHOT  — run once at a specified datetime (or immediately).
* INTERVAL  — run every N seconds/minutes/hours.
* CRON      — run on a cron expression (minute hour dom month dow).

Features
--------
* ScheduleEntry wraps a pipeline with its trigger config + run history.
* PipelineScheduler runs a background thread; tick() drives execution.
* Max-runs cap, jitter support, on_complete / on_error async callbacks.
* Payload factory callable per schedule — fresh data each run.
* Missed-run detection for INTERVAL schedules.
* Full dict serialisation for persistence / audit.
* stdlib-only (threading + sched + re); no external deps.

Usage
-----
    from core.pipeline_scheduler import PipelineScheduler, ScheduleType
    from core.data_transformer   import DataTransformer
    from core.pipeline_builder   import PipelineBuilder

    pipeline = PipelineBuilder("hourly-sync").extract("src").load("dst").build()

    scheduler = PipelineScheduler(DataTransformer())
    scheduler.add(
        pipeline,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
        max_runs=24,
        on_complete=my_callback,
    )
    scheduler.start()   # background thread
    # ...
    scheduler.stop()
"""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from core.data_transformer import DataTransformer, StepResult
    from core.pipeline_builder import PipelineDefinition
except ImportError:  # pragma: no cover
    DataTransformer    = Any  # type: ignore
    PipelineDefinition = Any  # type: ignore
    StepResult         = Any  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ScheduleType(str, Enum):
    ONE_SHOT = "one_shot"   # run once
    INTERVAL = "interval"   # every N seconds
    CRON     = "cron"       # cron expression


class ScheduleStatus(str, Enum):
    PENDING  = "pending"    # not yet started
    ACTIVE   = "active"     # scheduled and running
    PAUSED   = "paused"     # temporarily suspended
    DONE     = "done"       # max_runs reached or one-shot complete
    ERROR    = "error"      # permanently failed


# ---------------------------------------------------------------------------
# RunRecord
# ---------------------------------------------------------------------------

@dataclass
class RunRecord:
    """Outcome of a single scheduled execution."""
    run_id:        str
    schedule_id:   str
    pipeline_name: str
    started_at:    str          # ISO-8601
    finished_at:   str          # ISO-8601
    success:       bool
    step_count:    int
    error:         Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id":        self.run_id,
            "schedule_id":   self.schedule_id,
            "pipeline_name": self.pipeline_name,
            "started_at":    self.started_at,
            "finished_at":   self.finished_at,
            "success":       self.success,
            "step_count":    self.step_count,
            "error":         self.error,
        }


# ---------------------------------------------------------------------------
# ScheduleEntry
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    """
    Associates a PipelineDefinition with a trigger configuration.

    Parameters
    ----------
    schedule_id     : unique identifier
    pipeline        : PipelineDefinition to execute
    schedule_type   : ONE_SHOT | INTERVAL | CRON
    status          : ScheduleStatus
    interval_seconds: gap between INTERVAL runs
    cron_expression : 5-field cron string for CRON runs
    run_at          : explicit UTC datetime for ONE_SHOT
    max_runs        : 0 = unlimited
    jitter_seconds  : random delay added before each run (0 = none)
    payload_factory : callable() → dict — fresh payload per run
    on_complete     : async-friendly callback(run_record)
    on_error        : async-friendly callback(run_record)
    tags            : free-form labels
    run_count       : how many times this schedule has executed
    next_run_at     : UTC timestamp of next planned execution
    history         : last N RunRecords (capped at max_history)
    """
    schedule_id:      str
    pipeline:         Any                           # PipelineDefinition
    schedule_type:    ScheduleType
    status:           ScheduleStatus                = ScheduleStatus.PENDING
    interval_seconds: int                           = 0
    cron_expression:  str                           = ""
    run_at:           Optional[datetime]            = None
    max_runs:         int                           = 0
    jitter_seconds:   int                           = 0
    payload_factory:  Optional[Callable[[], Dict]] = None
    on_complete:      Optional[Callable]            = None
    on_error:         Optional[Callable]            = None
    tags:             List[str]                     = field(default_factory=list)
    run_count:        int                           = 0
    next_run_at:      Optional[float]               = None   # unix timestamp
    history:          List[RunRecord]               = field(default_factory=list)
    max_history:      int                           = 50

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id":      self.schedule_id,
            "pipeline_id":      getattr(self.pipeline, "pipeline_id", None),
            "pipeline_name":    getattr(self.pipeline, "name", str(self.pipeline)),
            "schedule_type":    self.schedule_type.value,
            "status":           self.status.value,
            "interval_seconds": self.interval_seconds,
            "cron_expression":  self.cron_expression,
            "run_at":           self.run_at.isoformat() if self.run_at else None,
            "max_runs":         self.max_runs,
            "jitter_seconds":   self.jitter_seconds,
            "tags":             list(self.tags),
            "run_count":        self.run_count,
            "next_run_at":      self.next_run_at,
            "history":          [r.to_dict() for r in self.history],
        }

    def _append_history(self, record: RunRecord) -> None:
        self.history.append(record)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]


# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------

_CRON_RANGES = {
    "minute": (0, 59),
    "hour":   (0, 23),
    "dom":    (1, 31),
    "month":  (1, 12),
    "dow":    (0,  6),
}


def _cron_field_matches(expr: str, value: int, lo: int, hi: int) -> bool:
    """Return True if *value* satisfies the cron field *expr*."""
    if expr == "*":
        return True
    for part in expr.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step_int = int(step)
            start = lo if base == "*" else int(base.split("-")[0])
            end   = hi if base == "*" or "-" not in base else int(base.split("-")[1])
            if start <= value <= end and (value - start) % step_int == 0:
                return True
        elif "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
        elif int(part) == value:
            return True
    return False


def cron_matches(expression: str, dt: datetime) -> bool:
    """
    Return True if *dt* satisfies the 5-field cron *expression*.

    Fields: minute  hour  dom  month  dow
    Example: ``"*/15 9-17 * * 1-5"``  — every 15 min, 09:00-17:00, Mon-Fri.
    """
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields: {expression!r}")
    fields = [("minute", dt.minute), ("hour", dt.hour),
              ("dom", dt.day), ("month", dt.month), ("dow", dt.weekday())]
    for (name, val), expr in zip(fields, parts):
        lo, hi = _CRON_RANGES[name]
        if not _cron_field_matches(expr, val, lo, hi):
            return False
    return True


def _next_cron_ts(expression: str, after: datetime, max_minutes: int = 525_600) -> Optional[float]:
    """
    Scan forward minute-by-minute from *after* until cron matches.
    Returns unix timestamp or None if not found within *max_minutes*.
    """
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(max_minutes):
        if cron_matches(expression, candidate):
            return candidate.timestamp()
        candidate += timedelta(minutes=1)
    return None


# ---------------------------------------------------------------------------
# PipelineScheduler
# ---------------------------------------------------------------------------

class PipelineScheduler:
    """
    Background-thread scheduler that drives DataTransformer executions.

    Thread safety
    -------------
    All mutations to ``_entries`` are guarded by ``_lock``.
    ``tick()`` is called by the background thread every ``poll_interval``
    seconds; it can also be called manually in tests.

    Parameters
    ----------
    transformer    : DataTransformer instance used for all executions
    poll_interval  : seconds between scheduler ticks (default 1)
    """

    def __init__(
        self,
        transformer: DataTransformer,
        poll_interval: float = 1.0,
    ) -> None:
        self._transformer   = transformer
        self._poll_interval = poll_interval
        self._entries:  Dict[str, ScheduleEntry] = {}
        self._lock      = threading.Lock()
        self._thread:   Optional[threading.Thread] = None
        self._stop_evt  = threading.Event()

    # ------------------------------------------------------------------
    # Schedule management
    # ------------------------------------------------------------------

    def add(
        self,
        pipeline: PipelineDefinition,
        *,
        schedule_type:    ScheduleType             = ScheduleType.ONE_SHOT,
        interval_seconds: int                      = 0,
        cron_expression:  str                      = "",
        run_at:           Optional[datetime]       = None,
        max_runs:         int                      = 0,
        jitter_seconds:   int                      = 0,
        payload_factory:  Optional[Callable]       = None,
        on_complete:      Optional[Callable]       = None,
        on_error:         Optional[Callable]       = None,
        tags:             Optional[List[str]]      = None,
        schedule_id:      Optional[str]            = None,
    ) -> str:
        """
        Register a pipeline with a schedule.  Returns the schedule_id.

        Examples
        --------
        # Run once immediately
        sid = scheduler.add(pipeline)

        # Run every 5 minutes, max 12 times
        sid = scheduler.add(pipeline,
                            schedule_type=ScheduleType.INTERVAL,
                            interval_seconds=300, max_runs=12)

        # Run on a cron pattern
        sid = scheduler.add(pipeline,
                            schedule_type=ScheduleType.CRON,
                            cron_expression="0 9 * * 1-5")
        """
        sid = schedule_id or str(uuid.uuid4())
        now = time.time()

        # Compute initial next_run_at
        next_run: Optional[float]
        if schedule_type == ScheduleType.ONE_SHOT:
            if run_at:
                next_run = run_at.timestamp()
            else:
                next_run = now
        elif schedule_type == ScheduleType.INTERVAL:
            if interval_seconds <= 0:
                raise ValueError("interval_seconds must be > 0 for INTERVAL schedules")
            next_run = now
        elif schedule_type == ScheduleType.CRON:
            if not cron_expression:
                raise ValueError("cron_expression is required for CRON schedules")
            next_run = _next_cron_ts(
                cron_expression,
                datetime.fromtimestamp(now, tz=timezone.utc),
            )
        else:
            next_run = now

        entry = ScheduleEntry(
            schedule_id=sid,
            pipeline=pipeline,
            schedule_type=schedule_type,
            status=ScheduleStatus.ACTIVE,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            run_at=run_at,
            max_runs=max_runs,
            jitter_seconds=jitter_seconds,
            payload_factory=payload_factory,
            on_complete=on_complete,
            on_error=on_error,
            tags=list(tags or []),
            next_run_at=next_run,
        )
        with self._lock:
            self._entries[sid] = entry
        return sid

    def remove(self, schedule_id: str) -> None:
        """Deregister a schedule (safe to call while running)."""
        with self._lock:
            self._entries.pop(schedule_id, None)

    def pause(self, schedule_id: str) -> None:
        """Temporarily suspend a schedule."""
        with self._lock:
            if schedule_id in self._entries:
                self._entries[schedule_id].status = ScheduleStatus.PAUSED

    def resume(self, schedule_id: str) -> None:
        """Resume a paused schedule."""
        with self._lock:
            if schedule_id in self._entries:
                entry = self._entries[schedule_id]
                if entry.status == ScheduleStatus.PAUSED:
                    entry.status = ScheduleStatus.ACTIVE

    def get(self, schedule_id: str) -> Optional[ScheduleEntry]:
        with self._lock:
            return self._entries.get(schedule_id)

    def list_entries(
        self,
        status: Optional[ScheduleStatus] = None,
    ) -> List[ScheduleEntry]:
        with self._lock:
            entries = list(self._entries.values())
        if status:
            entries = [e for e in entries if e.status == status]
        return entries

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="PipelineScheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            self.tick()
            self._stop_evt.wait(timeout=self._poll_interval)

    # ------------------------------------------------------------------
    # Tick — check and fire due entries
    # ------------------------------------------------------------------

    def tick(self) -> List[RunRecord]:
        """
        Check all ACTIVE entries and execute any that are due.
        Returns list of RunRecords produced this tick.
        Called automatically by background thread; can be driven manually.
        """
        now     = time.time()
        records: List[RunRecord] = []

        with self._lock:
            due = [
                e for e in self._entries.values()
                if e.status == ScheduleStatus.ACTIVE
                and e.next_run_at is not None
                and e.next_run_at <= now
            ]

        for entry in due:
            record = self._execute_entry(entry)
            records.append(record)

        return records

    # ------------------------------------------------------------------
    # Execute a single entry
    # ------------------------------------------------------------------

    def _execute_entry(self, entry: ScheduleEntry) -> RunRecord:
        # Apply jitter
        if entry.jitter_seconds > 0:
            import random
            time.sleep(random.uniform(0, entry.jitter_seconds))

        payload = entry.payload_factory() if entry.payload_factory else {}
        started = datetime.now(tz=timezone.utc)
        error_msg: Optional[str] = None
        step_count = 0

        try:
            final_payload, results = self._transformer.execute(entry.pipeline, payload)
            step_count = len(results)
            success    = all(r.success for r in results)
            if not success:
                failed = next((r for r in results if not r.success), None)
                error_msg = failed.error if failed else "Unknown step failure"
        except Exception as exc:
            success   = False
            error_msg = str(exc)

        finished = datetime.now(tz=timezone.utc)
        record   = RunRecord(
            run_id=str(uuid.uuid4()),
            schedule_id=entry.schedule_id,
            pipeline_name=getattr(entry.pipeline, "name", "unknown"),
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            success=success,
            step_count=step_count,
            error=error_msg,
        )

        # Update entry under lock
        with self._lock:
            entry.run_count += 1
            entry._append_history(record)
            self._advance_schedule(entry)

        # Callbacks (outside lock)
        try:
            if success and entry.on_complete:
                entry.on_complete(record)
            elif not success and entry.on_error:
                entry.on_error(record)
        except Exception:
            pass  # never let callbacks crash the scheduler

        return record

    # ------------------------------------------------------------------
    # Advance schedule after a run
    # ------------------------------------------------------------------

    def _advance_schedule(self, entry: ScheduleEntry) -> None:
        """
        Recompute next_run_at and update status after a completed run.
        Must be called while holding _lock.
        """
        now = time.time()

        # Check max_runs cap
        if entry.max_runs > 0 and entry.run_count >= entry.max_runs:
            entry.status     = ScheduleStatus.DONE
            entry.next_run_at = None
            return

        if entry.schedule_type == ScheduleType.ONE_SHOT:
            entry.status      = ScheduleStatus.DONE
            entry.next_run_at = None

        elif entry.schedule_type == ScheduleType.INTERVAL:
            # Advance by interval; catch up if multiple intervals missed
            nxt = (entry.next_run_at or now) + entry.interval_seconds
            while nxt <= now:
                nxt += entry.interval_seconds
            entry.next_run_at = nxt

        elif entry.schedule_type == ScheduleType.CRON:
            entry.next_run_at = _next_cron_ts(
                entry.cron_expression,
                datetime.fromtimestamp(now, tz=timezone.utc),
            )
            if entry.next_run_at is None:
                entry.status = ScheduleStatus.DONE

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status_report(self) -> Dict[str, Any]:
        """Return a summary dict of all schedules."""
        with self._lock:
            entries = list(self._entries.values())
        return {
            "total":   len(entries),
            "active":  sum(1 for e in entries if e.status == ScheduleStatus.ACTIVE),
            "paused":  sum(1 for e in entries if e.status == ScheduleStatus.PAUSED),
            "done":    sum(1 for e in entries if e.status == ScheduleStatus.DONE),
            "error":   sum(1 for e in entries if e.status == ScheduleStatus.ERROR),
            "schedules": [e.to_dict() for e in entries],
        }

    def __repr__(self) -> str:
        n = len(self._entries)
        running = self._thread is not None and self._thread.is_alive()
        return f"PipelineScheduler(entries={n}, running={running})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PipelineScheduler",
    "ScheduleEntry",
    "ScheduleStatus",
    "ScheduleType",
    "RunRecord",
    "cron_matches",
]
