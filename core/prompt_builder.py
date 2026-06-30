"""
C94 — Prompt Builder
Fluent builder for constructing structured LLM prompts.
Supports system/user/assistant sections, variable interpolation,
few-shot examples, and tool-use instructions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.llm_client import LLMMessage


@dataclass
class FewShotExample:
    user: str
    assistant: str


class PromptBuilder:
    """
    Fluent builder that produces a list[LLMMessage].

    Example usage::

        messages = (
            PromptBuilder()
            .system("You are a helpful assistant.")
            .var("language", "Python")
            .few_shot(FewShotExample("What is 2+2?", "4"))
            .user("Write a {{language}} hello-world.")
            .build()
        )
    """

    def __init__(self):
        self._sections: list[dict] = []
        self._variables: dict[str, Any] = {}
        self._few_shots: list[FewShotExample] = []
        self._tool_names: list[str] = []

    def var(self, key: str, value: Any) -> "PromptBuilder":
        self._variables[key] = value
        return self

    def vars(self, mapping: dict[str, Any]) -> "PromptBuilder":
        self._variables.update(mapping)
        return self

    def system(self, content: str) -> "PromptBuilder":
        self._sections.append({"role": "system", "content": content})
        return self

    def user(self, content: str) -> "PromptBuilder":
        self._sections.append({"role": "user", "content": content})
        return self

    def assistant(self, content: str) -> "PromptBuilder":
        self._sections.append({"role": "assistant", "content": content})
        return self

    def few_shot(self, *examples: FewShotExample) -> "PromptBuilder":
        self._few_shots.extend(examples)
        return self

    def tool_instructions(self, tool_names: list[str]) -> "PromptBuilder":
        self._tool_names = tool_names
        return self

    def build(self) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        tool_note = self._build_tool_note()
        sections = list(self._sections)
        if tool_note:
            inserted = False
            for i, s in enumerate(sections):
                if s["role"] == "system":
                    sections[i] = {**s, "content": s["content"] + "\n\n" + tool_note}
                    inserted = True
                    break
            if not inserted:
                sections.insert(0, {"role": "system", "content": tool_note})
        last_user_idx = None
        for i in range(len(sections) - 1, -1, -1):
            if sections[i]["role"] == "user":
                last_user_idx = i
                break
        result_sections: list[dict] = []
        for i, s in enumerate(sections):
            result_sections.append(s)
            if i == last_user_idx and self._few_shots and i == len(sections) - 1:
                result_sections.pop()
                for ex in self._few_shots:
                    result_sections.append({"role": "user", "content": ex.user})
                    result_sections.append({"role": "assistant", "content": ex.assistant})
                result_sections.append(s)
        for s in result_sections:
            content = self._interpolate(s["content"])
            messages.append(LLMMessage(role=s["role"], content=content))
        return messages

    def render(self) -> str:
        parts = []
        for msg in self.build():
            parts.append(f"{msg.role.upper()}:\n{msg.content}")
        return "\n\n".join(parts)

    def _interpolate(self, text: str) -> str:
        def replacer(match: re.Match) -> str:
            key = match.group(1).strip()
            val = self._variables.get(key)
            return str(val) if val is not None else match.group(0)
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", replacer, text)

    def _build_tool_note(self) -> str:
        if not self._tool_names:
            return ""
        names = ", ".join(self._tool_names)
        return (
            f"You have access to the following tools: {names}.\n"
            "Use <tool_call>{\"name\": \"...\", \"arguments\": {...}}</tool_call> "
            "to invoke a tool. Wrap your final answer in "
            "<FINAL_ANSWER>...</FINAL_ANSWER>."
        )

    def clone(self) -> "PromptBuilder":
        new = PromptBuilder()
        new._sections = list(self._sections)
        new._variables = dict(self._variables)
        new._few_shots = list(self._few_shots)
        new._tool_names = list(self._tool_names)
        return new
