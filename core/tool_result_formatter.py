"""ToolResultFormatter — Normalise tool outputs for LLM consumption (C36).

Transforms raw ToolResult objects into the message-list entry that gets
appended to the conversation before the next LLM call.

Supports three output shapes (auto-detected from tool output type):
  1. text     — plain str → passed through with optional truncation
  2. table    — list[dict] → rendered as markdown table
  3. json     — dict / other → pretty-printed JSON block (truncated)
  4. error    — ToolResult.success=False → structured error message

The formatter also:
  - Enforces a per-result token budget (truncates if needed)
  - Annotates the message with tool name, duration, and retry count
  - Produces the correct role for each provider:
      OpenAI: role="tool", tool_call_id=...
      Anthropic: role="user" with a tool_result content block
      Generic: role="tool" (default)

Public API:
  fmt = ToolResultFormatter(max_tokens=1000, provider="openai")
  msg = fmt.format(result, tool_call_id="call_abc")  → dict (message)
  msgs = fmt.format_batch(results, tool_call_ids)    → list[dict]
  text = fmt.to_text(result)                         → plain string
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 2_000
_CHARS_PER_TOKEN    = 4
_TABLE_MAX_ROWS     = 30
_TABLE_MAX_COL_W    = 40


class ToolResultFormatter:
    """Formats ToolResult objects into LLM-ready message dicts."""

    def __init__(
        self,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        provider: str = "openai",   # "openai" | "anthropic" | "generic"
    ):
        self._max_chars = max_tokens * _CHARS_PER_TOKEN
        self._provider  = provider.lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format(
        self,
        result,   # ToolResult
        tool_call_id: Optional[str] = None,
    ) -> Dict:
        """Return a single message dict ready for the messages array."""
        content = self._render_content(result)
        return self._wrap(result.name, content, tool_call_id)

    def format_batch(
        self,
        results: List,
        tool_call_ids: Optional[List[str]] = None,
    ) -> List[Dict]:
        ids = tool_call_ids or [None] * len(results)
        return [self.format(r, tid) for r, tid in zip(results, ids)]

    def to_text(self, result) -> str:
        """Plain-text representation (for logging / LTM storage)."""
        return self._render_content(result)

    # ------------------------------------------------------------------
    # Content rendering
    # ------------------------------------------------------------------

    def _render_content(self, result) -> str:
        if not result.success:
            return self._render_error(result)
        return self._render_output(result.name, result.output, result.duration_ms, result.retries)

    def _render_output(
        self,
        name: str,
        output: Any,
        duration_ms: float,
        retries: int,
    ) -> str:
        if isinstance(output, str):
            body = output
        elif isinstance(output, list) and output and isinstance(output[0], dict):
            body = self._render_table(output)
        elif isinstance(output, (dict, list)):
            body = self._render_json(output)
        else:
            body = str(output)

        # Truncate to budget
        if len(body) > self._max_chars:
            body = body[:self._max_chars] + f"\n\n[truncated — {len(body)} chars total]"

        meta = f"<!-- tool:{name} duration:{duration_ms:.0f}ms"
        if retries:
            meta += f" retries:{retries}"
        meta += " -->"
        return f"{meta}\n{body}"

    @staticmethod
    def _render_error(result) -> str:
        return (
            f"<!-- tool:{result.name} ERROR -->"
            f"\nTool call failed after {result.retries} retries."
            f"\nError: {result.error or 'unknown error'}"
        )

    @staticmethod
    def _render_table(rows: List[Dict]) -> str:
        """Render a list of dicts as a Markdown table."""
        if not rows:
            return "(empty table)"
        rows = rows[:_TABLE_MAX_ROWS]
        headers = list(rows[0].keys())

        def cell(v: Any) -> str:
            s = str(v).replace("|", "｜").replace("\n", " ")
            return s[:_TABLE_MAX_COL_W] + ("…" if len(s) > _TABLE_MAX_COL_W else "")

        header_row = "| " + " | ".join(headers) + " |"
        sep_row    = "| " + " | ".join("---" for _ in headers) + " |"
        data_rows  = [
            "| " + " | ".join(cell(row.get(h, "")) for h in headers) + " |"
            for row in rows
        ]
        return "\n".join([header_row, sep_row] + data_rows)

    @staticmethod
    def _render_json(obj: Any) -> str:
        try:
            return "```json\n" + json.dumps(obj, indent=2, default=str) + "\n```"
        except Exception:
            return str(obj)

    # ------------------------------------------------------------------
    # Provider wrapping
    # ------------------------------------------------------------------

    def _wrap(self, name: str, content: str, tool_call_id: Optional[str]) -> Dict:
        if self._provider == "openai":
            return {
                "role":         "tool",
                "tool_call_id": tool_call_id or f"call_{name}",
                "name":         name,
                "content":      content,
            }
        if self._provider == "anthropic":
            return {
                "role": "user",
                "content": [{
                    "type":        "tool_result",
                    "tool_use_id": tool_call_id or f"toolu_{name}",
                    "content":     content,
                }],
            }
        # Generic / fallback
        return {
            "role":    "tool",
            "name":    name,
            "content": content,
        }
