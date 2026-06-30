"""
C108 — Output Formatter
Structured output rendering for agent responses: markdown, JSON,
plain text, tables, and streaming-compatible chunked output.
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional


@dataclass
class FormattedOutput:
    content: str
    format: str  # 'markdown' | 'json' | 'text' | 'table'
    truncated: bool = False
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.content


class OutputFormatter:
    """
    Renders agent output in multiple formats.

    Usage::

        fmt = OutputFormatter(max_width=100)
        print(fmt.markdown(response_text))
        print(fmt.as_json({"answer": "42"}))
        print(fmt.table([["Name", "Score"], ["Alice", 95]]))
    """

    def __init__(
        self,
        max_width: int = 120,
        max_length: Optional[int] = None,
        indent: int = 2,
    ):
        self.max_width = max_width
        self.max_length = max_length
        self.indent = indent

    # ------------------------------------------------------------------ #
    #  Format methods                                                      #
    # ------------------------------------------------------------------ #

    def text(self, content: str, wrap: bool = True) -> FormattedOutput:
        out = textwrap.fill(content, width=self.max_width) if wrap else content
        return self._make(out, "text")

    def markdown(self, content: str) -> FormattedOutput:
        return self._make(content, "markdown")

    def as_json(
        self,
        data: Any,
        pretty: bool = True,
        sort_keys: bool = False,
    ) -> FormattedOutput:
        try:
            out = json.dumps(data, indent=self.indent if pretty else None, sort_keys=sort_keys, default=str)
        except (TypeError, ValueError) as e:
            out = json.dumps({"error": str(e), "raw": str(data)})
        return self._make(out, "json")

    def table(
        self,
        rows: list[list[Any]],
        headers: Optional[list[str]] = None,
        align: str = "left",
    ) -> FormattedOutput:
        if not rows:
            return self._make("", "table")
        all_rows = ([headers] + rows) if headers else rows
        str_rows = [[str(cell) for cell in row] for row in all_rows]
        col_count = max(len(r) for r in str_rows)
        col_widths = [
            max(len(r[i]) if i < len(r) else 0 for r in str_rows)
            for i in range(col_count)
        ]
        lines = []
        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
        for idx, row in enumerate(str_rows):
            cells = []
            for i, w in enumerate(col_widths):
                cell = row[i] if i < len(row) else ""
                if align == "right":
                    cells.append(f" {cell:>{w}} ")
                elif align == "center":
                    cells.append(f" {cell:^{w}} ")
                else:
                    cells.append(f" {cell:<{w}} ")
            lines.append("|" + "|".join(cells) + "|")
            if idx == 0 and headers:
                lines.append(sep)
        return self._make("\n".join([sep] + lines + [sep]), "table")

    def bullet_list(
        self,
        items: list[str],
        symbol: str = "-",
        numbered: bool = False,
    ) -> FormattedOutput:
        lines = []
        for i, item in enumerate(items, 1):
            prefix = f"{i}." if numbered else symbol
            wrapped = textwrap.fill(
                item, width=self.max_width - len(prefix) - 1,
                subsequent_indent=" " * (len(prefix) + 1)
            )
            lines.append(f"{prefix} {wrapped}")
        return self._make("\n".join(lines), "text")

    def key_value(
        self,
        data: dict,
        separator: str = ": ",
        sort_keys: bool = False,
    ) -> FormattedOutput:
        keys = sorted(data) if sort_keys else list(data)
        max_key = max(len(str(k)) for k in keys) if keys else 0
        lines = [f"{str(k):<{max_key}}{separator}{data[k]}" for k in keys]
        return self._make("\n".join(lines), "text")

    def truncate(self, content: str, max_len: Optional[int] = None) -> FormattedOutput:
        limit = max_len or self.max_length
        if limit and len(content) > limit:
            return FormattedOutput(
                content=content[:limit] + "... [truncated]",
                format="text",
                truncated=True,
            )
        return self._make(content, "text")

    # ------------------------------------------------------------------ #
    #  Streaming                                                           #
    # ------------------------------------------------------------------ #

    def stream_chunks(self, content: str, chunk_size: int = 80) -> Iterator[str]:
        for i in range(0, len(content), chunk_size):
            yield content[i:i + chunk_size]

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _make(self, content: str, fmt: str) -> FormattedOutput:
        truncated = False
        if self.max_length and len(content) > self.max_length:
            content = content[:self.max_length] + "... [truncated]"
            truncated = True
        return FormattedOutput(content=content, format=fmt, truncated=truncated)

    def __repr__(self) -> str:
        return f"OutputFormatter(max_width={self.max_width}, max_length={self.max_length})"
