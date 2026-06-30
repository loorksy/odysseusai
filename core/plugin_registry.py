"""
C85 · plugin_registry.py
Versioned plugin manifest store with dependency resolver.

Responsibilities
----------------
* Register plugins with semantic-versioned manifests.
* Resolve load order via topological sort of declared dependencies.
* Detect version conflicts and circular dependency chains.
* Enable / disable plugins at runtime without restart.
* Emit lifecycle events (registered, enabled, disabled, removed) via EventBus (C4).

Design constraints
------------------
* stdlib only — no external packages.
* Thread-safe: all mutations guarded by RLock.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[\w.]+))?(?:\+(?P<build>[\w.]+))?$"
)


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: str = ""

    @classmethod
    def parse(cls, s: str) -> "Version":
        m = _SEMVER_RE.match(s.strip())
        if not m:
            raise ValueError(f"Invalid semver: {s!r}")
        return cls(
            major=int(m.group("major")),
            minor=int(m.group("minor")),
            patch=int(m.group("patch")),
            pre=m.group("pre") or "",
        )

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.pre}" if self.pre else base

    def compatible_with(self, required: "Version") -> bool:
        """True when self satisfies ^required (same major, >= minor.patch)."""
        if self.major != required.major:
            return False
        if self.minor != required.minor:
            return self.minor > required.minor
        return self.patch >= required.patch


# ---------------------------------------------------------------------------
# Manifest & state
# ---------------------------------------------------------------------------

class PluginState(Enum):
    REGISTERED = "registered"
    ENABLED = "enabled"
    DISABLED = "disabled"
    REMOVED = "removed"


@dataclass
class PluginManifest:
    """Declarative description of a plugin."""
    name: str
    version: Version
    entry_point: str                        # dotted module path
    description: str = ""
    author: str = ""
    requires: Dict[str, Version] = field(default_factory=dict)  # name -> min version
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PluginManifest":
        requires = {
            k: Version.parse(v) for k, v in d.get("requires", {}).items()
        }
        return cls(
            name=d["name"],
            version=Version.parse(d["version"]),
            entry_point=d["entry_point"],
            description=d.get("description", ""),
            author=d.get("author", ""),
            requires=requires,
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": str(self.version),
            "entry_point": self.entry_point,
            "description": self.description,
            "author": self.author,
            "requires": {k: str(v) for k, v in self.requires.items()},
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class PluginRecord:
    manifest: PluginManifest
    state: PluginState = PluginState.REGISTERED
    registered_at: float = field(default_factory=time.time)
    enabled_at: Optional[float] = None
    disabled_at: Optional[float] = None
    instance: object = None  # loaded object set by extension_loader


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PluginRegistryError(Exception):
    pass

class DuplicatePluginError(PluginRegistryError):
    pass

class PluginNotFoundError(PluginRegistryError):
    pass

class VersionConflictError(PluginRegistryError):
    pass

class CircularDependencyError(PluginRegistryError):
    pass


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class PluginRegistry:
    """
    Central store for plugin manifests.

    Usage
    -----
    registry = PluginRegistry()
    registry.register(PluginManifest.from_dict({...}))
    order = registry.resolve_load_order()   # topologically sorted names
    registry.enable("my_plugin")
    """

    def __init__(self, event_bus=None) -> None:
        self._plugins: Dict[str, PluginRecord] = {}
        self._lock = threading.RLock()
        self._bus = event_bus  # optional C4 EventBus

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, manifest: PluginManifest) -> PluginRecord:
        """Register a new plugin. Raises DuplicatePluginError if already present."""
        with self._lock:
            if manifest.name in self._plugins:
                existing = self._plugins[manifest.name]
                if existing.state != PluginState.REMOVED:
                    raise DuplicatePluginError(
                        f"Plugin {manifest.name!r} already registered "
                        f"(version {existing.manifest.version})."
                    )
            record = PluginRecord(manifest=manifest)
            self._plugins[manifest.name] = record
            self._emit("plugin.registered", {"name": manifest.name, "version": str(manifest.version)})
            return record

    def update(self, manifest: PluginManifest) -> PluginRecord:
        """Replace an existing plugin's manifest (e.g. after hot-reload)."""
        with self._lock:
            if manifest.name not in self._plugins:
                raise PluginNotFoundError(manifest.name)
            old = self._plugins[manifest.name]
            record = PluginRecord(
                manifest=manifest,
                state=old.state,
                registered_at=old.registered_at,
                instance=old.instance,
            )
            self._plugins[manifest.name] = record
            self._emit("plugin.updated", {"name": manifest.name, "version": str(manifest.version)})
            return record

    def remove(self, name: str) -> None:
        """Mark plugin as removed (soft delete)."""
        with self._lock:
            record = self._get_or_raise(name)
            record.state = PluginState.REMOVED
            record.instance = None
            self._emit("plugin.removed", {"name": name})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def enable(self, name: str) -> None:
        with self._lock:
            record = self._get_or_raise(name)
            record.state = PluginState.ENABLED
            record.enabled_at = time.time()
            self._emit("plugin.enabled", {"name": name})

    def disable(self, name: str) -> None:
        with self._lock:
            record = self._get_or_raise(name)
            record.state = PluginState.DISABLED
            record.disabled_at = time.time()
            self._emit("plugin.disabled", {"name": name})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> PluginRecord:
        with self._lock:
            return self._get_or_raise(name)

    def list_all(self, state: Optional[PluginState] = None) -> List[PluginRecord]:
        with self._lock:
            records = list(self._plugins.values())
            if state:
                records = [r for r in records if r.state == state]
            return records

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            if name not in self._plugins:
                return False
            return self._plugins[name].state == PluginState.ENABLED

    def set_instance(self, name: str, instance: object) -> None:
        """Called by extension_loader (C87) after loading."""
        with self._lock:
            self._get_or_raise(name).instance = instance

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def validate_dependencies(self) -> List[str]:
        """
        Check all registered (non-removed) plugins satisfy their declared
        dependencies.  Returns list of error strings (empty = all OK).
        """
        with self._lock:
            errors: List[str] = []
            active = {
                n: r for n, r in self._plugins.items()
                if r.state != PluginState.REMOVED
            }
            for name, record in active.items():
                for dep_name, min_ver in record.manifest.requires.items():
                    if dep_name not in active:
                        errors.append(
                            f"{name} requires {dep_name} which is not registered."
                        )
                    else:
                        dep_ver = active[dep_name].manifest.version
                        if not dep_ver.compatible_with(min_ver):
                            errors.append(
                                f"{name} requires {dep_name}>={min_ver} "
                                f"but found {dep_ver}."
                            )
            return errors

    def resolve_load_order(self) -> List[str]:
        """
        Topological sort of non-removed plugins by declared dependencies.
        Raises CircularDependencyError on cycles.
        Returns ordered list of plugin names (dependencies first).
        """
        with self._lock:
            active = [
                n for n, r in self._plugins.items()
                if r.state != PluginState.REMOVED
            ]
            deps: Dict[str, Set[str]] = {
                n: set(self._plugins[n].manifest.requires.keys()) & set(active)
                for n in active
            }
            return self._topo_sort(active, deps)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_raise(self, name: str) -> PluginRecord:
        if name not in self._plugins:
            raise PluginNotFoundError(f"Plugin {name!r} not found.")
        return self._plugins[name]

    @staticmethod
    def _topo_sort(nodes: List[str], deps: Dict[str, Set[str]]) -> List[str]:
        """Kahn's algorithm."""
        in_degree = {n: 0 for n in nodes}
        for n in nodes:
            for dep in deps.get(n, set()):
                in_degree[n] = in_degree.get(n, 0)  # dep already counted below
        # rebuild properly
        in_degree = {n: 0 for n in nodes}
        reverse: Dict[str, List[str]] = {n: [] for n in nodes}
        for n in nodes:
            for dep in deps.get(n, set()):
                if dep in reverse:
                    reverse[dep].append(n)
                    in_degree[n] += 1

        queue = [n for n in nodes if in_degree[n] == 0]
        queue.sort()  # deterministic
        result: List[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in sorted(reverse[node]):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(nodes):
            remaining = set(nodes) - set(result)
            raise CircularDependencyError(
                f"Circular dependency detected among: {sorted(remaining)}"
            )
        return result

    def _emit(self, event: str, payload: dict) -> None:
        if self._bus is not None:
            try:
                self._bus.publish(event, payload)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            counts = {s: 0 for s in PluginState}
            for r in self._plugins.values():
                counts[r.state] += 1
            return (
                f"<PluginRegistry total={len(self._plugins)} "
                + " ".join(f"{s.value}={counts[s]}" for s in PluginState)
                + ">"
            )
