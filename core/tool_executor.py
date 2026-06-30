"""
C123 — Tool Executor
Registry and dispatcher for all tools available to agents.
Tools are registered with a name, callable, schema, and optional
feature requirement. The executor validates inputs against the schema,
enforces feature gating, emits monitor events, and returns structured results.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.agent_monitor import AgentMonitor, EventKind
from core.tier_config import TierConfig, feature_enabled

logger = logging.getLogger(__name__)


@dataclass
class ToolSchema:
    required: list[str] = field(default_factory=list)
    types: dict[str, type] = field(default_factory=dict)

    def validate(self, kwargs: dict) -> list[str]:
        errors = []
        for key in self.required:
            if key not in kwargs:
                errors.append(f"Missing required param: '{key}'")
        for key, expected_type in self.types.items():
            if key in kwargs and not isinstance(kwargs[key], expected_type):
                errors.append(
                    f"Param '{key}' expected {expected_type.__name__}, "
                    f"got {type(kwargs[key]).__name__}"
                )
        return errors


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: Any = None
    error: str = ""
    elapsed_ms: float = 0.0

    def __repr__(self) -> str:
        if self.success:
            return f"<ToolResult {self.tool_name} OK ({self.elapsed_ms:.0f}ms)>"
        return f"<ToolResult {self.tool_name} ERROR: {self.error}>"


@dataclass
class RegisteredTool:
    name: str
    fn: Callable
    schema: ToolSchema
    description: str = ""
    required_feature: str = ""
    timeout_s: float = 30.0


class ToolExecutor:
    def __init__(self, monitor: AgentMonitor, tier_cfg: Optional[TierConfig] = None):
        self.monitor = monitor
        self.tier_cfg = tier_cfg
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        schema: Optional[ToolSchema] = None,
        description: str = "",
        required_feature: str = "",
        timeout_s: float = 30.0,
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = RegisteredTool(
                name=name, fn=fn, schema=schema or ToolSchema(),
                description=description, required_feature=required_feature,
                timeout_s=timeout_s,
            )
            return fn
        return decorator

    def register_fn(
        self, name: str, fn: Callable,
        schema: Optional[ToolSchema] = None,
        description: str = "", required_feature: str = "",
    ) -> None:
        self._tools[name] = RegisteredTool(
            name=name, fn=fn, schema=schema or ToolSchema(),
            description=description, required_feature=required_feature,
        )

    def call(self, tool_name: str, agent_id: str = "unknown", **kwargs: Any) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            err = f"Tool not found: '{tool_name}'"
            self.monitor.emit(agent_id, EventKind.ERROR, err)
            return ToolResult(tool_name=tool_name, success=False, error=err)

        if tool.required_feature and self.tier_cfg:
            if not feature_enabled(self.tier_cfg, tool.required_feature):
                err = (f"Tool '{tool_name}' requires feature '{tool.required_feature}' "
                       f"(not available on tier '{self.tier_cfg.tier}')")
                self.monitor.emit(agent_id, EventKind.WARNING, err)
                return ToolResult(tool_name=tool_name, success=False, error=err)

        errors = tool.schema.validate(kwargs)
        if errors:
            err = "; ".join(errors)
            self.monitor.emit(agent_id, EventKind.ERROR, f"Schema error for '{tool_name}': {err}")
            return ToolResult(tool_name=tool_name, success=False, error=err)

        self.monitor.emit(agent_id, EventKind.TOOL_CALL, f"{tool_name}({list(kwargs.keys())})")
        self.monitor.set_last_tool(agent_id, tool_name)

        t0 = time.perf_counter()
        try:
            output = tool.fn(**kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            self.monitor.emit(agent_id, EventKind.TOOL_RESULT, f"{tool_name} -> {str(output)[:80]}")
            return ToolResult(tool_name=tool_name, success=True, output=output, elapsed_ms=elapsed)
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.error("Tool '%s' raised: %s", tool_name, e, exc_info=True)
            self.monitor.emit(agent_id, EventKind.ERROR, f"{tool_name} error: {e}")
            return ToolResult(tool_name=tool_name, success=False, error=str(e), elapsed_ms=elapsed)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def available_tools(self) -> list[str]:
        out = []
        for name, tool in self._tools.items():
            if tool.required_feature and self.tier_cfg:
                if not feature_enabled(self.tier_cfg, tool.required_feature):
                    continue
            out.append(name)
        return out
