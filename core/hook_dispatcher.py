"""HookDispatcher — Plugin hook registration and sync/async dispatch (C54)."""
from __future__ import annotations
import asyncio, logging, threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

@dataclass
class HandlerEntry:
    hook: str
    handler: Callable
    priority: int
    plugin_name: str
    once: bool
    is_async: bool
    def __lt__(self, other: HandlerEntry) -> bool:
        return self.priority < other.priority

class HookDispatcher:
    """Priority-ordered hook registration and dispatch."""

    def __init__(self):
        self._hooks: Dict[str, List[HandlerEntry]] = {}
        self._lock  = threading.Lock()

    def register(
        self, hook: str, handler: Callable, *,
        priority: int = 50, plugin_name: str = "", once: bool = False,
    ) -> None:
        entry = HandlerEntry(hook=hook, handler=handler, priority=priority,
                             plugin_name=plugin_name, once=once,
                             is_async=asyncio.iscoroutinefunction(handler))
        with self._lock:
            bucket = self._hooks.setdefault(hook, [])
            bucket.append(entry)
            bucket.sort()

    def unregister(self, hook: str, handler: Callable) -> bool:
        with self._lock:
            bucket = self._hooks.get(hook, [])
            before = len(bucket)
            self._hooks[hook] = [e for e in bucket if e.handler is not handler]
            return len(self._hooks[hook]) < before

    def unregister_plugin(self, plugin_name: str) -> int:
        removed = 0
        with self._lock:
            for hook in list(self._hooks):
                before = len(self._hooks[hook])
                self._hooks[hook] = [e for e in self._hooks[hook] if e.plugin_name != plugin_name]
                removed += before - len(self._hooks[hook])
        return removed

    def call(self, hook: str, *args, **kwargs) -> List[Any]:
        entries, once_remove, results = self._get_entries(hook), [], []
        for e in entries:
            if e.is_async: continue
            try: results.append(e.handler(*args, **kwargs))
            except Exception as ex: logger.warning(f"HookDispatcher '{hook}': {ex}")
            if e.once: once_remove.append(e)
        for e in once_remove: self.unregister(hook, e.handler)
        return results

    def filter(self, hook: str, value: Any, **kwargs) -> Any:
        for e in self._get_entries(hook):
            if e.is_async: continue
            try:
                r = e.handler(value, **kwargs)
                if r is not None: value = r
            except Exception as ex: logger.warning(f"HookDispatcher.filter '{hook}': {ex}")
        return value

    async def acall(self, hook: str, *args, **kwargs) -> List[Any]:
        entries, once_remove, results, coros = self._get_entries(hook), [], [], []
        for e in entries:
            if e.is_async: coros.append((e, e.handler(*args, **kwargs)))
            else:
                try: results.append(e.handler(*args, **kwargs))
                except Exception as ex: logger.warning(f"HookDispatcher.acall '{hook}': {ex}")
            if e.once: once_remove.append(e)
        if coros:
            async_results = await asyncio.gather(*[c for _, c in coros], return_exceptions=True)
            for (e, _), res in zip(coros, async_results):
                if isinstance(res, Exception): logger.warning(f"HookDispatcher async '{hook}': {res}")
                else: results.append(res)
        for e in once_remove: self.unregister(hook, e.handler)
        return results

    async def afilter(self, hook: str, value: Any, **kwargs) -> Any:
        for e in self._get_entries(hook):
            try:
                r = await e.handler(value, **kwargs) if e.is_async else e.handler(value, **kwargs)
                if r is not None: value = r
            except Exception as ex: logger.warning(f"HookDispatcher.afilter '{hook}': {ex}")
        return value

    def hooks(self) -> List[str]:
        with self._lock: return [h for h, es in self._hooks.items() if es]

    def handlers(self, hook: str) -> List[HandlerEntry]:
        return list(self._get_entries(hook))

    def _get_entries(self, hook: str) -> List[HandlerEntry]:
        with self._lock: return list(self._hooks.get(hook, []))
