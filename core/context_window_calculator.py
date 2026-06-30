"""ContextWindowCalculator — Accurate context budget and headroom calculation (C23).

Answers the question: "How many tokens do I have left before this model's
context window is full?"

Key responsibilities:
  - Maintains a model registry with (context_window, output_reserve) pairs.
  - Counts tokens in a message list using tiktoken (cl100k_base) with a
    character-based fallback so it never crashes on models without tokenizers.
  - Computes headroom = context_window - used_tokens - output_reserve.
  - Provides a `fit_messages()` helper that trims oldest non-system messages
    to fit within a target token budget (used by CompactionMiddleware).

Public API:
  calc = ContextWindowCalculator()
  calc.count_tokens(messages)              → int
  calc.headroom(messages, model)           → int
  calc.utilization(messages, model)        → float 0.0–1.0
  calc.fit_messages(messages, model, reserve_output=True) → trimmed messages
  calc.model_info(model)                   → {context_window, output_reserve}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model registry
# context_window = total tokens the model accepts (input + output)
# output_reserve = tokens to hold back for the assistant reply
# ---------------------------------------------------------------------------
_MODEL_REGISTRY: Dict[str, Dict[str, int]] = {
    # OpenAI
    "gpt-4o":                   {"context_window": 128_000, "output_reserve": 4_096},
    "gpt-4o-mini":              {"context_window": 128_000, "output_reserve": 4_096},
    "gpt-4-turbo":              {"context_window": 128_000, "output_reserve": 4_096},
    "gpt-4":                    {"context_window":   8_192, "output_reserve": 2_048},
    "gpt-3.5-turbo":            {"context_window":  16_385, "output_reserve": 4_096},
    "o1":                       {"context_window": 200_000, "output_reserve": 8_000},
    "o1-mini":                  {"context_window": 128_000, "output_reserve": 4_096},
    "o3":                       {"context_window": 200_000, "output_reserve": 8_000},
    "o3-mini":                  {"context_window": 128_000, "output_reserve": 4_096},
    # Anthropic
    "claude-3-5-sonnet":        {"context_window": 200_000, "output_reserve": 8_192},
    "claude-3-5-haiku":         {"context_window": 200_000, "output_reserve": 8_192},
    "claude-3-opus":            {"context_window": 200_000, "output_reserve": 4_096},
    "claude-3-sonnet":          {"context_window": 200_000, "output_reserve": 4_096},
    "claude-3-haiku":           {"context_window": 200_000, "output_reserve": 4_096},
    # Google
    "gemini-1.5-pro":           {"context_window": 1_048_576, "output_reserve": 8_192},
    "gemini-1.5-flash":         {"context_window": 1_048_576, "output_reserve": 8_192},
    "gemini-2.0-flash":         {"context_window": 1_048_576, "output_reserve": 8_192},
    # Meta (via API providers)
    "llama-3.1-405b":           {"context_window": 128_000, "output_reserve": 4_096},
    "llama-3.1-70b":            {"context_window": 128_000, "output_reserve": 4_096},
    # Fallback / unknown
    "__default__":              {"context_window":  16_000, "output_reserve": 2_048},
}

# Overhead per message in the ChatML wire format (role + delimiters).
_TOKENS_PER_MESSAGE = 4
_TOKENS_PER_REPLY_PRIMER = 3  # every reply is primed with <|start|>assistant<|message|>


class ContextWindowCalculator:
    """Token counting and context-window headroom calculator."""

    def __init__(self, extra_models: Optional[Dict[str, Dict[str, int]]] = None):
        self._registry = {**_MODEL_REGISTRY, **(extra_models or {})}
        self._enc = self._load_encoder()

    # ------------------------------------------------------------------
    # Model registry
    # ------------------------------------------------------------------

    @staticmethod
    def _load_encoder():
        try:
            import tiktoken
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None

    def model_info(self, model: Optional[str] = None) -> Dict[str, int]:
        """Return {context_window, output_reserve} for `model`.

        Tries exact match, then prefix match (e.g. "gpt-4o-2024-05" → "gpt-4o"),
        then falls back to __default__.
        """
        if model and model in self._registry:
            return self._registry[model]
        if model:
            for key in self._registry:
                if key != "__default__" and model.startswith(key):
                    return self._registry[key]
        return self._registry["__default__"]

    def register_model(
        self,
        name: str,
        context_window: int,
        output_reserve: int = 4_096,
    ) -> None:
        """Add or override a model entry at runtime."""
        self._registry[name] = {"context_window": context_window, "output_reserve": output_reserve}

    # ------------------------------------------------------------------
    # Token counting
    # ------------------------------------------------------------------

    def count_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens for a message list using tiktoken or char fallback."""
        if self._enc:
            return self._count_tiktoken(messages)
        return self._count_chars(messages)

    def count_text(self, text: str) -> int:
        """Count tokens for a raw string."""
        if self._enc:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass
        return max(1, len(text) // 4)

    def _count_tiktoken(self, messages: List[Dict[str, Any]]) -> int:
        total = _TOKENS_PER_REPLY_PRIMER
        for msg in messages:
            total += _TOKENS_PER_MESSAGE
            for key, value in msg.items():
                if isinstance(value, str):
                    try:
                        total += len(self._enc.encode(value))
                    except Exception:
                        total += max(1, len(value) // 4)
                elif isinstance(value, list):  # content blocks (vision)
                    for block in value:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            try:
                                total += len(self._enc.encode(block["text"]))
                            except Exception:
                                total += max(1, len(block["text"]) // 4)
        return total

    @staticmethod
    def _count_chars(messages: List[Dict[str, Any]]) -> int:
        """Character-based fallback: ~4 chars per token."""
        total = _TOKENS_PER_REPLY_PRIMER
        for msg in messages:
            total += _TOKENS_PER_MESSAGE
            for key, value in msg.items():
                if isinstance(value, str):
                    total += max(1, len(value) // 4)
        return total

    # ------------------------------------------------------------------
    # Headroom / utilization
    # ------------------------------------------------------------------

    def headroom(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        *,
        extra_tokens: int = 0,
    ) -> int:
        """Tokens remaining before the context window is full.

        headroom = context_window - used - output_reserve - extra_tokens
        A negative value means the window is already over budget.
        """
        info = self.model_info(model)
        used = self.count_tokens(messages) + extra_tokens
        return info["context_window"] - used - info["output_reserve"]

    def utilization(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> float:
        """Fraction of the usable context window consumed (0.0 – 1.0+)."""
        info = self.model_info(model)
        usable = info["context_window"] - info["output_reserve"]
        if usable <= 0:
            return 1.0
        return self.count_tokens(messages) / usable

    # ------------------------------------------------------------------
    # Trim helper (used by CompactionMiddleware)
    # ------------------------------------------------------------------

    def fit_messages(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        *,
        target_utilization: float = 0.75,
        preserve_system: bool = True,
        preserve_last_n: int = 2,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Trim oldest non-system messages until utilization ≤ target_utilization.

        Returns (trimmed_messages, tokens_removed).
        System messages and the last `preserve_last_n` turns are never dropped.
        """
        info = self.model_info(model)
        target_tokens = int((info["context_window"] - info["output_reserve"]) * target_utilization)

        if self.count_tokens(messages) <= target_tokens:
            return messages, 0

        system_msgs = [m for m in messages if m.get("role") == "system"] if preserve_system else []
        non_system  = [m for m in messages if m.get("role") != "system"] if preserve_system else list(messages)

        protected_tail = non_system[-preserve_last_n:] if preserve_last_n > 0 else []
        trimmable      = non_system[:-preserve_last_n] if preserve_last_n > 0 else non_system

        before_tokens = self.count_tokens(messages)
        while trimmable and self.count_tokens(system_msgs + trimmable + protected_tail) > target_tokens:
            trimmable.pop(0)  # drop oldest trimmable

        result = system_msgs + trimmable + protected_tail
        after_tokens = self.count_tokens(result)
        return result, before_tokens - after_tokens
