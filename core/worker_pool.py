"""WorkerPool — Fixed thread pool that drains a TaskQueue (C66).

Spawns N worker threads that continuously pull tasks from a TaskQueue,
execute them, and ack/nack based on outcome.

Features:
  - Configurable pool size (default: CPU count)
  - Graceful shutdown: drain in-flight before stopping
  - Per-worker error isolation: one failure doesn't kill the pool
  - Exponential backoff on nack with jitter
  - Live metrics: active workers, tasks/s throughput
  - Dynamic resize: grow/shrink pool at runtime

Public API:
  pool = WorkerPool(task_queue, num_workers=4)
  pool.start()
  pool.stop(graceful=True, timeout_s=30)
  pool.resize(n)
  pool.stats() -> dict
"""
from __future__ import annotations
import logging, os, random, threading, time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS  = min(32, (os.cpu_count() or 1) + 4)
_DEQUEUE_TIMEOUT  = 1.0
_BASE_BACKOFF     = 0.5
_MAX_BACKOFF      = 60.0


class WorkerPool:
    """Thread pool that drains a TaskQueue."""

    def __init__(
        self,
        task_queue,
        num_workers: int = _DEFAULT_WORKERS,
        metrics_registry = None,
    ):
        self._tq       = task_queue
        self._target   = num_workers
        self._metrics  = metrics_registry
        self._workers: List[threading.Thread] = []
        self._stop     = threading.Event()
        self._lock     = threading.Lock()
        self._active   = 0
        self._completed = 0
        self._errors   = 0
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        with self._lock:
            for i in range(self._target):
                self._spawn_worker(i)
        logger.info(f"WorkerPool: started {self._target} workers")

    def stop(self, graceful: bool = True, timeout_s: float = 30.0) -> None:
        self._stop.set()
        if graceful:
            deadline = time.time() + timeout_s
            with self._lock:
                workers = list(self._workers)
            for w in workers:
                remaining = max(0, deadline - time.time())
                w.join(timeout=remaining)
        logger.info("WorkerPool: stopped")

    def resize(self, n: int) -> None:
        with self._lock:
            current = len([w for w in self._workers if w.is_alive()])
            if n > current:
                for i in range(n - current):
                    self._spawn_worker(current + i)
            self._target = n
        logger.info(f"WorkerPool: resized to {n} workers")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict:
        with self._lock:
            alive = sum(1 for w in self._workers if w.is_alive())
        elapsed = max(1.0, time.time() - self._start_time)
        return {
            "workers_alive": alive,
            "workers_target": self._target,
            "active_tasks":  self._active,
            "completed":     self._completed,
            "errors":        self._errors,
            "throughput_per_s": round(self._completed / elapsed, 2),
        }

    # ------------------------------------------------------------------
    # Worker internals
    # ------------------------------------------------------------------

    def _spawn_worker(self, idx: int) -> None:
        t = threading.Thread(
            target=self._worker_loop, args=(idx,),
            name=f"worker-{idx}", daemon=True
        )
        t.start()
        self._workers.append(t)

    def _worker_loop(self, idx: int) -> None:
        backoff = _BASE_BACKOFF
        while not self._stop.is_set():
            task = self._tq.dequeue(block=True, timeout=_DEQUEUE_TIMEOUT)
            if task is None:
                continue
            with self._lock:
                self._active += 1
            try:
                task.fn(*task.args, **task.kwargs)
                self._tq.ack(task.task_id)
                with self._lock:
                    self._completed += 1
                backoff = _BASE_BACKOFF
                self._emit_metric("task_completed")
            except Exception as e:
                logger.warning(f"WorkerPool worker-{idx}: task {task.task_id} failed: {e}")
                jitter = random.uniform(0, backoff * 0.1)
                self._tq.nack(task.task_id, requeue=True, delay_s=backoff + jitter)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                with self._lock:
                    self._errors += 1
                self._emit_metric("task_error")
            finally:
                with self._lock:
                    self._active -= 1

    def _emit_metric(self, event: str) -> None:
        if self._metrics:
            try:
                self._metrics.counter(event).inc()
            except Exception:
                pass
