"""
C91 — Memory Manager
Manages agent conversation history with token budgeting,
summarization hooks, and sliding-window truncation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from core.llm_client import LLMMessage


@dataclass
class MemoryEntry:
    message: LLMMessage
    tokens: int = 0
    pinned: bool = False  # pinned entries are never evicted


class MemoryManager:
    """
    Maintains a bounded conversation history.
    Strategies: sliding_window | summarize | truncate_oldest
    """

    STRATEGIES = ("sliding_window", "summarize", "truncate_oldest")

    def __init__(
        self,
        max_tokens: int = 8192,
        strategy: str = "sliding_window",
        token_estimator: Optional[Callable[[str], int]] = None,
        summarizer: Optional[Callable[[list[LLMMessage]], str]] = None,
        system_prompt: str = "",
    ):
        if strategy not in self.STRATEGIES:
            raise ValueError(f"strategy must be one of {self.STRATEGIES}")
        self.max_tokens = max_tokens
        self.strategy = strategy
        self._estimator = token_estimator or self._default_estimator
        self._summarizer = summarizer
        self._entries: list[MemoryEntry] = []
        self._summary: str = ""
        if system_prompt:
            self.set_system_prompt(system_prompt)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def set_system_prompt(self, prompt: str) -> None:
        """Replace or create the pinned system message."""
        self._entries = [e for e in self._entries if e.message.role != "system"]
        entry = MemoryEntry(
            message=LLMMessage(role="system", content=prompt),
            tokens=self._estimator(prompt),
            pinned=True,
        )
        self._entries.insert(0, entry)

    def add(self, message: LLMMessage, pinned: bool = False) -> None:
        tokens = self._estimator(message.content)
        self._entries.append(MemoryEntry(message=message, tokens=tokens, pinned=pinned))
        self._enforce_budget()

    def add_user(self, content: str) -> None:
        self.add(LLMMessage(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self.add(LLMMessage(role="assistant", content=content))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.add(LLMMessage(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        ))

    def get_messages(self) -> list[LLMMessage]:
        messages = []
        if self._summary:
            first_non_system = next(
                (i for i, e in enumerate(self._entries) if e.message.role != "system"), 0
            )
            messages = [e.message for e in self._entries[:first_non_system]]
            messages.append(LLMMessage(role="system", content=f"[Summary of earlier context]\n{self._summary}"))
            messages += [e.message for e in self._entries[first_non_system:]]
        else:
            messages = [e.message for e in self._entries]
        return messages

    def token_count(self) -> int:
        return sum(e.tokens for e in self._entries)

    def clear(self, keep_system: bool = True) -> None:
        if keep_system:
            self._entries = [e for e in self._entries if e.message.role == "system"]
        else:
            self._entries = []
        self._summary = ""

    def last_assistant_message(self) -> Optional[str]:
        for entry in reversed(self._entries):
            if entry.message.role == "assistant":
                return entry.message.content
        return None

    # ------------------------------------------------------------------ #
    #  Budget enforcement                                                  #
    # ------------------------------------------------------------------ #

    def _enforce_budget(self) -> None:
        if self.token_count() <= self.max_tokens:
            return
        if self.strategy == "sliding_window":
            self._sliding_window()
        elif self.strategy == "summarize":
            self._summarize_old()
        elif self.strategy == "truncate_oldest":
            self._truncate_oldest()

    def _sliding_window(self) -> None:
        while self.token_count() > self.max_tokens:
            evictable = [i for i, e in enumerate(self._entries) if not e.pinned]
            if not evictable:
                break
            self._entries.pop(evictable[0])

    def _truncate_oldest(self) -> None:
        self._sliding_window()

    def _summarize_old(self) -> None:
        if self._summarizer is None:
            self._sliding_window()
            return
        evictable = [e for e in self._entries if not e.pinned]
        if len(evictable) < 4:
            self._sliding_window()
            return
        half = evictable[: len(evictable) // 2]
        summary_text = self._summarizer([e.message for e in half])
        self._summary = summary_text
        ids_to_remove = {id(e) for e in half}
        self._entries = [e for e in self._entries if id(e) not in ids_to_remove]

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _default_estimator(text: str) -> int:
        """Rough approximation: 1 token ≈ 4 characters."""
        return max(1, len(text) // 4)

    def __len__(self) -> int:
        return len(self._entries)
