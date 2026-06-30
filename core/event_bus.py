"""
C96 — Event Bus
Lightweight pub/sub event system for decoupled agent communication.
Supports sync and async handlers, wildcards, and one-time listeners.
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Event:
    name: str
    data: Any = None
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class Subscription:
    pattern: str
    handler: Callable
    once: bool = False
    async_handler: bool = False
    sub_id: str = ""


class EventBus:
    """
    Global or instance-level event bus with fnmatch wildcard support.
    """

    def __init__(self):
        self._subscriptions: list[Subscription] = []
        self._history: list[Event] = []
        self._max_history: int = 500
        self._counter: int = 0

    def on(
        self,
        pattern: str,
        handler: Optional[Callable] = None,
        once: bool = False,
    ) -> Callable:
        def _register(fn: Callable) -> Callable:
            self._counter += 1
            is_async = asyncio.iscoroutinefunction(fn)
            sub = Subscription(
                pattern=pattern,
                handler=fn,
                once=once,
                async_handler=is_async,
                sub_id=f"sub_{self._counter}",
            )
            self._subscriptions.append(sub)
            return fn
        if handler is not None:
            return _register(handler)
        return _register

    def once(self, pattern: str) -> Callable:
        return self.on(pattern, once=True)

    def off(self, handler: Callable) -> int:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.handler is not handler]
        return before - len(self._subscriptions)

    def off_id(self, sub_id: str) -> bool:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.sub_id != sub_id]
        return len(self._subscriptions) < before

    def clear(self, pattern: Optional[str] = None) -> None:
        if pattern:
            self._subscriptions = [s for s in self._subscriptions if s.pattern != pattern]
        else:
            self._subscriptions = []

    def emit(self, name: str, data: Any = None, source: str = "", **metadata) -> int:
        event = Event(name=name, data=data, source=source, metadata=metadata)
        self._record(event)
        called = 0
        to_remove = []
        for sub in list(self._subscriptions):
            if not self._matches(sub.pattern, name):
                continue
            try:
                if sub.async_handler:
                    asyncio.run(sub.handler(event))
                else:
                    sub.handler(event)
                called += 1
            except Exception as e:
                logger.error("Event handler error [%s -> %s]: %s", name, sub.pattern, e)
            if sub.once:
                to_remove.append(sub)
        for sub in to_remove:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)
        return called

    async def aemit(self, name: str, data: Any = None, source: str = "", **metadata) -> int:
        event = Event(name=name, data=data, source=source, metadata=metadata)
        self._record(event)
        called = 0
        to_remove = []
        for sub in list(self._subscriptions):
            if not self._matches(sub.pattern, name):
                continue
            try:
                if sub.async_handler:
                    await sub.handler(event)
                else:
                    sub.handler(event)
                called += 1
            except Exception as e:
                logger.error("Async event handler error [%s -> %s]: %s", name, sub.pattern, e)
            if sub.once:
                to_remove.append(sub)
        for sub in to_remove:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)
        return called

    def history(self, pattern: Optional[str] = None, limit: int = 50) -> list[Event]:
        events = self._history
        if pattern:
            events = [e for e in events if self._matches(pattern, e.name)]
        return events[-limit:]

    def listener_count(self, pattern: Optional[str] = None) -> int:
        if pattern:
            return sum(1 for s in self._subscriptions if self._matches(s.pattern, pattern))
        return len(self._subscriptions)

    def _matches(self, pattern: str, name: str) -> bool:
        return fnmatch.fnmatch(name, pattern)

    def _record(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]


bus = EventBus()
