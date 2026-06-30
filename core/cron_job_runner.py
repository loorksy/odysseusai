"""CronJobRunner — Cron-expression job scheduler (C47).

Parses standard 5-field cron expressions and fires registered callables
at the correct wall-clock times.  Backed by TaskScheduler for actual
execution; CronJobRunner only handles the next-fire-time calculation.

Cron field order:  minute  hour  day  month  weekday
Supported syntax:  * / , -  (no @reboot / @daily shorthands)

Public API:
  runner = CronJobRunner(scheduler)
  job_id = runner.add(expression, fn, *args, name, **kwargs)
  runner.remove(job_id)
  runner.list_jobs()  -> list[CronJob]
  runner.next_fire(expression, after_ts)  -> float
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CronJob:
    job_id:     str
    expression: str
    name:       str
    fn:         Callable
    args:       tuple
    kwargs:     dict
    task_id:    Optional[str] = None   # current TaskScheduler task ID
    enabled:    bool = True
    last_run:   Optional[float] = None
    run_count:  int = 0


class CronJobRunner:
    """Schedules callables via cron expressions using TaskScheduler."""

    def __init__(self, scheduler):
        self._scheduler = scheduler
        self._jobs: Dict[str, CronJob] = {}

    # ------------------------------------------------------------------
    # Job management
    # ------------------------------------------------------------------

    def add(
        self,
        expression: str,
        fn: Callable,
        *args,
        name: str = "",
        **kwargs,
    ) -> str:
        _validate(expression)
        job_id = uuid.uuid4().hex[:10]
        job = CronJob(
            job_id=job_id,
            expression=expression,
            name=name or fn.__name__,
            fn=fn, args=args, kwargs=kwargs,
        )
        self._jobs[job_id] = job
        self._schedule_next(job)
        return job_id

    def remove(self, job_id: str) -> bool:
        job = self._jobs.pop(job_id, None)
        if job and job.task_id:
            self._scheduler.cancel(job.task_id)
        return job is not None

    def enable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.enabled = True
            self._schedule_next(job)
            return True
        return False

    def disable(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.enabled = False
            if job.task_id:
                self._scheduler.cancel(job.task_id)
            return True
        return False

    def list_jobs(self) -> List[CronJob]:
        return list(self._jobs.values())

    # ------------------------------------------------------------------
    # Next-fire calculation
    # ------------------------------------------------------------------

    @staticmethod
    def next_fire(expression: str, after_ts: Optional[float] = None) -> float:
        """Return unix timestamp of next fire after `after_ts` (default: now)."""
        after = datetime.fromtimestamp(after_ts or time.time(), tz=timezone.utc)
        fields = expression.strip().split()
        if len(fields) != 5:
            raise ValueError(f"Invalid cron expression: '{expression}'")
        minutes, hours, days, months, weekdays = [_expand(f, lo, hi)
            for f, (lo, hi) in zip(fields, [(0,59),(0,23),(1,31),(1,12),(0,6)])]

        # Advance minute by 1 so we don't re-fire the current minute
        candidate = after.replace(second=0, microsecond=0)
        from datetime import timedelta
        candidate += timedelta(minutes=1)

        for _ in range(366 * 24 * 60):   # max 1 year search
            if (candidate.month in months
                    and candidate.day in days
                    and candidate.weekday() in [w % 7 for w in weekdays]
                    and candidate.hour in hours
                    and candidate.minute in minutes):
                return candidate.timestamp()
            candidate += timedelta(minutes=1)
        raise RuntimeError("No next fire time found within 1 year")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _schedule_next(self, job: CronJob) -> None:
        if not job.enabled:
            return
        try:
            fire_ts = self.next_fire(job.expression)
        except Exception as e:
            logger.warning(f"CronJobRunner: cannot compute next fire for '{job.name}': {e}")
            return

        def _run():
            job.last_run  = time.time()
            job.run_count += 1
            try:
                job.fn(*job.args, **job.kwargs)
            except Exception as e:
                logger.warning(f"CronJobRunner: job '{job.name}' raised: {e}")
            finally:
                self._schedule_next(job)  # re-arm

        job.task_id = self._scheduler.run_at(fire_ts, _run)


# ---------------------------------------------------------------------------
# Cron expression helpers
# ---------------------------------------------------------------------------

def _validate(expression: str) -> None:
    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got: '{expression}'")


def _expand(field: str, lo: int, hi: int) -> set:
    """Expand a cron field string to a set of integers in [lo, hi]."""
    result = set()
    for part in field.split(","):
        if part == "*":
            result.update(range(lo, hi + 1))
        elif "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            start = lo if base == "*" else int(base.split("-")[0])
            result.update(range(start, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return result
