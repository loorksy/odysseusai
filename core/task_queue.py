"""TaskQueue — Priority-based async task queue (C64).

Thread-safe FIFO/priority queue for background tasks with:
  - Priority levels: CRITICAL(0), HIGH(1), NORMAL(2), LOW(3)
  - Task deduplication by idempotency key
  - TTL: tasks that aren't consumed before expiry are discarded
  - Retry tracking: max_retries, attempt count, backoff delay
  - Dead-letter queue for exhausted tasks
  - Blocking and non-blocking dequeue

Public API:
  tq = TaskQueue(maxsize=0)
  task_id = tq.enqueue(fn, args, kwargs, *, priority, ttl_s, max_retries, idempotency_key)
  task    = tq.dequeue(block=True, timeout=None)  -> Task | None
  tq.ack(task_id)
  tq.nack(task_id, *, requeue=True)
  tq.dead_letters()  -> list[Task]
  tq.stats()         -> dict
  tq.cancel(task_id) -> bool
"""
from __future__ import annotations
import heapq, logging, threading, time, uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CRITICAL, HIGH, NORMAL, LOW = 0, 1, 2, 3


@dataclass
class Task:
    task_id:         str
    fn:              Callable
    args:            Tuple
    kwargs:          Dict
    priority:        int        = NORMAL
    created_at:      float      = field(default_factory=time.time)
    expires_at:      Optional[float] = None
    max_retries:     int        = 3
    attempts:        int        = 0
    idempotency_key: Optional[str] = None
    delay_until:     float      = 0.0

    def __lt__(self, other: "Task") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    def is_ready(self) -> bool:
        return time.time() >= self.delay_until


class TaskQueue:
    """Priority-based task queue with retry and dead-letter support."""

    def __init__(self, maxsize: int = 0):
        self._heap:       List[Task] = []
        self._in_flight:  Dict[str, Task] = {}
        self._dead:       List[Task] = []
        self._idem_keys:  Dict[str, str] = {}  # idempotency_key -> task_id
        self._cancelled:  set = set()
        self._lock        = threading.Lock()
        self._not_empty   = threading.Condition(self._lock)
        self._maxsize     = maxsize
        self._enqueued = self._acked = self._nacked = self._dropped = 0

    def enqueue(
        self,
        fn:              Callable,
        args:            Tuple = (),
        kwargs:          Optional[Dict] = None,
        *,
        priority:        int   = NORMAL,
        ttl_s:           Optional[float] = None,
        max_retries:     int   = 3,
        idempotency_key: Optional[str] = None,
        delay_s:         float = 0.0,
    ) -> str:
        with self._not_empty:
            if idempotency_key and idempotency_key in self._idem_keys:
                return self._idem_keys[idempotency_key]
            if self._maxsize and len(self._heap) >= self._maxsize:
                raise OverflowError("TaskQueue is full")
            task = Task(
                task_id=uuid.uuid4().hex,
                fn=fn, args=args, kwargs=kwargs or {},
                priority=priority,
                expires_at=(time.time() + ttl_s) if ttl_s else None,
                max_retries=max_retries,
                idempotency_key=idempotency_key,
                delay_until=time.time() + delay_s,
            )
            heapq.heappush(self._heap, task)
            if idempotency_key:
                self._idem_keys[idempotency_key] = task.task_id
            self._enqueued += 1
            self._not_empty.notify()
        return task.task_id

    def dequeue(self, block: bool = True, timeout: Optional[float] = None) -> Optional[Task]:
        deadline = (time.time() + timeout) if timeout else None
        with self._not_empty:
            while True:
                task = self._next_ready()
                if task:
                    task.attempts += 1
                    self._in_flight[task.task_id] = task
                    return task
                if not block:
                    return None
                wait_s = 0.1 if deadline is None else max(0, deadline - time.time())
                if deadline and time.time() >= deadline:
                    return None
                self._not_empty.wait(timeout=wait_s)

    def ack(self, task_id: str) -> None:
        with self._lock:
            task = self._in_flight.pop(task_id, None)
            if task and task.idempotency_key:
                self._idem_keys.pop(task.idempotency_key, None)
            self._acked += 1

    def nack(self, task_id: str, *, requeue: bool = True, delay_s: float = 0.0) -> None:
        with self._not_empty:
            task = self._in_flight.pop(task_id, None)
            if not task: return
            self._nacked += 1
            if requeue and task.attempts < task.max_retries:
                task.delay_until = time.time() + delay_s
                heapq.heappush(self._heap, task)
                self._not_empty.notify()
            else:
                self._dead.append(task)
                if task.idempotency_key:
                    self._idem_keys.pop(task.idempotency_key, None)
                logger.warning(f"TaskQueue: task {task_id} moved to dead-letter after {task.attempts} attempts")

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            self._cancelled.add(task_id)
            return True

    def dead_letters(self) -> List[Task]:
        return list(self._dead)

    def stats(self) -> Dict:
        with self._lock:
            return {
                "queued":     len(self._heap),
                "in_flight":  len(self._in_flight),
                "dead":       len(self._dead),
                "enqueued":   self._enqueued,
                "acked":      self._acked,
                "nacked":     self._nacked,
            }

    def _next_ready(self) -> Optional[Task]:
        while self._heap:
            task = self._heap[0]
            if task.task_id in self._cancelled:
                heapq.heappop(self._heap)
                self._dropped += 1
                continue
            if task.is_expired():
                heapq.heappop(self._heap)
                self._dead.append(task)
                self._dropped += 1
                continue
            if task.is_ready():
                heapq.heappop(self._heap)
                return task
            break
        return None
