"""
C99 — Context Manager
Thread-safe scoped context for passing data across agent components
without explicit argument threading. Supports nested scopes and
read-only snapshots.
"""
from __future__ import annotations

import copy
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class ContextSnapshot:
    data: dict
    taken_at: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class ContextKeyError(KeyError):
    pass


class AgentContext:
    """
    Thread-local scoped context store.

    Usage::

        ctx = AgentContext()
        ctx.set("run_id", "abc123")

        with ctx.scope({"user": "alice"}):
            print(ctx.get("user"))   # alice
            print(ctx.get("run_id")) # abc123  (inherited)
        print(ctx.get("user", None)) # None (scope exited)
    """

    def __init__(self, defaults: Optional[dict] = None):
        self._local = threading.local()
        self._global: dict = dict(defaults or {})
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    #  Core get/set                                                        #
    # ------------------------------------------------------------------ #

    def set(self, key: str, value: Any) -> None:
        """Set a value in the current scope (or global if no scope active)."""
        stack = self._stack()
        if stack:
            stack[-1][key] = value
        else:
            with self._lock:
                self._global[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value, searching from innermost scope outward."""
        for scope in reversed(self._stack()):
            if key in scope:
                return scope[key]
        with self._lock:
            return self._global.get(key, default)

    def require(self, key: str) -> Any:
        """Like get() but raises ContextKeyError if missing."""
        val = self.get(key, _MISSING)
        if val is _MISSING:
            raise ContextKeyError(f"Required context key missing: '{key}'")
        return val

    def delete(self, key: str) -> None:
        stack = self._stack()
        if stack and key in stack[-1]:
            del stack[-1][key]
            return
        with self._lock:
            self._global.pop(key, None)

    def update(self, mapping: dict) -> None:
        for k, v in mapping.items():
            self.set(k, v)

    def has(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    # ------------------------------------------------------------------ #
    #  Scoping                                                             #
    # ------------------------------------------------------------------ #

    @contextmanager
    def scope(self, initial: Optional[dict] = None) -> Iterator["AgentContext"]:
        """Push a new scope; all sets within go to this scope layer."""
        layer = dict(initial or {})
        self._stack().append(layer)
        try:
            yield self
        finally:
            self._stack().pop()

    def push(self, initial: Optional[dict] = None) -> None:
        """Manually push a scope layer (pair with pop())."""
        self._stack().append(dict(initial or {}))

    def pop(self) -> dict:
        """Manually pop the innermost scope layer."""
        stack = self._stack()
        if not stack:
            raise ContextKeyError("No scope to pop")
        return stack.pop()

    def depth(self) -> int:
        return len(self._stack())

    # ------------------------------------------------------------------ #
    #  Snapshot / export                                                   #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> ContextSnapshot:
        """Return a deep-copy snapshot of the merged context."""
        merged: dict = {}
        with self._lock:
            merged.update(copy.deepcopy(self._global))
        for layer in self._stack():
            merged.update(copy.deepcopy(layer))
        return ContextSnapshot(data=merged)

    def to_dict(self) -> dict:
        return self.snapshot().data

    def clear(self, global_only: bool = False) -> None:
        with self._lock:
            self._global.clear()
        if not global_only:
            stack = self._stack()
            stack.clear()

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _stack(self) -> list[dict]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    def __repr__(self) -> str:
        return f"AgentContext(depth={self.depth()}, keys={list(self.to_dict().keys())})"


_MISSING = object()

# Module-level default context
context = AgentContext()
