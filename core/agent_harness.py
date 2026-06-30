"""AgentHarness — Session management, skill injection, tool routing (C19).

Glues together:
  - SkillRegistry   — progressive disclosure layer
  - ToolSelector    — slim MCP tool injection (already shipped, C11)
  - TokenCounter    — per-session budget tracking (already shipped, C09)
  - CompactionMiddleware — 80% auto-summarise (already shipped, C12)

Lifecycle:
  harness = AgentHarness(skills_manager, owner="alice")
  harness.begin_session(session_id)      # start a new turn
  system_prompt = harness.build_system_prompt(task)   # compact index + selected skill
  messages = harness.inject_skill(messages, skill_name)  # full skill into messages
  messages = harness.maybe_compact(messages)             # 80% guard
  harness.record_turn(tokens_used)       # update budget

Design constraints:
  - Never injects full skill content into the system prompt.
  - Skill content is injected as an assistant turn BEFORE the user message
    so it does not pollute the permanent system prompt.
  - Compaction is checked AFTER skill injection so the skill content is
    included in the token estimate before the guard fires.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentHarness:
    """Thin orchestrator that wires the progressive disclosure stack."""

    def __init__(
        self,
        skills_manager,
        owner: Optional[str] = None,
        *,
        active_toolsets: Optional[List[str]] = None,
        platform: Optional[str] = None,
    ):
        from core.skill_registry import SkillRegistry
        self._sm = skills_manager
        self._registry = SkillRegistry(skills_manager)
        self.owner = owner
        self.active_toolsets = active_toolsets or []
        self.platform = platform

        # Per-harness session state (reset on begin_session)
        self._session_id: Optional[str] = None
        self._session_start: float = 0.0
        self._tokens_used: int = 0
        self._active_skill: Optional[str] = None
        self._training_mode: bool = False

        # Lazy-load optional components so the harness works even when
        # those modules are not yet installed (graceful degradation).
        self._token_counter = self._load_token_counter()
        self._compaction = self._load_compaction()

    # ------------------------------------------------------------------
    # Lazy component loaders
    # ------------------------------------------------------------------

    @staticmethod
    def _load_token_counter():
        try:
            from core.token_counter import TokenCounter
            return TokenCounter.get()
        except Exception as e:
            logger.debug(f"TokenCounter unavailable: {e}")
            return None

    @staticmethod
    def _load_compaction():
        try:
            from core.compaction_middleware import CompactionMiddleware
            return CompactionMiddleware()
        except Exception as e:
            logger.debug(f"CompactionMiddleware unavailable: {e}")
            return None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def begin_session(self, session_id: str) -> None:
        """Start a new agent session (resets per-session counters)."""
        self._session_id = session_id
        self._session_start = time.time()
        self._tokens_used = 0
        self._active_skill = None
        if self._token_counter:
            try:
                self._token_counter.reset_session(session_id)
            except Exception:
                pass

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    # ------------------------------------------------------------------
    # System prompt assembly
    # ------------------------------------------------------------------

    def build_system_prompt(
        self,
        base_prompt: str,
        task: Optional[str] = None,
        *,
        inject_skill: Optional[str] = None,
    ) -> str:
        """Return the full system prompt for this turn.

        Appends the compact skill index to `base_prompt`.
        Does NOT inject the full skill content — use inject_skill() for that.

        Args:
            base_prompt:  The agent's base system prompt (persona, rules, etc.).
            task:         The user's current task text (used for routing hints only).
            inject_skill: If provided, the compact index will note this skill is active.
        """
        skill_block = self._registry.prompt_block(
            owner=self.owner,
            active_toolsets=self.active_toolsets or None,
            platform=self.platform,
        )
        parts = [base_prompt.rstrip()]
        if skill_block:
            parts.append(skill_block)
        if inject_skill:
            parts.append(f"\n> Active skill: **{inject_skill}** (full instructions injected below).")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Skill injection into message history
    # ------------------------------------------------------------------

    def inject_skill(
        self,
        messages: List[Dict[str, Any]],
        skill_name: str,
    ) -> List[Dict[str, Any]]:
        """Inject the full SKILL.md as a system-context message into `messages`.

        The skill is inserted as a `system` role message immediately before the
        last user message so it appears as fresh context without permanently
        enlarging the static system prompt.

        Returns the modified message list (may be same object).
        """
        md = self._registry.select(skill_name, owner=self.owner)
        if not md:
            logger.warning(f"inject_skill: skill '{skill_name}' not found for owner '{self.owner}'")
            return messages

        self._active_skill = skill_name
        skill_msg = {
            "role": "system",
            "content": f"## Active Skill: {skill_name}\n\n{md}",
        }

        # Insert before the last user message, or append if none found.
        last_user_idx = next(
            (i for i in range(len(messages) - 1, -1, -1)
             if messages[i].get("role") == "user"),
            None,
        )
        if last_user_idx is not None:
            return messages[:last_user_idx] + [skill_msg] + messages[last_user_idx:]
        return messages + [skill_msg]

    def route_and_inject(
        self,
        messages: List[Dict[str, Any]],
        task: str,
        *,
        max_results: int = 3,
        min_confidence: float = 0.7,
    ) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """Auto-route: search for relevant skills and inject the best match.

        Returns (updated_messages, injected_skill_name_or_None).
        Only injects when a single clear winner scores above min_confidence.
        """
        hits = self._registry.search(
            task, owner=self.owner,
            max_results=max_results,
            min_confidence=min_confidence,
        )
        if not hits:
            return messages, None
        best = hits[0]
        messages = self.inject_skill(messages, best["name"])
        return messages, best["name"]

    # ------------------------------------------------------------------
    # Compaction guard
    # ------------------------------------------------------------------

    def maybe_compact(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run the 80% compaction guard if CompactionMiddleware is available."""
        if not self._compaction:
            return messages
        try:
            return self._compaction.maybe_compact(messages, model=model)
        except Exception as e:
            logger.warning(f"Compaction guard failed: {e}")
            return messages

    # ------------------------------------------------------------------
    # Token tracking
    # ------------------------------------------------------------------

    def record_turn(
        self,
        tokens_used: int,
        *,
        skill_name: Optional[str] = None,
        source: str = "chat",
    ) -> None:
        """Record token usage for the current turn."""
        self._tokens_used += tokens_used
        nm = skill_name or self._active_skill
        if self._token_counter and self._session_id:
            try:
                self._token_counter.record(
                    session_id=self._session_id,
                    tokens_in=tokens_used,
                    tokens_out=0,
                    model="unknown",
                    source=f"skill:{nm}" if nm else source,
                )
            except Exception as e:
                logger.debug(f"TokenCounter.record failed: {e}")

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    # ------------------------------------------------------------------
    # Training mode flag (used by TrainingInterface)
    # ------------------------------------------------------------------

    @property
    def training_mode(self) -> bool:
        return self._training_mode

    def set_training_mode(self, enabled: bool) -> None:
        self._training_mode = enabled
        logger.info(f"AgentHarness training_mode={'ON' if enabled else 'OFF'} (session={self._session_id})")

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def status(self) -> Dict:
        return {
            "session_id": self._session_id,
            "owner": self.owner,
            "tokens_used": self._tokens_used,
            "active_skill": self._active_skill,
            "training_mode": self._training_mode,
            "skill_count": len(self._registry.compact_index(owner=self.owner)),
            "estimated_skill_tokens": self._registry.estimated_tokens(owner=self.owner),
        }
