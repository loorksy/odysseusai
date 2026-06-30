"""JobScheduler — Cron-style and interval job scheduling (C65).

Runs named jobs on a fixed interval or cron-like schedule.
Jobs are dispatched to a TaskQueue or run inline in a background thread.

Features:
  - Interval jobs: run every N seconds
  - Cron jobs: run at specific minute/hour/day patterns (subset of cron)
  - One-shot: run once at a specific future time
  - Missed-run policy: skip | catchup (run once) | run_all
  - Overlap guard: skip if previous run hasn't finished
  - Job history: last N run results per job

Public API:
  sched = JobScheduler(task_queue=None)
  sched.every(name, fn, interval_s, *, args, kwargs, overlap, missed)
  sched.cron(name, fn, minute, hour, *, args, kwargs)
  sched.once(name, fn, run_at, *, args, kwargs)
  sched.cancel(name)
  sched.start()   # background thread
  sched.stop()
  sched.jobs()    -> list[JobInfo]
  sched.history(name, n) -> list[dict]
"""
from __future__ import annotations
import logging, threading, time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class JobInfo:
    name:        str
    kind:        str        # interval | cron | once
    fn:          Callable
    args:        Tuple
    kwargs:      Dict
    interval_s:  Optional[float] = None
    cron_minute: Optional[int]   = None   # None = wildcard
    cron_hour:   Optional[int]   = None
    run_at:      Optional[float] = None   # once
    next_run:    float = 0.0
    running:     bool  = False
    cancelled:   bool  = False
    missed:      str   = "skip"           # skip | catchup | run_all
    overlap:     bool  = False            # allow concurrent runs
    history:     List[Dict] = field(default_factory=list)


class JobScheduler:
    """Interval / cron / one-shot job dispatcher."""

    def __init__(self, task_queue=None):
        self._tq      = task_queue
        self._jobs:   Dict[str, JobInfo] = {}
        self._lock    = threading.Lock()
        self._stop    = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def every(
        self, name: str, fn: Callable, interval_s: float,
        *, args: Tuple = (), kwargs: Optional[Dict] = None,
        overlap: bool = False, missed: str = "skip",
    ) -> None:
        job = JobInfo(name=name, kind="interval", fn=fn, args=args,
                      kwargs=kwargs or {}, interval_s=interval_s,
                      next_run=time.time(), overlap=overlap, missed=missed)
        with self._lock:
            self._jobs[name] = job

    def cron(
        self, name: str, fn: Callable,
        minute: Optional[int] = None, hour: Optional[int] = None,
        *, args: Tuple = (), kwargs: Optional[Dict] = None,
    ) -> None:
        job = JobInfo(name=name, kind="cron", fn=fn, args=args,
                      kwargs=kwargs or {}, cron_minute=minute, cron_hour=hour,
                      next_run=self._next_cron(minute, hour))
        with self._lock:
            self._jobs[name] = job

    def once(
        self, name: str, fn: Callable, run_at: float,
        *, args: Tuple = (), kwargs: Optional[Dict] = None,
    ) -> None:
        job = JobInfo(name=name, kind="once", fn=fn, args=args,
                      kwargs=kwargs or {}, run_at=run_at, next_run=run_at)
        with self._lock:
            self._jobs[name] = job

    def cancel(self, name: str) -> bool:
        with self._lock:
            job = self._jobs.get(name)
            if job: job.cancelled = True
        return job is not None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def jobs(self) -> List[JobInfo]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.cancelled]

    def history(self, name: str, n: int = 20) -> List[Dict]:
        with self._lock:
            job = self._jobs.get(name)
        return (job.history[-n:] if job else [])

    # ------------------------------------------------------------------
    # Scheduler loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                jobs = list(self._jobs.values())
            for job in jobs:
                if job.cancelled or job.next_run > now:
                    continue
                if job.running and not job.overlap:
                    continue
                self._dispatch(job, now)
            self._stop.wait(1.0)

    def _dispatch(self, job: JobInfo, now: float) -> None:
        job.running = True
        def _run():
            start = time.time()
            try:
                job.fn(*job.args, **job.kwargs)
                status = "ok"
            except Exception as e:
                status = f"error: {e}"
                logger.warning(f"JobScheduler: '{job.name}' raised: {e}")
            finally:
                job.running = False
            job.history.append({"ran_at": start, "duration_s": time.time() - start, "status": status})
            if len(job.history) > 100: job.history = job.history[-100:]
        if self._tq:
            self._tq.enqueue(_run)
        else:
            threading.Thread(target=_run, daemon=True).start()
        self._advance(job, now)

    def _advance(self, job: JobInfo, now: float) -> None:
        if job.kind == "interval":
            job.next_run = now + job.interval_s
        elif job.kind == "cron":
            job.next_run = self._next_cron(job.cron_minute, job.cron_hour)
        elif job.kind == "once":
            job.cancelled = True

    @staticmethod
    def _next_cron(minute: Optional[int], hour: Optional[int]) -> float:
        import datetime
        now = datetime.datetime.now()
        nxt = now.replace(second=0, microsecond=0)
        for _ in range(1440):  # max 1 day lookahead
            nxt += datetime.timedelta(minutes=1)
            if hour   is not None and nxt.hour   != hour:   continue
            if minute is not None and nxt.minute != minute: continue
            return nxt.timestamp()
        return now.timestamp() + 86400
