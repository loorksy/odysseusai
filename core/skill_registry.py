"""SkillRegistry — Progressive Disclosure Layer (C18).

Enforces the ShadowRealm contract:
  - Only `name + description` (~53 tokens per skill) is kept in
    context at all times.  Full Instructions / Examples / Failure
    Modes are injected ON-DEMAND when a skill is selected for the
    current task.
  - Wraps the existing SkillsManager so no storage logic is
    duplicated here.

Public surface:
  SkillRegistry.compact_index(owner)  → [{name, description, category}] — always-on
  SkillRegistry.select(name, owner)   → full SKILL.md string or None
  SkillRegistry.search(query, owner)  → [{name, description, category}]
  SkillRegistry.prompt_block(owner)   → compact index formatted for system-prompt injection
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Token budget: name(~20) + description(~30) + overhead(~3)
_TOKENS_PER_SKILL = 53
_MAX_COMPACT_SKILLS = 40  # hard cap to prevent context overflow on huge libraries


class SkillRegistry:
    """Progressive disclosure wrapper around SkillsManager.

    Thread-safe for read operations. Writes go through SkillsManager;
    call `invalidate()` if the skill library changes outside this instance.
    """

    def __init__(self, skills_manager):
        """Args:
            skills_manager: an instance of services.memory.skills.SkillsManager
        """
        self._sm = skills_manager

    # ------------------------------------------------------------------
    # Compact index — always-on context footprint
    # ------------------------------------------------------------------

    def compact_index(
        self,
        owner: Optional[str] = None,
        *,
        active_toolsets: Optional[List[str]] = None,
        platform: Optional[str] = None,
        max_skills: int = _MAX_COMPACT_SKILLS,
    ) -> List[Dict]:
        """Return the lightweight [{name, description, category}] list.

        This is the ONLY skill data that should sit in the system prompt
        permanently.  Full skill content MUST NOT appear here.
        """
        idx = self._sm.index_for(
            owner=owner,
            active_toolsets=active_toolsets,
            platform=platform,
        )
        # Trim to budget so a large skill library never blows the context cap.
        return [{"name": s["name"], "description": s["description"], "category": s["category"]}
                for s in idx[:max_skills]]

    # ------------------------------------------------------------------
    # On-demand full skill injection
    # ------------------------------------------------------------------

    def select(
        self,
        name: str,
        owner: Optional[str] = None,
    ) -> Optional[str]:
        """Return the full SKILL.md string for `name`, or None if not found.

        Call this only when routing has decided the current task should use
        this skill. Injecting full content unconditionally defeats progressive
        disclosure.
        """
        md = self._sm.read_skill_md(name, owner=owner)
        if md:
            self._sm.record_use(name, owner=owner)
        return md

    # ------------------------------------------------------------------
    # Relevance search — on-demand, not injected by default
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        owner: Optional[str] = None,
        *,
        max_results: int = 5,
        min_confidence: float = 0.0,
    ) -> List[Dict]:
        """Return [{name, description, category}] for skills relevant to `query`.

        Results still contain only compact fields.  Call `select()` to get
        full content for any result the router decides to inject.
        """
        skills = self._sm.load(owner=owner)
        hits = self._sm.get_relevant_skills(
            query,
            skills=skills,
            max_items=max_results,
            min_confidence=min_confidence,
        )
        return [{"name": s["name"], "description": s.get("description", ""),
                 "category": s.get("category", "general")} for s in hits]

    # ------------------------------------------------------------------
    # System-prompt block
    # ------------------------------------------------------------------

    def prompt_block(
        self,
        owner: Optional[str] = None,
        *,
        active_toolsets: Optional[List[str]] = None,
        platform: Optional[str] = None,
        header: str = "## Available Skills",
    ) -> str:
        """Format the compact index as a system-prompt section.

        The block is intentionally minimal.  Full skill instructions are
        injected separately by AgentHarness.inject_skill() when routing
        selects a skill for the current turn.

        Example output:
          ## Available Skills
          - git-squash-commits: Squash the last N commits into one clean commit.
          - skill_creator: Writes a new SKILL.md from a successful workflow trace.
        """
        idx = self.compact_index(
            owner=owner,
            active_toolsets=active_toolsets,
            platform=platform,
        )
        if not idx:
            return ""
        lines = [header]
        for s in idx:
            desc = (s.get("description") or "").strip().rstrip(".")
            lines.append(f"- {s['name']}: {desc}.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Estimated token footprint
    # ------------------------------------------------------------------

    def estimated_tokens(self, owner: Optional[str] = None) -> int:
        """Rough token count for the compact index (for the Token Panel)."""
        return len(self.compact_index(owner=owner)) * _TOKENS_PER_SKILL
