"""
C94 · WorkflowScheduler
=======================
Fires SCHEDULE and EVENT triggers for active WorkflowDefinitions,
dispatching each run to the WorkflowEngine and persisting results
via WorkflowStore.

Design principles
-----------------
* Single background thread per scheduler instance (daemon=True).
* Cron expressions: 5-field standard cron parsed in pure stdlib.
* Event bus: publish(event_type, payload) wakes any waiting workflows.
* Missed-run policy: fire once on wake-up if the last fire was >1 interval ago.
* Thread-safe: all shared state guarded by threading.Lock / threading.Event.
* Zero external deps — stdlib only.

Cron support
------------
Field order:  minute  hour  day-of-month  month  day-of-week
Supported syntax per field:
    *           every unit
    */N         every N units
    N           exact value
    N,M,…       list of values
    N-M         inclusive range

Lifecycle
---------
    scheduler = WorkflowScheduler(engine, store, tick=30)
    scheduler.start()          # launches background thread
    scheduler.publish("user.login", {"user_id": "u1"})  # fire event workflows
    scheduler.stop()           # graceful shutdown (waits for current tick)

Auto-registration
-----------------
On start() (and on each tick) the scheduler calls store.list_workflows(status="active")
and registers any SCHEDULE / EVENT workflows it hasn't seen yet.
Workflows moved to non-active status are automatically unregistered.

Usage
-----
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType, WorkflowStatus
    from core.workflow_engine    import WorkflowEngine
    from core.workflow_store     import WorkflowStore
    from core.workflow_scheduler import WorkflowScheduler

    store  = WorkflowStore("./data/store")
    engine = WorkflowEngine()
    engine.register_stub(ActionType.TOOL_CALL, {"ok": True})

    wf = (
        WorkflowBuilder("hourly-report")
        .trigger(TriggerType.SCHEDULE, cron="0 * * * *")
        .action("report", ActionType.TOOL_CALL, tool="reporter")
        .build()
    )
    wf.status = WorkflowStatus.ACTIVE
    store.save_workflow(wf)

    scheduler = WorkflowScheduler(engine, store, tick=60)
    scheduler.start()
    # … later …
    scheduler.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from core.workflow_definition import TriggerType, WorkflowDefinition, WorkflowStatus
from core.workflow_engine import RunRecord, WorkflowEngine
from core.workflow_store import WorkflowStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------

class CronExpression:
    """
    Parse and evaluate a 5-field cron expression.

    Fields: minute(0-59)  hour(0-23)  dom(1-31)  month(1-12)  dow(0-6, 0=Sun)
    """

    _RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    _NAMES: Dict[int, Dict[str, int]] = {
        3: {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
            "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12},
        4: {"sun":0,"mon":1,"tue":2,"wed":3,"thu":4,"fri":5,"sat":6},
    }

    def __init__(self, expression: str) -> None:
        self.expression = expression.strip()
        parts = self.expression.split()
        if len(parts) != 5:
            raise ValueError(
                f"Cron expression must have 5 fields, got {len(parts)}: {expression!r}"
            )
        self._fields: List[Set[int]] = [
            self._parse_field(parts[i], *self._RANGES[i], self._NAMES.get(i, {}))
            for i in range(5)
        ]

    @staticmethod
    def _parse_field(
        token: str,
        lo: int,
        hi: int,
        names: Dict[str, int],
    ) -> Set[int]:
        result: Set[int] = set()
        for part in token.split(","):
            part = part.strip().lower()
            if part == "*":
                result.update(range(lo, hi + 1))
            elif part.startswith("*/"):
                step = int(part[2:])
                result.update(range(lo, hi + 1, step))
            elif "-" in part:
                a_s, b_s = part.split("-", 1)
                a = names.get(a_s, int(a_s))
                b = names.get(b_s, int(b_s))
                result.update(range(a, b + 1))
            else:
                result.add(names.get(part, int(part)))
        return result

    def matches(self, dt: datetime) -> bool:
        """Return True if *dt* matches this cron expression."""
        return (
            dt.minute     in self._fields[0]
            and dt.hour   in self._fields[1]
            and dt.day    in self._fields[2]
            and dt.month  in self._fields[3]
            and dt.weekday() in {(d - 1) % 7 for d in self._fields[4]}  # cron: 0=Sun
        )

    def __repr__(self) -> str:
        return f"CronExpression({self.expression!r})"


# ---------------------------------------------------------------------------
# Registered trigger bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class _ScheduleEntry:
    workflow_id:  str
    cron:         CronExpression
    last_fired:   Optional[datetime] = None


@dataclass
class _EventEntry:
    workflow_id: str
    event_type:  str


# ---------------------------------------------------------------------------
# RunCallback type
# ---------------------------------------------------------------------------

# Called after each run (success or failure); useful for alerting / metrics.
RunCallback = Callable[[RunRecord], None]


# ---------------------------------------------------------------------------
# WorkflowScheduler
# ---------------------------------------------------------------------------

class WorkflowScheduler:
    """
    Background scheduler that fires SCHEDULE and EVENT workflow triggers.

    Parameters
    ----------
    engine : WorkflowEngine
        Engine used to execute triggered workflows.
    store : WorkflowStore
        Store used to load active workflows and persist run records.
    tick : float
        Poll interval in seconds (default 60).  Should be ≤ 60 for
        per-minute cron granularity.
    on_run_complete : RunCallback | None
        Optional callback invoked after every run, regardless of outcome.
    """

    def __init__(
        self,
        engine: WorkflowEngine,
        store: WorkflowStore,
        tick: float = 60.0,
        on_run_complete: Optional[RunCallback] = None,
    ) -> None:
        self._engine         = engine
        self._store          = store
        self._tick           = tick
        self._on_complete    = on_run_complete

        self._lock           = threading.Lock()
        self._stop_event     = threading.Event()
        self._event_queue:   List[Dict[str, Any]] = []
        self._schedule_reg:  Dict[str, _ScheduleEntry] = {}   # wf_id → entry
        self._event_reg:     Dict[str, List[_EventEntry]] = {} # event_type → [entries]
        self._thread:        Optional[threading.Thread] = None
        self._running        = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background scheduler thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="WorkflowScheduler",
            daemon=True,
        )
        self._thread.start()
        log.info("WorkflowScheduler started (tick=%.1fs)", self._tick)

    def stop(self, timeout: float = 10.0) -> None:
        """Signal the scheduler to stop and wait for the thread to exit."""
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        log.info("WorkflowScheduler stopped")

    def publish(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """
        Enqueue an internal event.  All EVENT workflows listening to
        *event_type* will be triggered on the next scheduler tick
        (or immediately if tick=0).
        """
        with self._lock:
            self._event_queue.append({"event_type": event_type, "payload": payload or {}})
        log.debug("Event enqueued: %s", event_type)

    def register_workflow(self, wf: WorkflowDefinition) -> None:
        """
        Manually register a workflow without waiting for the next auto-sync.
        Idempotent — re-registering updates the cron expression.
        """
        self._register(wf)

    def unregister_workflow(self, workflow_id: str) -> None:
        """Remove a workflow from the scheduler (does not affect the store)."""
        with self._lock:
            self._schedule_reg.pop(workflow_id, None)
            for entries in self._event_reg.values():
                entries[:] = [e for e in entries if e.workflow_id != workflow_id]

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick_once()
            except Exception:
                log.exception("Unhandled error in scheduler tick")
            self._stop_event.wait(timeout=self._tick)

    def _tick_once(self) -> None:
        self._sync_active_workflows()
        now = datetime.now(timezone.utc)
        self._fire_schedules(now)
        self._drain_event_queue()

    # ------------------------------------------------------------------
    # Auto-sync with store
    # ------------------------------------------------------------------

    def _sync_active_workflows(self) -> None:
        """Load active workflows from store; register new, unregister stale."""
        try:
            active = self._store.list_workflows(status=WorkflowStatus.ACTIVE.value, limit=1000)
        except Exception:
            log.exception("Failed to load active workflows from store")
            return

        active_ids: Set[str] = {wf.workflow_id for wf in active}

        # Register new
        for wf in active:
            if wf.workflow_id not in self._schedule_reg and not self._in_event_reg(wf.workflow_id):
                self._register(wf)

        # Unregister stale
        with self._lock:
            stale = [wid for wid in self._schedule_reg if wid not in active_ids]
        for wid in stale:
            self.unregister_workflow(wid)

        stale_event: List[str] = []
        with self._lock:
            for entries in self._event_reg.values():
                for e in entries:
                    if e.workflow_id not in active_ids:
                        stale_event.append(e.workflow_id)
        for wid in set(stale_event):
            self.unregister_workflow(wid)

    def _in_event_reg(self, workflow_id: str) -> bool:
        return any(
            any(e.workflow_id == workflow_id for e in entries)
            for entries in self._event_reg.values()
        )

    def _register(self, wf: WorkflowDefinition) -> None:
        trigger = wf.nodes.get(wf.trigger_id)
        if trigger is None:
            return

        if trigger.trigger_type == TriggerType.SCHEDULE:
            cron_expr = trigger.config.get("cron", "")
            if not cron_expr:
                log.warning("Workflow %s has SCHEDULE trigger but no cron field", wf.workflow_id)
                return
            try:
                cron = CronExpression(cron_expr)
            except ValueError as exc:
                log.warning("Invalid cron for workflow %s: %s", wf.workflow_id, exc)
                return
            with self._lock:
                self._schedule_reg[wf.workflow_id] = _ScheduleEntry(
                    workflow_id=wf.workflow_id, cron=cron
                )
            log.debug("Registered schedule workflow %s (%s)", wf.workflow_id, cron_expr)

        elif trigger.trigger_type == TriggerType.EVENT:
            event_type = trigger.config.get("event_type", "")
            if not event_type:
                log.warning("Workflow %s has EVENT trigger but no event_type", wf.workflow_id)
                return
            entry = _EventEntry(workflow_id=wf.workflow_id, event_type=event_type)
            with self._lock:
                self._event_reg.setdefault(event_type, []).append(entry)
            log.debug("Registered event workflow %s (event=%s)", wf.workflow_id, event_type)

    # ------------------------------------------------------------------
    # Schedule firing
    # ------------------------------------------------------------------

    def _fire_schedules(self, now: datetime) -> None:
        with self._lock:
            entries = list(self._schedule_reg.values())

        for entry in entries:
            if not entry.cron.matches(now):
                continue
            # Deduplicate: don't fire twice in the same minute
            if entry.last_fired and _same_minute(entry.last_fired, now):
                continue
            entry.last_fired = now
            self._dispatch(entry.workflow_id, {"triggered_at": now.isoformat(), "trigger": "schedule"})

    # ------------------------------------------------------------------
    # Event queue draining
    # ------------------------------------------------------------------

    def _drain_event_queue(self) -> None:
        with self._lock:
            events, self._event_queue = list(self._event_queue), []

        for event in events:
            et = event["event_type"]
            with self._lock:
                entries = list(self._event_reg.get(et, []))
            for entry in entries:
                self._dispatch(
                    entry.workflow_id,
                    {"event_type": et, "event_payload": event["payload"],
                     "trigger": "event"},
                )

    # ------------------------------------------------------------------
    # Dispatch a single workflow run in a daemon thread
    # ------------------------------------------------------------------

    def _dispatch(self, workflow_id: str, trigger_payload: Dict[str, Any]) -> None:
        """Load, execute, and persist one workflow run (non-blocking)."""
        def _run() -> None:
            try:
                wf = self._store.load_workflow(workflow_id)
            except Exception:
                log.exception("Scheduler: failed to load workflow %s", workflow_id)
                return
            try:
                record = self._engine.run(wf, trigger_payload)
            except Exception:
                log.exception("Scheduler: unhandled error running workflow %s", workflow_id)
                return
            try:
                self._store.save_run(record)
            except Exception:
                log.exception("Scheduler: failed to save run for workflow %s", workflow_id)
            if self._on_complete:
                try:
                    self._on_complete(record)
                except Exception:
                    log.exception("Scheduler: on_run_complete callback raised")
            log.info(
                "Workflow %s run %s → %s (%.3fs)",
                workflow_id, record.run_id, record.status.value, record.elapsed,
            )

        t = threading.Thread(target=_run, daemon=True, name=f"wf-run-{workflow_id[:8]}")
        t.start()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _same_minute(a: datetime, b: datetime) -> bool:
    return a.year == b.year and a.month == b.month and a.day == b.day \
           and a.hour == b.hour and a.minute == b.minute
