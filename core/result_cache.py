"""
C101 — Result Cache
TTL-aware in-memory cache for agent results with LRU eviction,
namespacing, and optional async refresh.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0  # seconds; 0 = never expire
    hits: int = 0
    last_accessed: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False
        return time.time() - self.created_at > self.ttl

    def touch(self) -> None:
        self.hits += 1
        self.last_accessed = time.time()


class ResultCache:
    """
    TTL + LRU cache.

    Usage::

        cache = ResultCache(max_size=500, default_ttl=60)
        cache.set("key", value)
        val = cache.get("key")        # None if expired
        val = cache.get_or_set("key", compute_fn)
    """

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0, namespace: str = ""):
        self._store: dict[str, CacheEntry] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.namespace = namespace
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------ #
    #  Core operations                                                     #
    # ------------------------------------------------------------------ #

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}" if self.namespace else key

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        full_key = self._key(key)
        if len(self._store) >= self.max_size and full_key not in self._store:
            self._evict_lru()
        self._store[full_key] = CacheEntry(
            key=full_key,
            value=value,
            ttl=ttl if ttl is not None else self.default_ttl,
        )

    def get(self, key: str, default: Any = None) -> Any:
        full_key = self._key(key)
        entry = self._store.get(full_key)
        if entry is None:
            self._misses += 1
            return default
        if entry.is_expired():
            del self._store[full_key]
            self._misses += 1
            return default
        entry.touch()
        self._hits += 1
        return entry.value

    def get_or_set(self, key: str, fn: Callable[[], Any], ttl: Optional[float] = None) -> Any:
        val = self.get(key)
        if val is None:
            val = fn()
            self.set(key, val, ttl=ttl)
        return val

    async def aget_or_set(
        self, key: str, fn: Callable[[], Any], ttl: Optional[float] = None
    ) -> Any:
        val = self.get(key)
        if val is None:
            if asyncio.iscoroutinefunction(fn):
                val = await fn()
            else:
                val = await asyncio.to_thread(fn)
            self.set(key, val, ttl=ttl)
        return val

    def delete(self, key: str) -> bool:
        full_key = self._key(key)
        if full_key in self._store:
            del self._store[full_key]
            return True
        return False

    def has(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def clear(self) -> None:
        self._store.clear()

    def purge_expired(self) -> int:
        expired = [k for k, e in self._store.items() if e.is_expired()]
        for k in expired:
            del self._store[k]
        return len(expired)

    # ------------------------------------------------------------------ #
    #  Hashing helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def make_key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------ #
    #  Stats                                                               #
    # ------------------------------------------------------------------ #

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
        }

    # ------------------------------------------------------------------ #
    #  LRU eviction                                                        #
    # ------------------------------------------------------------------ #

    def _evict_lru(self) -> None:
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].last_accessed)
        del self._store[lru_key]
        logger.debug("Cache LRU eviction: %s", lru_key)

    def __len__(self) -> int:
        return len(self._store)

    def __repr__(self) -> str:
        return f"ResultCache(size={len(self)}/{self.max_size}, namespace={self.namespace!r})"


_MISSING = object()
