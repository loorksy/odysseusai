"""ExtensionLoader — Hot-reload extensions without restart (C87).

Loads Python extension modules from a configured directory.
Supports hot-reload: re-imports changed files without restarting
the process. Integrates with PluginRegistry for manifest tracking
and EventBus for lifecycle events.

Lifecycle events emitted on EventBus (if provided):
  extension.loaded    {name, path, version}
  extension.reloaded  {name, path, version}
  extension.unloaded  {name}
  extension.error     {name, error}

Features:
  - Load all .py files from an extension directory
  - Watch for file mtime changes and hot-reload on next tick
  - Manual reload(name) for on-demand refresh
  - Unload: remove from sys.modules + internal registry
  - load_all() / reload_all() for bulk operations
  - Thread-safe module tracking

Public API:
  el = ExtensionLoader(extension_dir, registry=None, event_bus=None)
  el.load(name)       -> module | None
  el.reload(name)     -> module | None
  el.unload(name)     -> bool
  el.load_all()       -> list[str]     # loaded names
  el.reload_changed() -> list[str]     # reloaded names
  el.list()           -> list[dict]    # {name, path, mtime, loaded}
"""
from __future__ import annotations
import importlib.util, logging, sys, threading, time
from pathlib import Path
from types  import ModuleType
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ExtensionLoader:
    """Hot-reload Python extensions from a directory."""

    def __init__(
        self,
        extension_dir: str,
        registry   = None,   # PluginRegistry (optional)
        event_bus  = None,   # EventBus (optional)
    ):
        self._dir      = Path(extension_dir)
        self._registry = registry
        self._bus      = event_bus
        self._modules: Dict[str, Dict] = {}   # name -> {module, path, mtime}
        self._lock     = threading.Lock()

    # ------------------------------------------------------------------
    # Load / Unload
    # ------------------------------------------------------------------

    def load(self, name: str) -> Optional[ModuleType]:
        path = self._dir / f"{name}.py"
        if not path.exists():
            logger.warning(f"ExtensionLoader: '{path}' not found")
            return None
        return self._load_path(name, path)

    def reload(self, name: str) -> Optional[ModuleType]:
        with self._lock:
            entry = self._modules.get(name)
        if not entry:
            return self.load(name)
        return self._load_path(name, Path(entry["path"]), is_reload=True)

    def unload(self, name: str) -> bool:
        with self._lock:
            entry = self._modules.pop(name, None)
        if not entry:
            return False
        module_name = entry["module"].__name__
        sys.modules.pop(module_name, None)
        if self._bus:
            self._bus.emit("extension.unloaded", {"name": name})
        logger.info(f"ExtensionLoader: unloaded '{name}'")
        return True

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def load_all(self) -> List[str]:
        loaded = []
        for path in sorted(self._dir.glob("*.py")):
            name = path.stem
            if name.startswith("_"):
                continue
            if self._load_path(name, path):
                loaded.append(name)
        return loaded

    def reload_changed(self) -> List[str]:
        """Reload any extension whose file mtime has changed."""
        reloaded = []
        with self._lock:
            snapshot = dict(self._modules)
        for name, entry in snapshot.items():
            try:
                current_mtime = Path(entry["path"]).stat().st_mtime
            except FileNotFoundError:
                self.unload(name)
                continue
            if current_mtime != entry["mtime"]:
                if self._load_path(name, Path(entry["path"]), is_reload=True):
                    reloaded.append(name)
        return reloaded

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list(self) -> List[Dict]:
        with self._lock:
            return [
                {
                    "name":   name,
                    "path":   e["path"],
                    "mtime":  e["mtime"],
                    "loaded": True,
                }
                for name, e in self._modules.items()
            ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_path(
        self, name: str, path: Path, *, is_reload: bool = False
    ) -> Optional[ModuleType]:
        module_name = f"shadowrealm.ext.{name}"
        try:
            spec   = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            # Remove stale module if reloading
            sys.modules.pop(module_name, None)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            mtime = path.stat().st_mtime
            with self._lock:
                self._modules[name] = {
                    "module": module, "path": str(path), "mtime": mtime
                }
            # Register manifest if module exposes __manifest__
            if self._registry and hasattr(module, "__manifest__"):
                self._registry.register(module.__manifest__)
            # Emit lifecycle event
            event = "extension.reloaded" if is_reload else "extension.loaded"
            if self._bus:
                self._bus.emit(event, {
                    "name": name, "path": str(path),
                    "version": getattr(module, "__version__", "0.0.0"),
                })
            action = "reloaded" if is_reload else "loaded"
            logger.info(
                f"ExtensionLoader: {action} '{name}' "
                f"v{getattr(module, '__version__', '?')} from {path}"
            )
            return module
        except Exception as exc:
            logger.error(f"ExtensionLoader: failed to load '{name}': {exc}")
            if self._bus:
                self._bus.emit("extension.error", {"name": name, "error": str(exc)})
            return None
