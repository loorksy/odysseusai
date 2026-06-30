# core/tool_selector.py
# Slim MCP tool injection — filters the full tool registry down to the
# subset most relevant to the current task, respecting the active
# ContextProfile's max_tool_slots budget.
#
# Usage:
#   selector = ToolSelector(registry=all_tools, profile=get_profile(model))
#   tools    = selector.select(task_text, session_tags=["git", "python"])

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.context_profiles import ContextProfile, MEDIUM


# ---------------------------------------------------------------------------
# Tool descriptor
# ---------------------------------------------------------------------------

@dataclass
class ToolDescriptor:
    """Lightweight representation of one MCP tool for selection purposes."""
    name: str
    description: str
    tags: List[str] = field(default_factory=list)   # e.g. ["git", "code", "search"]
    always_include: bool = False                     # Pinned tools (e.g. memory_write)
    token_cost: int = 200                            # Approximate schema token cost
    raw: Optional[Any] = None                        # Original tool dict/object

    def keyword_set(self) -> Set[str]:
        """Lowercase keywords from name + description for matching."""
        text = f"{self.name} {self.description}".lower()
        return set(re.findall(r"[a-z0-9_]+", text))


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

class ToolSelector:
    """Selects a task-relevant subset of MCP tools within a token budget.

    Selection algorithm:
      1. Always-include tools are pinned (they fill slots first).
      2. Remaining slots are scored by keyword overlap between the task
         text + session tags and each tool's name/description/tags.
      3. If tie-breaking is needed, lower token_cost tools are preferred.
      4. Result is trimmed to profile.max_tool_slots.

    This is intentionally simple and local — no embeddings, no LLM call.
    A future sprint can upgrade to semantic ranking via the RAG server.
    """

    def __init__(
        self,
        registry: List[ToolDescriptor],
        profile: ContextProfile = MEDIUM,
    ):
        self._registry = registry
        self._profile = profile

    def select(
        self,
        task: str,
        session_tags: Optional[List[str]] = None,
        force_include: Optional[List[str]] = None,
    ) -> List[ToolDescriptor]:
        """Return a scored, trimmed list of tools for this task.

        Args:
            task:          Natural-language task description or first user message.
            session_tags:  Domain hints from session context (e.g. ["python", "git"]).
            force_include: Tool names that must appear regardless of score.

        Returns:
            List of ToolDescriptor, length ≤ profile.max_tool_slots.
        """
        slots = self._profile.max_tool_slots
        pinned: List[ToolDescriptor] = []
        candidates: List[ToolDescriptor] = []
        forced_names: Set[str] = set(force_include or [])

        for tool in self._registry:
            if tool.always_include or tool.name in forced_names:
                pinned.append(tool)
            else:
                candidates.append(tool)

        # Build query keyword set
        query_words: Set[str] = set(re.findall(r"[a-z0-9_]+", task.lower()))
        for tag in (session_tags or []):
            query_words.update(re.findall(r"[a-z0-9_]+", tag.lower()))

        # Score candidates
        def score(t: ToolDescriptor) -> tuple:
            overlap = len(query_words & t.keyword_set())
            tag_bonus = sum(1 for tag in t.tags if tag.lower() in query_words)
            return (-(overlap + tag_bonus), t.token_cost)  # negate for sort

        ranked = sorted(candidates, key=score)

        # Fill remaining slots
        remaining = max(0, slots - len(pinned))
        selected = pinned + ranked[:remaining]
        return selected[:slots]

    def update_profile(self, profile: ContextProfile) -> None:
        self._profile = profile

    def all_tools(self) -> List[ToolDescriptor]:
        return list(self._registry)


# ---------------------------------------------------------------------------
# Helpers: build registry from raw MCP tool dicts
# ---------------------------------------------------------------------------

# Tag heuristics: keyword → tags
_TAG_RULES: Dict[str, List[str]] = {
    "search": ["search", "web", "research"],
    "memory": ["memory", "knowledge", "recall"],
    "file": ["file", "filesystem", "io"],
    "git": ["git", "github", "version_control"],
    "shell": ["shell", "bash", "exec", "terminal"],
    "code": ["code", "coding", "python", "javascript"],
    "image": ["image", "vision", "screenshot"],
    "email": ["email", "mail", "send"],
    "notion": ["notion", "page", "database"],
    "browser": ["browser", "navigate", "dom"],
    "skill": ["skill", "skills", "capability"],
    "schedule": ["schedule", "cron", "task"],
}


def _infer_tags(name: str, description: str) -> List[str]:
    text = f"{name} {description}".lower()
    tags: List[str] = []
    for keyword, tag_list in _TAG_RULES.items():
        if keyword in text:
            tags.extend(tag_list)
    return list(set(tags))


def build_registry(
    raw_tools: List[Dict],
    always_include: Optional[List[str]] = None,
) -> List[ToolDescriptor]:
    """Convert a list of raw MCP tool dicts to ToolDescriptor objects.

    Args:
        raw_tools:      List of {"name": str, "description": str, ...} dicts.
        always_include: Tool names that should always be injected (pinned).
    """
    pinned_names: Set[str] = set(always_include or [])
    descriptors: List[ToolDescriptor] = []
    for t in raw_tools:
        name = t.get("name", "")
        desc = t.get("description", "")
        descriptors.append(ToolDescriptor(
            name=name,
            description=desc,
            tags=_infer_tags(name, desc),
            always_include=(name in pinned_names),
            token_cost=_estimate_cost(t),
            raw=t,
        ))
    return descriptors


def _estimate_cost(tool_dict: Dict) -> int:
    """Rough token estimate: ~1 token per 4 chars of serialised schema."""
    import json
    try:
        return max(50, len(json.dumps(tool_dict)) // 4)
    except Exception:
        return 200
