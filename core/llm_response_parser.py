"""
core/llm_response_parser.py
C90 — LLM Response Parser

Extracts structured data from any LLMResponse (C88):
  - Plain text content
  - Tool/function call arguments (OpenAI + Anthropic normalised format)
  - JSON blobs (fenced or raw)
  - Key-value fields from structured prose
  - ReAct Thought / Action / Observation blocks (feeds C123 ReasoningEngine)
  - Streaming chunk accumulation

Architecture Invariant #1: stdlib only at module level.
No external dependencies.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single parsed tool/function call from an LLM response."""
    id: str
    name: str
    arguments: Dict[str, Any]  # already parsed from JSON string
    raw_arguments: str         # original JSON string

    def get(self, key: str, default: Any = None) -> Any:
        return self.arguments.get(key, default)


@dataclass
class ReActStep:
    """
    One Thought→Action→Observation triple from a ReAct-style response.
    Feeds directly into C123 ReasoningEngine.
    """
    thought: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""   # populated by the caller after tool execution


@dataclass
class ParsedResponse:
    """Fully parsed and validated result from any LLMResponse."""
    # Raw
    raw_content: str
    finish_reason: str
    model: str
    provider: str

    # Extracted
    text: str = ""                          # clean prose, no fences
    json_data: Optional[Dict] = None        # first valid JSON block found
    json_blocks: List[Dict] = field(default_factory=list)  # all JSON blocks
    tool_calls: List[ToolCall] = field(default_factory=list)
    react_step: Optional[ReActStep] = None
    key_values: Dict[str, str] = field(default_factory=dict)  # "Key: value" pairs

    # Meta
    has_tool_calls: bool = False
    has_json: bool = False
    is_react: bool = False
    parse_errors: List[str] = field(default_factory=list)

    @property
    def first_tool_call(self) -> Optional[ToolCall]:
        return self.tool_calls[0] if self.tool_calls else None

    @property
    def ok(self) -> bool:
        """True if parsing completed with no errors."""
        return len(self.parse_errors) == 0


# ---------------------------------------------------------------------------
# LLMResponseParser
# ---------------------------------------------------------------------------

class LLMResponseParser:
    """
    Parses LLMResponse objects (C88) into structured ParsedResponse objects.

    Usage:
        from core.llm_client import complete, LLMConfig, Message
        from core.llm_response_parser import LLMResponseParser

        parser = LLMResponseParser()
        llm_resp = complete([Message(role="user", content="...")])
        parsed = parser.parse(llm_resp)

        # Access tool calls
        if parsed.has_tool_calls:
            tc = parsed.first_tool_call
            result = dispatch(tc.name, tc.arguments)  # C89 ToolRegistry

        # Access JSON
        if parsed.has_json:
            data = parsed.json_data

        # Access ReAct step (C123)
        if parsed.is_react:
            step = parsed.react_step
            print(step.thought, step.action, step.action_input)
    """

    # Regex patterns
    _JSON_FENCE_RE = re.compile(
        r"```(?:json)?\s*\n?({.*?})\s*\n?```",
        re.DOTALL | re.IGNORECASE,
    )
    _JSON_OBJECT_RE = re.compile(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", re.DOTALL)
    _KEY_VALUE_RE = re.compile(r"^([A-Za-z][\w ]{0,40}):\s*(.+)$", re.MULTILINE)

    # ReAct patterns
    _THOUGHT_RE = re.compile(r"Thought:\s*(.+?)(?=Action:|Observation:|$)", re.DOTALL | re.IGNORECASE)
    _ACTION_RE = re.compile(r"Action:\s*(.+?)(?=Action Input:|Observation:|Thought:|$)", re.DOTALL | re.IGNORECASE)
    _ACTION_INPUT_RE = re.compile(r"Action Input:\s*(.+?)(?=Observation:|Thought:|Action:|$)", re.DOTALL | re.IGNORECASE)
    _OBSERVATION_RE = re.compile(r"Observation:\s*(.+?)(?=Thought:|Action:|$)", re.DOTALL | re.IGNORECASE)

    def parse(self, llm_response: Any) -> ParsedResponse:
        """
        Main entry point. Accepts an LLMResponse (C88) or any object with
        .content, .finish_reason, .model, .provider, .tool_calls attributes.
        """
        content = getattr(llm_response, "content", "") or ""
        finish_reason = getattr(llm_response, "finish_reason", "stop") or "stop"
        model = getattr(llm_response, "model", "unknown")
        provider = getattr(llm_response, "provider", "unknown")
        raw_tool_calls = getattr(llm_response, "tool_calls", None) or []

        result = ParsedResponse(
            raw_content=content,
            finish_reason=finish_reason,
            model=model,
            provider=provider,
        )

        # 1. Tool calls (provider-normalised format from C88)
        self._parse_tool_calls(raw_tool_calls, result)

        # 2. JSON blocks
        self._parse_json(content, result)

        # 3. ReAct structure
        self._parse_react(content, result)

        # 4. Key-value pairs
        self._parse_key_values(content, result)

        # 5. Clean text
        result.text = self._clean_text(content)

        return result

    def parse_raw(self, content: str, **kwargs) -> ParsedResponse:
        """
        Parse a raw string without a full LLMResponse object.
        Useful for testing or processing streamed/accumulated content.
        """
        class _Stub:
            pass
        stub = _Stub()
        stub.content = content
        stub.finish_reason = kwargs.get("finish_reason", "stop")
        stub.model = kwargs.get("model", "unknown")
        stub.provider = kwargs.get("provider", "unknown")
        stub.tool_calls = kwargs.get("tool_calls", [])
        return self.parse(stub)

    # ------------------------------------------------------------------
    # Tool call parsing
    # ------------------------------------------------------------------

    def _parse_tool_calls(self, raw: List[Dict], result: ParsedResponse) -> None:
        """
        Normalises the tool_calls list from C88 (already in OpenAI format):
        [{ "id": ..., "type": "function", "function": { "name": ..., "arguments": "..." } }]
        """
        for tc in raw:
            try:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, dict):
                    # Anthropic already parsed it
                    arguments = raw_args
                    raw_args = json.dumps(raw_args)
                else:
                    arguments = json.loads(raw_args)
                result.tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=name,
                    arguments=arguments,
                    raw_arguments=raw_args,
                ))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                result.parse_errors.append(f"ToolCall parse error: {e}")
                logger.warning(f"[LLMResponseParser] Tool call parse error: {e}")

        result.has_tool_calls = len(result.tool_calls) > 0

    # ------------------------------------------------------------------
    # JSON extraction
    # ------------------------------------------------------------------

    def _parse_json(self, content: str, result: ParsedResponse) -> None:
        blocks: List[Dict] = []

        # 1. Try fenced ```json blocks first (highest confidence)
        for m in self._JSON_FENCE_RE.finditer(content):
            try:
                blocks.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass

        # 2. Try bare JSON objects if no fenced blocks found
        if not blocks:
            for m in self._JSON_OBJECT_RE.finditer(content):
                try:
                    obj = json.loads(m.group(1))
                    if isinstance(obj, dict):
                        blocks.append(obj)
                except json.JSONDecodeError:
                    pass

        # 3. Try entire content as JSON (response_format=json_object mode)
        if not blocks:
            stripped = content.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    blocks.append(json.loads(stripped))
                except json.JSONDecodeError:
                    pass

        result.json_blocks = blocks
        result.json_data = blocks[0] if blocks else None
        result.has_json = len(blocks) > 0

    # ------------------------------------------------------------------
    # ReAct parsing (feeds C123 ReasoningEngine)
    # ------------------------------------------------------------------

    def _parse_react(self, content: str, result: ParsedResponse) -> None:
        thought_m = self._THOUGHT_RE.search(content)
        action_m = self._ACTION_RE.search(content)

        if not thought_m and not action_m:
            return

        step = ReActStep()
        if thought_m:
            step.thought = thought_m.group(1).strip()
        if action_m:
            step.action = action_m.group(1).strip()
        action_input_m = self._ACTION_INPUT_RE.search(content)
        if action_input_m:
            step.action_input = action_input_m.group(1).strip()
        observation_m = self._OBSERVATION_RE.search(content)
        if observation_m:
            step.observation = observation_m.group(1).strip()

        result.react_step = step
        result.is_react = True

    # ------------------------------------------------------------------
    # Key-value extraction
    # ------------------------------------------------------------------

    def _parse_key_values(self, content: str, result: ParsedResponse) -> None:
        """
        Extracts "Key: value" pairs from prose.
        Skips common false positives (URLs, timestamps).
        """
        skip_keys = {"http", "https", "ftp", "file"}
        for m in self._KEY_VALUE_RE.finditer(content):
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key.lower() not in skip_keys and len(val) < 500:
                result.key_values[key] = val

    # ------------------------------------------------------------------
    # Text cleaning
    # ------------------------------------------------------------------

    def _clean_text(self, content: str) -> str:
        """
        Returns content with fenced code blocks and leading/trailing
        whitespace stripped, suitable for display or further NLP.
        """
        text = re.sub(r"```[\w]*\n?.*?```", "", content, flags=re.DOTALL)
        text = text.strip()
        return text

    # ------------------------------------------------------------------
    # Streaming accumulator
    # ------------------------------------------------------------------

    def accumulate(self, chunks) -> str:
        """
        Consume a streaming iterator (from C88 LLMClient.stream()) and
        return the fully accumulated content string.

        Usage:
            content = parser.accumulate(client.stream(messages, config))
            parsed = parser.parse_raw(content)
        """
        return "".join(chunks)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_json_schema(
    data: Dict, required_keys: List[str], types: Optional[Dict[str, type]] = None
) -> Tuple[bool, List[str]]:
    """
    Lightweight schema validation for parsed JSON blocks.
    Returns (is_valid, list_of_errors).

    Example:
        ok, errors = validate_json_schema(
            data,
            required_keys=["action", "target"],
            types={"action": str, "target": str},
        )
    """
    errors: List[str] = []
    for key in required_keys:
        if key not in data:
            errors.append(f"Missing required key: '{key}'")
    if types:
        for key, expected_type in types.items():
            if key in data and not isinstance(data[key], expected_type):
                errors.append(
                    f"Key '{key}' expected {expected_type.__name__}, "
                    f"got {type(data[key]).__name__}"
                )
    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_parser: Optional[LLMResponseParser] = None


def get_parser() -> LLMResponseParser:
    """Return the module-level shared LLMResponseParser (lazy-init singleton)."""
    global _parser
    if _parser is None:
        _parser = LLMResponseParser()
        logger.debug("[LLMResponseParser] Global parser initialised.")
    return _parser


def parse(llm_response: Any) -> ParsedResponse:
    """Module-level shorthand: get_parser().parse(llm_response)"""
    return get_parser().parse(llm_response)


def parse_raw(content: str, **kwargs) -> ParsedResponse:
    """Module-level shorthand: get_parser().parse_raw(content)"""
    return get_parser().parse_raw(content, **kwargs)
