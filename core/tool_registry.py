"""
core/tool_registry.py
C89 — Tool Registry: OpenAI function-calling spec tool definitions

Provides:
 - A central registry for all callable tools exposed to the LLM layer.
 - Generates OpenAI-compatible function-calling JSON schemas on demand.
 - Handles tool dispatch (name -> callable) for the ReAct execution loop (C123).
 - Zero external dependencies — stdlib only (json, inspect, typing).

Design Rules (Architecture Invariant #1: core/ = stdlib only):
 - No FastAPI, no Pydantic, no requests. Pure Python.
 - Tool schemas are generated from Python type annotations + docstrings.
 - Compatible with OpenAI tools[], Anthropic tool_choice, and Ollama tool-call.

Integration Points:
 - C88 (llm_client.py)   : passes registry.get_schemas() as `tools` param in LLM calls.
 - C123 (reasoning_engine.py): calls registry.dispatch(name, args) in the Action step.
 - C111 (pantheon_router.py) : introspects registered tools to score task-to-agent fit.
"""

import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional, get_type_hints

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type mapping: Python annotations -> JSON Schema types
# ---------------------------------------------------------------------------
_PYTHON_TYPE_TO_JSON_SCHEMA: Dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    None.__class__: "null",  # type: ignore[type-arg]
}


def _python_type_to_json(annotation: Any) -> str:
    """Convert a Python type annotation to a JSON Schema type string."""
    if annotation is inspect.Parameter.empty:
        return "string"  # Default fallback
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return _PYTHON_TYPE_TO_JSON_SCHEMA.get(annotation, "string")


# ---------------------------------------------------------------------------
# ToolDefinition: one registered tool
# ---------------------------------------------------------------------------
class ToolDefinition:
    """
    Wraps a Python callable as a named, schema-described tool.

    Args:
        name:        Unique tool identifier (e.g. "web_search").
        description: Human-readable description for the LLM.
        fn:          The callable to invoke.
        parameters:  Optional explicit parameter schema dict (JSON Schema object).
                     If omitted, schema is auto-generated from type hints + docstring.
        tags:        Optional list of category tags (e.g. ["search", "read-only"]).
    """

    def __init__(
        self,
        name: str,
        description: str,
        fn: Callable,
        parameters: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ):
        self.name = name
        self.description = description
        self.fn = fn
        self.tags: List[str] = tags or []
        self._parameters = parameters or self._infer_parameters()

    def _infer_parameters(self) -> Dict:
        """
        Auto-generate an OpenAI-compatible JSON Schema `parameters` object
        from the function's type hints and signature.
        """
        sig = inspect.signature(self.fn)
        try:
            hints = get_type_hints(self.fn)
        except Exception:
            hints = {}

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            annotation = hints.get(param_name, inspect.Parameter.empty)
            json_type = _python_type_to_json(annotation)

            # Extract per-parameter description from docstring if present
            doc = inspect.getdoc(self.fn) or ""
            param_description = ""
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{param_name}:") or stripped.startswith(f"{param_name} ("):
                    param_description = stripped.split(":", 1)[-1].strip()
                    break

            prop: Dict[str, Any] = {"type": json_type}
            if param_description:
                prop["description"] = param_description

            # Handle Optional[X] — mark as non-required
            origin = getattr(annotation, "__origin__", None)
            args = getattr(annotation, "__args__", ())
            is_optional = origin is type(None) or (
                origin is getattr(__import__("typing"), "Union", None)
                and type(None) in args
            )

            properties[param_name] = prop
            if param.default is inspect.Parameter.empty and not is_optional:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def to_openai_schema(self) -> Dict:
        """
        Return the OpenAI function-calling schema for this tool.
        Compatible with:
          - OpenAI `tools` parameter (chat completions)
          - Anthropic `tools` parameter
          - Ollama tool-call format
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._parameters,
            },
        }

    def call(self, **kwargs) -> Any:
        """Invoke the underlying callable with validated keyword arguments."""
        try:
            return self.fn(**kwargs)
        except TypeError as e:
            logger.error(f"[ToolRegistry] Call to '{self.name}' failed with bad args: {e}")
            raise


# ---------------------------------------------------------------------------
# ToolRegistry: central store
# ---------------------------------------------------------------------------
class ToolRegistry:
    """
    Central registry for all callable tools.

    Usage:
        registry = ToolRegistry()

        @registry.register(description="Search the web", tags=["search"])
        def web_search(query: str, max_results: int = 5) -> list:
            ...

        schemas = registry.get_schemas()   # -> list of OpenAI tool dicts
        result  = registry.dispatch("web_search", {"query": "hello"})
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> Callable:
        """
        Decorator to register a function as a tool.

        Args:
            name:        Override tool name (defaults to function name).
            description: Override description (defaults to first docstring line).
            parameters:  Explicit JSON Schema parameters dict.
            tags:        Category tags for filtering.
        """
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            tool_desc = description or (inspect.getdoc(fn) or "").split("\n")[0]
            tool = ToolDefinition(
                name=tool_name,
                description=tool_desc,
                fn=fn,
                parameters=parameters,
                tags=tags,
            )
            self._register_tool(tool)
            return fn
        return decorator

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a pre-built ToolDefinition directly."""
        self._register_tool(tool)

    def _register_tool(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            logger.warning(f"[ToolRegistry] Overwriting existing tool: '{tool.name}'")
        self._tools[tool.name] = tool
        logger.info(f"[ToolRegistry] Registered tool: '{tool.name}' tags={tool.tags}")

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if found and removed."""
        if name in self._tools:
            del self._tools[name]
            logger.info(f"[ToolRegistry] Unregistered tool: '{name}'")
            return True
        return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------
    def get(self, name: str) -> Optional[ToolDefinition]:
        """Return a ToolDefinition by name, or None."""
        return self._tools.get(name)

    def list_tools(self, tag: Optional[str] = None) -> List[ToolDefinition]:
        """Return all registered tools, optionally filtered by tag."""
        if tag:
            return [t for t in self._tools.values() if tag in t.tags]
        return list(self._tools.values())

    def get_schemas(
        self,
        tag: Optional[str] = None,
        names: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Return OpenAI-compatible tool schemas.

        Args:
            tag:   If provided, only include tools with this tag.
            names: If provided, only include tools with these names.
        """
        tools = self.list_tools(tag=tag)
        if names:
            tools = [t for t in tools if t.name in names]
        return [t.to_openai_schema() for t in tools]

    def get_schemas_json(
        self,
        tag: Optional[str] = None,
        names: Optional[List[str]] = None,
        indent: int = 2,
    ) -> str:
        """Return tool schemas as a formatted JSON string."""
        return json.dumps(self.get_schemas(tag=tag, names=names), indent=indent)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def dispatch(self, name: str, arguments: Dict[str, Any]) -> Any:
        """
        Dispatch a tool call by name with the given arguments.
        Used by the ReAct execution loop (C123) in the Action step.

        Args:
            name:      Tool name as returned by the LLM.
            arguments: Dict of argument name -> value (already JSON-decoded).

        Raises:
            KeyError:  If no tool with `name` is registered.
            TypeError: If arguments don't match the tool's signature.
        """
        tool = self._tools.get(name)
        if tool is None:
            available = list(self._tools.keys())
            raise KeyError(
                f"[ToolRegistry] Unknown tool: '{name}'. "
                f"Available: {available}"
            )
        logger.info(f"[ToolRegistry] Dispatching '{name}' with args: {list(arguments.keys())}")
        return tool.call(**arguments)

    def dispatch_json(self, name: str, arguments_json: str) -> Any:
        """
        Dispatch a tool call where arguments are a JSON string.
        Convenience wrapper for LLM outputs that return args as raw JSON.
        """
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"[ToolRegistry] Invalid JSON arguments for '{name}': {e}") from e
        return self.dispatch(name, args)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        names = list(self._tools.keys())
        return f"ToolRegistry({len(self._tools)} tools: {names})"


# ---------------------------------------------------------------------------
# Module-level singleton — shared registry used by all core modules
# ---------------------------------------------------------------------------
_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Return the module-level shared ToolRegistry (lazy-init singleton)."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        logger.debug("[ToolRegistry] Global registry initialised.")
    return _registry


def register(
    name: Optional[str] = None,
    description: Optional[str] = None,
    parameters: Optional[Dict] = None,
    tags: Optional[List[str]] = None,
) -> Callable:
    """
    Module-level decorator for the global registry.
    Shorthand for get_registry().register(...)

    Example:
        from core.tool_registry import register

        @register(description="Search the web for real-time info", tags=["search"])
        def web_search(query: str, max_results: int = 5) -> list:
            ...
    """
    return get_registry().register(
        name=name,
        description=description,
        parameters=parameters,
        tags=tags,
    )
