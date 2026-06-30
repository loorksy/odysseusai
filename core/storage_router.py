"""StorageRouter — Backend-agnostic storage dispatch (C60).

Routes storage operations to the right backend based on namespace,
file size, or content type:
  local    -> FileStoreManager (default)
  cache    -> BlobCache (hot path)
  remote   -> pluggable adapter (S3, GCS, Azure Blob — adapters registered at runtime)

The router provides a single unified API so callers don’t need to know
where data actually lives.  Routing rules are evaluated in order;
the first matching rule wins.

Routing rule fields:
  namespace  str | None   match namespace prefix (None = wildcard)
  max_bytes  int | None   route to remote if size > threshold
  backend    str          "local" | "cache" | "remote:<name>"

Public API:
  router = StorageRouter(file_store, blob_cache)
  router.register_remote(name, adapter)        # adapter: {write, read, delete}
  router.add_rule(namespace, backend, *, max_bytes)
  path = router.write(name, data, *, owner, namespace, ttl_s)
  data = router.read(name, *, owner, namespace)
  ok   = router.delete(name, *, owner, namespace)
  router.backends()  -> list[str]
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class RoutingRule:
    namespace: Optional[str]   # None = wildcard
    backend:   str             # "local" | "cache" | "remote:<name>"
    max_bytes: Optional[int] = None


class StorageRouter:
    """Routes read/write/delete to local, cache, or remote backends."""

    def __init__(self, file_store=None, blob_cache=None):
        self._fs      = file_store
        self._cache   = blob_cache
        self._remotes: Dict[str, Any] = {}
        self._rules:   List[RoutingRule] = [
            RoutingRule(namespace=None, backend="local"),  # default
        ]

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def register_remote(self, name: str, adapter: Any) -> None:
        """Register a remote backend adapter.
        Adapter must expose: write(name, data, **kw), read(name, **kw), delete(name, **kw)
        """
        self._remotes[name] = adapter
        logger.info(f"StorageRouter: registered remote backend '{name}'")

    def add_rule(
        self,
        namespace: Optional[str],
        backend:   str,
        *,
        max_bytes: Optional[int] = None,
        prepend:   bool = True,
    ) -> None:
        rule = RoutingRule(namespace=namespace, backend=backend, max_bytes=max_bytes)
        if prepend:
            self._rules.insert(0, rule)
        else:
            self._rules.append(rule)

    # ------------------------------------------------------------------
    # Unified API
    # ------------------------------------------------------------------

    def write(
        self,
        name:      str,
        data:      Union[bytes, str],
        *,
        owner:     str = "default",
        namespace: str = "default",
        ttl_s:     Optional[float] = None,
    ) -> str:
        raw = data.encode() if isinstance(data, str) else data
        backend = self._resolve(namespace, len(raw))
        if backend == "cache" and self._cache:
            self._cache.set(name, raw, ttl_s=ttl_s, namespace=namespace)
            return f"cache://{namespace}/{name}"
        elif backend.startswith("remote:"):
            rname = backend.split(":", 1)[1]
            adapter = self._remotes.get(rname)
            if adapter:
                return adapter.write(name, raw, owner=owner, namespace=namespace, ttl_s=ttl_s)
            logger.warning(f"StorageRouter: remote '{rname}' not registered, falling back to local")
        if self._fs:
            return self._fs.write(name, raw, owner=owner, namespace=namespace, ttl_s=ttl_s)
        raise RuntimeError("StorageRouter: no local FileStoreManager configured")

    def read(
        self,
        name:      str,
        *,
        owner:     str = "default",
        namespace: str = "default",
    ) -> Optional[bytes]:
        backend = self._resolve(namespace)
        if backend == "cache" and self._cache:
            data = self._cache.get(name, namespace=namespace)
            if data is not None:
                return data
        elif backend.startswith("remote:"):
            rname   = backend.split(":", 1)[1]
            adapter = self._remotes.get(rname)
            if adapter:
                return adapter.read(name, owner=owner, namespace=namespace)
        if self._fs:
            return self._fs.read(name, owner=owner, namespace=namespace)
        return None

    def delete(
        self,
        name:      str,
        *,
        owner:     str = "default",
        namespace: str = "default",
    ) -> bool:
        deleted = False
        if self._cache:
            deleted |= self._cache.delete(name, namespace=namespace)
        backend = self._resolve(namespace)
        if backend.startswith("remote:"):
            rname   = backend.split(":", 1)[1]
            adapter = self._remotes.get(rname)
            if adapter:
                deleted |= bool(adapter.delete(name, owner=owner, namespace=namespace))
        if self._fs:
            deleted |= self._fs.delete(name, owner=owner, namespace=namespace)
        return deleted

    def backends(self) -> List[str]:
        available = ["local"] if self._fs else []
        if self._cache: available.append("cache")
        available.extend(f"remote:{n}" for n in self._remotes)
        return available

    # ------------------------------------------------------------------
    # Rule resolution
    # ------------------------------------------------------------------

    def _resolve(self, namespace: str, size: int = 0) -> str:
        for rule in self._rules:
            ns_match   = rule.namespace is None or namespace.startswith(rule.namespace)
            size_match = rule.max_bytes is None or size <= rule.max_bytes
            if ns_match and size_match:
                return rule.backend
        return "local"
