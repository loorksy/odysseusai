"""BlobCache — LRU in-memory blob cache with TTL and size cap (C59).

Caches arbitrary byte blobs keyed by a string identifier.
Used to avoid re-reading large files, re-fetching remote assets, or
re-computing expensive byte outputs (e.g. rendered PDFs, image thumbnails).

Features:
  - LRU eviction when max_entries or max_bytes exceeded
  - Per-entry TTL (entries silently expire on next access)
  - Atomic get-or-set: get(key) or set then return
  - Hit/miss/eviction statistics
  - Optional namespace isolation
  - Thread-safe

Public API:
  cache = BlobCache(max_entries=1000, max_bytes=256*1024*1024, default_ttl_s=300)
  cache.set(key, data, *, ttl_s, namespace)
  data = cache.get(key, *, namespace)          -> bytes | None
  data = cache.get_or_set(key, loader_fn, ...) -> bytes
  cache.delete(key, *, namespace)
  cache.clear(namespace)
  cache.stats()  -> dict
"""
from __future__ import annotations
import logging, threading, time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENTRIES = 1_000
_DEFAULT_MAX_BYTES   = 256 * 1024 * 1024   # 256 MB
_DEFAULT_TTL         = 300.0


@dataclass
class _CacheEntry:
    data:       bytes
    expires_at: Optional[float]
    size:       int = field(init=False)
    def __post_init__(self): self.size = len(self.data)
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at


class BlobCache:
    """LRU byte-blob cache with TTL and byte-budget eviction."""

    def __init__(
        self,
        max_entries:   int   = _DEFAULT_MAX_ENTRIES,
        max_bytes:     int   = _DEFAULT_MAX_BYTES,
        default_ttl_s: float = _DEFAULT_TTL,
    ):
        self._max_entries   = max_entries
        self._max_bytes     = max_bytes
        self._default_ttl   = default_ttl_s
        # namespace -> OrderedDict[key -> _CacheEntry]  (LRU order)
        self._stores: Dict[str, OrderedDict] = {}
        self._total_bytes   = 0
        self._lock          = threading.Lock()
        self._hits = self._misses = self._evictions = 0

    # ------------------------------------------------------------------
    # Set / Get
    # ------------------------------------------------------------------

    def set(
        self,
        key:       str,
        data:      bytes,
        *,
        ttl_s:     Optional[float] = None,
        namespace: str = "default",
    ) -> None:
        ttl   = ttl_s if ttl_s is not None else self._default_ttl
        entry = _CacheEntry(
            data=data,
            expires_at=(time.time() + ttl) if ttl else None,
        )
        with self._lock:
            store = self._stores.setdefault(namespace, OrderedDict())
            if key in store:
                self._total_bytes -= store[key].size
                del store[key]
            store[key] = entry
            self._total_bytes += entry.size
            self._evict_if_needed(store, namespace)

    def get(
        self,
        key:       str,
        *,
        namespace: str = "default",
    ) -> Optional[bytes]:
        with self._lock:
            store = self._stores.get(namespace)
            if not store or key not in store:
                self._misses += 1
                return None
            entry = store[key]
            if entry.expired():
                self._total_bytes -= entry.size
                del store[key]
                self._misses += 1
                return None
            # LRU: move to end
            store.move_to_end(key)
            self._hits += 1
            return entry.data

    def get_or_set(
        self,
        key:        str,
        loader_fn:  Callable[[], bytes],
        *,
        ttl_s:      Optional[float] = None,
        namespace:  str = "default",
    ) -> bytes:
        data = self.get(key, namespace=namespace)
        if data is not None:
            return data
        data = loader_fn()
        self.set(key, data, ttl_s=ttl_s, namespace=namespace)
        return data

    def delete(
        self,
        key:       str,
        *,
        namespace: str = "default",
    ) -> bool:
        with self._lock:
            store = self._stores.get(namespace)
            if store and key in store:
                self._total_bytes -= store[key].size
                del store[key]
                return True
        return False

    def clear(self, namespace: str = "default") -> int:
        with self._lock:
            store = self._stores.pop(namespace, {})
            freed = sum(e.size for e in store.values())
            self._total_bytes -= freed
        return len(store)

    def stats(self) -> Dict:
        with self._lock:
            total_entries = sum(len(s) for s in self._stores.values())
        return {
            "entries":    total_entries,
            "bytes":      self._total_bytes,
            "hits":       self._hits,
            "misses":     self._misses,
            "evictions":  self._evictions,
            "namespaces": list(self._stores.keys()),
        }

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def _evict_if_needed(self, store: OrderedDict, namespace: str) -> None:
        # Evict expired first
        expired_keys = [k for k, e in store.items() if e.expired()]
        for k in expired_keys:
            self._total_bytes -= store[k].size
            del store[k]
            self._evictions += 1

        # Evict LRU by count
        all_entries = sum(len(s) for s in self._stores.values())
        while all_entries > self._max_entries and store:
            k, e = store.popitem(last=False)
            self._total_bytes -= e.size
            self._evictions   += 1
            all_entries       -= 1

        # Evict LRU by bytes
        while self._total_bytes > self._max_bytes and store:
            k, e = store.popitem(last=False)
            self._total_bytes -= e.size
            self._evictions   += 1
