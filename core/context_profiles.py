# core/context_profiles.py
# Context size profiles — auto-detect a model's context window and map it
# to a small / medium / large profile that controls injection budgets,
# compaction thresholds, and MCP tool-list trimming.
#
# Usage:
#   profile = get_profile("gpt-4o")          # -> ContextProfile
#   profile = get_profile_for_tokens(8000)   # -> ContextProfile by window size

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Profile definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextProfile:
    name: str                # "small" | "medium" | "large"
    window_tokens: int       # Detected or assumed context window
    compaction_threshold: float  # Fraction of window that triggers compaction
    max_tool_slots: int      # Max MCP tools injected per request
    max_skill_slots: int     # Max skills injected per request
    summary_budget: int      # Tokens reserved for compaction summary
    system_budget: int       # Tokens reserved for system prompt

    @property
    def compaction_limit(self) -> int:
        """Hard token count at which auto-compaction fires."""
        return int(self.window_tokens * self.compaction_threshold)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "window_tokens": self.window_tokens,
            "compaction_threshold": self.compaction_threshold,
            "compaction_limit": self.compaction_limit,
            "max_tool_slots": self.max_tool_slots,
            "max_skill_slots": self.max_skill_slots,
            "summary_budget": self.summary_budget,
            "system_budget": self.system_budget,
        }


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

SMALL = ContextProfile(
    name="small",
    window_tokens=4_096,
    compaction_threshold=0.75,
    max_tool_slots=5,
    max_skill_slots=3,
    summary_budget=512,
    system_budget=512,
)

MEDIUM = ContextProfile(
    name="medium",
    window_tokens=32_000,
    compaction_threshold=0.80,
    max_tool_slots=15,
    max_skill_slots=8,
    summary_budget=1_024,
    system_budget=1_024,
)

LARGE = ContextProfile(
    name="large",
    window_tokens=128_000,
    compaction_threshold=0.80,
    max_tool_slots=40,
    max_skill_slots=20,
    summary_budget=2_048,
    system_budget=2_048,
)

# Ordered list — selection walks from smallest to largest.
_PROFILES = [SMALL, MEDIUM, LARGE]


# ---------------------------------------------------------------------------
# Model window registry
# Context windows sourced from provider docs; update as models evolve.
# ---------------------------------------------------------------------------

_MODEL_WINDOWS: Dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o1-mini": 128_000,
    "o3-mini": 200_000,
    # Anthropic
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
    # Aliases without date suffix
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    # Google
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    # Groq-hosted
    "llama3-8b-8192": 8_192,
    "llama3-70b-8192": 8_192,
    "llama-3.1-8b-instant": 131_072,
    "llama-3.1-70b-versatile": 131_072,
    "llama-3.3-70b-versatile": 131_072,
    "mixtral-8x7b-32768": 32_768,
    # Common Ollama / local models
    "llama3": 8_192,
    "llama3.1": 131_072,
    "llama3.2": 131_072,
    "mistral": 32_768,
    "mistral-nemo": 128_000,
    "codestral": 32_768,
    "phi3": 4_096,
    "phi3.5": 4_096,
    "gemma2": 8_192,
    "qwen2.5": 131_072,
    "deepseek-coder-v2": 163_840,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_window(model: str) -> int:
    """Return known context window for *model*, or a conservative 8 192 default."""
    key = model.lower().strip()
    if key in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[key]
    # Prefix match (e.g. "claude-3-5-sonnet-20250101" → "claude-3-5-sonnet")
    for registered, window in _MODEL_WINDOWS.items():
        if key.startswith(registered):
            return window
    return 8_192  # conservative fallback


def get_profile_for_tokens(window: int) -> ContextProfile:
    """Pick the profile whose window_tokens is ≤ actual window."""
    selected = SMALL
    for p in _PROFILES:
        if window >= p.window_tokens:
            selected = p
    return selected


def get_profile(model: str) -> ContextProfile:
    """Auto-detect window for *model* and return the matching profile."""
    window = get_window(model)
    return get_profile_for_tokens(window)


def all_profiles() -> list:
    """Return all built-in profiles as dicts (for the token panel API)."""
    return [p.to_dict() for p in _PROFILES]
