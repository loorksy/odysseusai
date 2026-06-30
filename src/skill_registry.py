# src/skill_registry.py
"""Skill Registry component for progressive disclosure of skills."""

import os
import logging
from typing import Dict, List, Optional
from services.memory.skills import SkillsManager

logger = logging.getLogger(__name__)

class SkillRegistry:
    """Loads skills from disk, manages their lightweight index (name + description) for prompt injection, 
    and handles loading full skill files when selected/active for a task (progressive disclosure)."""

    def __init__(self, data_dir: str):
        self.skills_manager = SkillsManager(data_dir)
        self._index_cache: List[dict] = []

    def load_all(self, owner: Optional[str] = None) -> List[dict]:
        """Load and cache name+description index of all published/allowable skills."""
        self._index_cache = self.skills_manager.index_for(owner=owner)
        return self._index_cache

    def get_index_context(self, owner: Optional[str] = None) -> str:
        """Format the skill index for system prompt injection (name + description only)."""
        idx = self.load_all(owner=owner)
        if not idx:
            return ""
        lines = [
            "## Available skills",
            "You have access to the following procedural skills. To see the full procedure, pitfalls, and verification steps of a skill, invoke it or call `manage_skills` with action='view' and the name of the skill.",
            ""
        ]
        for s in idx:
            lines.append(f"- **{s['name']}**: {s['description']}")
        return "\n".join(lines)

    def get_full_skill_md(self, name: str, owner: Optional[str] = None) -> Optional[str]:
        """Load and return the full SKILL.md text on demand (when selected/active)."""
        return self.skills_manager.read_skill_md(name, owner=owner)

    def get_active_skill_context(self, name: str, owner: Optional[str] = None) -> str:
        """Generate the full context block for an active/injected skill."""
        md = self.get_full_skill_md(name, owner=owner)
        if not md:
            return ""
        return (
            f"### Active Skill: {name}\n"
            f"You have activated the following procedural skill for this task. Follow its instructions precisely:\n\n"
            f"--- BEGIN SKILL ---\n{md}\n--- END SKILL ---\n"
        )
