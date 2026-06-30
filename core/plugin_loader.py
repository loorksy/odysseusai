"""
C119 — Plugin Loader
Discovers, validates, and loads ShadowRealm plugins from a plugins/
directory. Each plugin is a Python module exposing a PLUGIN_META dict
and a register(context) function. Feature-flag gating ensures plugins
that require excluded features are silently skipped on low-tier hardware.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from core.tier_config import TierConfig, feature_enabled

logger = logging.getLogger(__name__)

PLUGINS_DIR = Path(__file__).parent.parent / "plugins"


@dataclass
class PluginMeta:
    name: str
    version: str = "0.1.0"
    description: str = ""
    required_features: list[str] = field(default_factory=list)
    author: str = ""
    enabled: bool = True


@dataclass
class LoadedPlugin:
    meta: PluginMeta
    module: Any
    register_fn: Callable
    path: str
    active: bool = False


class PluginLoader:
    """
    Scan plugins/ for modules, validate required features against
    the current TierConfig, and call register(context) for each
    compatible plugin.

    Usage::

        loader = PluginLoader(tier_cfg)
        loader.discover()
        loader.load_all(context={"monitor": monitor, "session": session})

        # List what loaded vs. skipped:
        for p in loader.loaded:
            print(p.meta.name, "active:", p.active)
    """

    def __init__(self, tier_cfg: TierConfig, plugins_dir: Path = PLUGINS_DIR):
        self.tier_cfg = tier_cfg
        self.plugins_dir = plugins_dir
        self.discovered: list[Path] = []
        self.loaded: list[LoadedPlugin] = []
        self.skipped: list[tuple[str, str]] = []  # (name, reason)

    def discover(self) -> list[Path]:
        if not self.plugins_dir.exists():
            logger.debug("Plugins dir not found: %s", self.plugins_dir)
            return []
        self.discovered = sorted(self.plugins_dir.glob("*.py"))
        logger.info("Discovered %d plugin(s)", len(self.discovered))
        return self.discovered

    def load_all(self, context: Optional[dict] = None) -> int:
        context = context or {}
        loaded_count = 0
        for path in self.discovered:
            plugin = self._load_one(path)
            if plugin is None:
                continue
            # Feature gate
            missing = [
                f for f in plugin.meta.required_features
                if not feature_enabled(self.tier_cfg, f)
            ]
            if missing:
                reason = f"missing features: {', '.join(missing)}"
                logger.info("Skipping plugin '%s': %s", plugin.meta.name, reason)
                self.skipped.append((plugin.meta.name, reason))
                continue
            if not plugin.meta.enabled:
                self.skipped.append((plugin.meta.name, "disabled in meta"))
                continue
            try:
                plugin.register_fn(context)
                plugin.active = True
                self.loaded.append(plugin)
                loaded_count += 1
                logger.info("Plugin loaded: %s v%s", plugin.meta.name, plugin.meta.version)
            except Exception as e:
                logger.error("Plugin '%s' register() failed: %s", plugin.meta.name, e)
                self.skipped.append((plugin.meta.name, str(e)))
        return loaded_count

    def _load_one(self, path: Path) -> Optional[LoadedPlugin]:
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[path.stem] = module
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning("Could not import plugin %s: %s", path.name, e)
            return None

        raw_meta = getattr(module, "PLUGIN_META", None)
        register_fn = getattr(module, "register", None)
        if raw_meta is None or register_fn is None:
            logger.warning("Plugin %s missing PLUGIN_META or register()", path.name)
            return None

        if isinstance(raw_meta, dict):
            meta = PluginMeta(**{k: v for k, v in raw_meta.items() if k in PluginMeta.__dataclass_fields__})
        else:
            meta = raw_meta

        return LoadedPlugin(meta=meta, module=module, register_fn=register_fn, path=str(path))

    def summary(self) -> str:
        lines = [f"Plugins loaded: {len(self.loaded)}  skipped: {len(self.skipped)}"]
        for p in self.loaded:
            lines.append(f"  ✅ {p.meta.name} v{p.meta.version}")
        for name, reason in self.skipped:
            lines.append(f"  ⏭️  {name}  ({reason})")
        return "\n".join(lines)
