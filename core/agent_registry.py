"""
C102 — Agent Registry
Central registry for discovering and instantiating agent classes
by name, tag, or capability. Supports lazy instantiation and
plugin-style registration via decorators.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Type

from core.agent_base import AgentConfig, BaseAgent

logger = logging.getLogger(__name__)


@dataclass
class AgentEntry:
    name: str
    cls: Type[BaseAgent]
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    default_config: Optional[AgentConfig] = None


class RegistryError(Exception):
    pass


class AgentRegistry:
    """
    Central agent class registry.

    Usage::

        registry = AgentRegistry()

        @registry.register(tags=["search"], capabilities=["web_search"])
        class WebSearchAgent(BaseAgent): ...

        agent = registry.create("WebSearchAgent")
    """

    def __init__(self):
        self._entries: dict[str, AgentEntry] = {}

    # ------------------------------------------------------------------ #
    #  Registration                                                        #
    # ------------------------------------------------------------------ #

    def register(
        self,
        cls: Optional[Type[BaseAgent]] = None,
        *,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
        description: str = "",
        default_config: Optional[AgentConfig] = None,
    ):
        """Decorator or direct call to register an agent class."""
        def _do_register(klass: Type[BaseAgent]) -> Type[BaseAgent]:
            entry_name = name or klass.__name__
            if entry_name in self._entries:
                logger.warning("Registry: overwriting existing entry '%s'", entry_name)
            self._entries[entry_name] = AgentEntry(
                name=entry_name,
                cls=klass,
                tags=tags or [],
                capabilities=capabilities or [],
                description=description or (klass.__doc__ or "").strip().split("\n")[0],
                default_config=default_config,
            )
            logger.debug("Registry: registered '%s'", entry_name)
            return klass

        if cls is not None:
            return _do_register(cls)
        return _do_register

    def register_from_module(
        self,
        module_path: str,
        class_names: Optional[list[str]] = None,
    ) -> int:
        """Import a module and auto-register BaseAgent subclasses."""
        mod = importlib.import_module(module_path)
        count = 0
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            try:
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseAgent)
                    and obj is not BaseAgent
                    and (class_names is None or attr_name in class_names)
                ):
                    self.register(obj)
                    count += 1
            except TypeError:
                pass
        return count

    def unregister(self, name: str) -> None:
        if name not in self._entries:
            raise RegistryError(f"Agent '{name}' not in registry")
        del self._entries[name]

    # ------------------------------------------------------------------ #
    #  Lookup                                                              #
    # ------------------------------------------------------------------ #

    def get_entry(self, name: str) -> AgentEntry:
        if name not in self._entries:
            raise RegistryError(f"Agent '{name}' not registered")
        return self._entries[name]

    def find_by_tag(self, tag: str) -> list[AgentEntry]:
        return [e for e in self._entries.values() if tag in e.tags]

    def find_by_capability(self, capability: str) -> list[AgentEntry]:
        return [e for e in self._entries.values() if capability in e.capabilities]

    def list_names(self) -> list[str]:
        return list(self._entries.keys())

    def list_entries(self) -> list[AgentEntry]:
        return list(self._entries.values())

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------ #
    #  Instantiation                                                       #
    # ------------------------------------------------------------------ #

    def create(
        self,
        name: str,
        config: Optional[AgentConfig] = None,
        **config_overrides: Any,
    ) -> BaseAgent:
        entry = self.get_entry(name)
        base_config = config or entry.default_config or AgentConfig(name=name)
        if config_overrides:
            base_config = AgentConfig(
                **{**vars(base_config), **config_overrides}
            )
        return entry.cls(config=base_config)

    def create_all(
        self,
        tag: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> list[BaseAgent]:
        """Instantiate all matching agents with their default configs."""
        if tag:
            entries = self.find_by_tag(tag)
        elif capability:
            entries = self.find_by_capability(capability)
        else:
            entries = self.list_entries()
        return [self.create(e.name) for e in entries]


# Module-level default registry
registry = AgentRegistry()
