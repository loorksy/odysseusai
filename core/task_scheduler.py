"""
C100 — Task Scheduler
Priority-based async task scheduler for agent workloads.
Supports delayed execution, recurring tasks, cancellation, and concurrency limits.
"""
from __future__ import annotations

import asyncio
import heapq
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


@dataclass(order=True)
class ScheduledTask:
    run_at: float
    priority: int
    task_id: str = field(compare=False)
    fn: Callable = field(compare=False)
    args: tuple = field(compare=False, default_factory=tuple)
    kwargs: dict = field(compare=False, default_factory=dict)
    recur_every: Optional[float] = field(compare=False, default=None)
    cancelled: bool = field(compare=False, default=False)
    label: str = field(compare=False, default="")


@dataclass
class TaskResult:
    task_id: str
    label: str
    output: Any
    success: bool
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return self.finished_at - self.started_at


class TaskScheduler:
    """
    Async task scheduler backed by a min-heap.

    Usage::

        scheduler = TaskScheduler(max_concurrency=4)
        scheduler.schedule(my_fn, delay=1.0, label="ping")
        await scheduler.run_until_empty()
    """

    def __init__(self, max_concurrency: int = 8):
        self._heap: list[ScheduledTask] = []
        self._results: list[TaskResult] = []
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._max_concurrency = max_concurrency
        self._running = False

    def schedule(
        self,
        fn: Callable,
        delay: float = 0.0,
        priority: int = 5,
        recur_every: Optional[float] = None,
        label: str = "",
        *args,
        **kwargs,
    ) -> str:
        task_id = str(uuid.uuid4())[:8]
        task = ScheduledTask(
            run_at=time.time() + delay,
            priority=priority,
            task_id=task_id,
            fn=fn,
            args=args,
            kwargs=kwargs,
            recur_every=recur_every,
            label=label or getattr(fn, "__name__", "task"),
        )
        heapq.heappush(self._heap, task)
        logger.debug("Scheduled task '%s' (id=%s, delay=%.2fs)", task.label, task_id, delay)
        return task_id

    def cancel(self, task_id: str) -> bool:
        for task in self._heap:
            if task.task_id == task_id:
                task.cancelled = True
                logger.debug("Cancelled task id=%s", task_id)
                return True
        return False

    def pending_count(self) -> int:
        return sum(1 for t in self._heap if not t.cancelled)

    async def run_until_empty(self, timeout: Optional[float] = None) -> list[TaskResult]:
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._running = True
        deadline = time.time() + timeout if timeout else None
        active: set[asyncio.Task] = set()

        while self._heap or active:
            if deadline and time.time() > deadline:
                logger.warning("Scheduler timed out")
                break
            now = time.time()
            while self._heap and self._heap[0].run_at <= now:
                task = heapq.heappop(self._heap)
                if task.cancelled:
                    continue
                coro_task = asyncio.create_task(self._execute(task))
                active.add(coro_task)
                coro_task.add_done_callback(active.discard)
            if active:
                _, pending = await asyncio.wait(active, timeout=0.05)
                active = pending
            else:
                await asyncio.sleep(0.05)

        self._running = False
        return list(self._results)

    async def _execute(self, task: ScheduledTask) -> None:
        async with self._semaphore:
            start = time.time()
            try:
                if asyncio.iscoroutinefunction(task.fn):
                    output = await task.fn(*task.args, **task.kwargs)
                else:
                    output = await asyncio.to_thread(task.fn, *task.args, **task.kwargs)
                result = TaskResult(
                    task_id=task.task_id,
                    label=task.label,
                    output=output,
                    success=True,
                    started_at=start,
                    finished_at=time.time(),
                )
                logger.debug("Task '%s' succeeded (%.2fs)", task.label, result.elapsed)
            except Exception as e:
                result = TaskResult(
                    task_id=task.task_id,
                    label=task.label,
                    output=None,
                    success=False,
                    error=str(e),
                    started_at=start,
                    finished_at=time.time(),
                )
                logger.error("Task '%s' failed: %s", task.label, e)
            self._results.append(result)
            if task.recur_every and not task.cancelled:
                self.schedule(
                    task.fn,
                    delay=task.recur_every,
                    priority=task.priority,
                    recur_every=task.recur_every,
                    label=task.label,
                    *task.args,
                    **task.kwargs,
                )

    def clear_results(self) -> None:
        self._results.clear()
