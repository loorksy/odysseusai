"""SSEEventBus — In-process pub/sub bus for Server-Sent Events (C38).

Decouples event producers (streaming LLM, tool executor, background tasks)
from SSE consumer endpoints.  Each session gets an isolated async queue;
Flask/Quart/FastAPI route handlers subscribe to that queue and forward
events as `data: ...\n\n` SSE frames.

Event model:
  Each event is a dict: {"event": str, "data": any, "id": str (optional)}
  Serialised to SSE wire format by `SSEEventBus.format_sse()`.

Design:
  - One asyncio.Queue per (session_id, subscriber_id) pair
  - Publisher calls `await bus.publish(event, data, session_id)` — O(subscribers)
  - Subscriber calls `async for msg in bus.subscribe(session_id)` — blocking
  - Heartbeat task emits `:keep-alive\n\n` every N seconds to prevent proxy timeouts
  - Stale queues are garbage-collected after TTL with no subscriber

Public API:
  bus = SSEEventBus(maxsize=256, heartbeat_s=15, queue_ttl_s=300)
  await bus.publish(event, data, *, session_id, event_id)
  async for frame in bus.subscribe(session_id, *, timeout_s):  yield str
  bus.close_session(session_id)
  SSEEventBus.format_sse(event, data, event_id)  → str  (static)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAXSIZE    = 256
_DEFAULT_HEARTBEAT  = 15      # seconds
_DEFAULT_QUEUE_TTL  = 300     # seconds
_SENTINEL           = object()  # signals subscriber to stop


class SSEEventBus:
    """In-process SSE pub/sub bus."""

    def __init__(
        self,
        maxsize: int = _DEFAULT_MAXSIZE,
        heartbeat_s: float = _DEFAULT_HEARTBEAT,
        queue_ttl_s: float = _DEFAULT_QUEUE_TTL,
    ):
        self._maxsize     = maxsize
        self._heartbeat_s = heartbeat_s
        self._queue_ttl_s = queue_ttl_s
        # session_id → list of asyncio.Queue
        self._queues: Dict[str, list] = {}
        # session_id → last-active timestamp
        self._last_active: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._hb_task: Optional[asyncio.Task] = None
        self._gc_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background heartbeat and GC tasks (call once on app startup)."""
        loop = asyncio.get_event_loop()
        self._hb_task = loop.create_task(self._heartbeat_loop())
        self._gc_task = loop.create_task(self._gc_loop())

    def stop(self) -> None:
        for task in (self._hb_task, self._gc_task):
            if task:
                task.cancel()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(
        self,
        event: str,
        data: Any,
        *,
        session_id: str,
        event_id: Optional[str] = None,
    ) -> int:
        """Broadcast an event to all subscribers of `session_id`.
        Returns number of queues delivered to.
        """
        frame = self.format_sse(event, data, event_id)
        delivered = 0
        async with self._lock:
            queues = self._queues.get(session_id, [])
            self._last_active[session_id] = time.time()
        for q in list(queues):
            try:
                q.put_nowait(frame)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(f"SSEEventBus: queue full for session '{session_id}', dropping event")
        return delivered

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        session_id: str,
        *,
        timeout_s: float = 0,   # 0 = no timeout
    ) -> AsyncIterator[str]:
        """Async generator yielding SSE frames for `session_id`.
        Yields heartbeat ':keep-alive' comments automatically.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            if session_id not in self._queues:
                self._queues[session_id] = []
            self._queues[session_id].append(q)
            self._last_active[session_id] = time.time()

        deadline = time.time() + timeout_s if timeout_s else None
        try:
            while True:
                wait = min(self._heartbeat_s, timeout_s) if timeout_s else self._heartbeat_s
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=wait)
                except asyncio.TimeoutError:
                    if deadline and time.time() >= deadline:
                        break
                    yield ":keep-alive\n\n"
                    continue
                if frame is _SENTINEL:
                    break
                yield frame
        finally:
            async with self._lock:
                try:
                    self._queues.get(session_id, []).remove(q)
                except ValueError:
                    pass

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def close_session(self, session_id: str) -> None:
        """Signal all subscribers of `session_id` to stop."""
        async with self._lock:
            queues = self._queues.pop(session_id, [])
            self._last_active.pop(session_id, None)
        for q in queues:
            try:
                q.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass

    def active_sessions(self) -> list:
        return list(self._queues.keys())

    def subscriber_count(self, session_id: str) -> int:
        return len(self._queues.get(session_id, []))

    # ------------------------------------------------------------------
    # SSE wire format
    # ------------------------------------------------------------------

    @staticmethod
    def format_sse(
        event: str,
        data: Any,
        event_id: Optional[str] = None,
    ) -> str:
        """Serialize to SSE wire format string."""
        if not isinstance(data, str):
            try:
                data = json.dumps(data, default=str)
            except Exception:
                data = str(data)
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event}")
        # Multi-line data: prefix each line with 'data: '
        for line in data.splitlines():
            lines.append(f"data: {line}")
        lines.append("")   # trailing newline
        lines.append("")   # blank line to terminate event
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_s)
            async with self._lock:
                sessions = list(self._queues.keys())
            for sid in sessions:
                await self.publish("heartbeat", "ping", session_id=sid)

    async def _gc_loop(self) -> None:
        while True:
            await asyncio.sleep(self._queue_ttl_s / 2)
            now = time.time()
            async with self._lock:
                stale = [
                    sid for sid, ts in self._last_active.items()
                    if now - ts > self._queue_ttl_s and not self._queues.get(sid)
                ]
            for sid in stale:
                await self.close_session(sid)
                logger.debug(f"SSEEventBus: GC removed stale session '{sid}'")
