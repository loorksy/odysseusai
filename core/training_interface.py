"""TrainingInterface — Trace capture and guided workflow recording (C20).

Responsibilities:
  - Activates / deactivates training mode on an AgentHarness.
  - Captures every agent turn (user message + assistant response + tool calls)
    into an in-memory trace buffer while training mode is active.
  - Provides `crystallize()` which formats the trace into a payload suitable
    for the skill_creator meta-skill or POST /api/skills/generate.
  - Optionally appends AI annotation prompts after each captured turn
    (Teach Mode Q&A: the agent clarifies its own step for the trace).

Usage:
  ti = TrainingInterface(harness)
  ti.start(session_id)

  # Per turn (inside your chat loop):
  ti.capture_turn(user_msg, assistant_reply, tool_calls=[])

  # When user says "save this as a skill":
  payload = ti.crystallize(goal="describe the overall goal")
  # payload["trace"] and payload["system_prompt"] are ready for skill_creator

  ti.stop()
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TraceEntry:
    """One captured turn in the training trace."""

    __slots__ = ("turn", "timestamp", "user", "assistant", "tool_calls", "annotation")

    def __init__(
        self,
        turn: int,
        user: str,
        assistant: str,
        tool_calls: Optional[List[Dict]] = None,
        annotation: Optional[str] = None,
    ):
        self.turn = turn
        self.timestamp = time.time()
        self.user = user
        self.assistant = assistant
        self.tool_calls = tool_calls or []
        self.annotation = annotation  # optional AI-added step clarification

    def to_dict(self) -> Dict:
        return {
            "turn": self.turn,
            "timestamp": self.timestamp,
            "user": self.user,
            "assistant": self.assistant,
            "tool_calls": self.tool_calls,
            "annotation": self.annotation,
        }


class TrainingInterface:
    """Manages trace capture for Teach Mode / skill crystallization."""

    # Max turns to buffer before oldest turns are dropped (FIFO).
    MAX_TURNS = 200

    def __init__(self, harness):
        """Args:
            harness: AgentHarness instance for this session.
        """
        self._harness = harness
        self._active = False
        self._session_id: Optional[str] = None
        self._trace: List[TraceEntry] = []
        self._turn_counter: int = 0
        self._started_at: Optional[float] = None
        self._goal: Optional[str] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        session_id: Optional[str] = None,
        goal: Optional[str] = None,
    ) -> None:
        """Activate training mode and begin trace capture."""
        if self._active:
            logger.debug("TrainingInterface.start called while already active — resetting trace.")
        self._active = True
        self._session_id = session_id or self._harness.session_id
        self._trace = []
        self._turn_counter = 0
        self._started_at = time.time()
        self._goal = goal
        self._harness.set_training_mode(True)
        logger.info(f"TrainingInterface started (session={self._session_id}, goal={goal!r})")

    def stop(self) -> None:
        """Deactivate training mode. Trace is preserved until next start()."""
        self._active = False
        self._harness.set_training_mode(False)
        logger.info(
            f"TrainingInterface stopped (session={self._session_id}, "
            f"turns_captured={self._turn_counter})"
        )

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def turn_count(self) -> int:
        return self._turn_counter

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture_turn(
        self,
        user_message: str,
        assistant_reply: str,
        *,
        tool_calls: Optional[List[Dict]] = None,
        annotation: Optional[str] = None,
    ) -> Optional[TraceEntry]:
        """Record one agent turn into the trace buffer.

        Returns the TraceEntry, or None if training mode is not active.
        Silently drops the oldest entry when MAX_TURNS is reached.
        """
        if not self._active:
            return None
        self._turn_counter += 1
        entry = TraceEntry(
            turn=self._turn_counter,
            user=user_message,
            assistant=assistant_reply,
            tool_calls=list(tool_calls or []),
            annotation=annotation,
        )
        self._trace.append(entry)
        if len(self._trace) > self.MAX_TURNS:
            self._trace.pop(0)
        return entry

    def annotate_last(
        self,
        annotation: str,
    ) -> bool:
        """Add an AI annotation to the most recently captured turn.

        Used by Teach Mode Q&A to attach the agent's step clarification
        without creating a new trace entry.
        Returns True if the trace was non-empty and annotation was applied.
        """
        if not self._trace:
            return False
        self._trace[-1].annotation = annotation
        return True

    # ------------------------------------------------------------------
    # Crystallization
    # ------------------------------------------------------------------

    def crystallize(
        self,
        goal: Optional[str] = None,
        *,
        include_tool_calls: bool = True,
    ) -> Dict[str, Any]:
        """Format the captured trace for skill_creator / POST /api/skills/generate.

        Returns a dict with:
          - "goal": the stated goal for this workflow
          - "session_id": session that was traced
          - "turns": list of turn dicts (user / assistant / tool_calls / annotation)
          - "trace": the full trace as a formatted string (what skill_creator reads)
          - "system_prompt": the instruction block to prepend when calling skill_creator
        """
        effective_goal = goal or self._goal or "(no goal specified)"
        turns = [e.to_dict() for e in self._trace]
        trace_text = self._format_trace(effective_goal, include_tool_calls=include_tool_calls)

        system_prompt = (
            "You are crystallizing a workflow into a reusable SKILL.md.\n"
            "The trace below captures a successful agent session.\n"
            f"Goal: {effective_goal}\n\n"
            "Follow the skill_creator meta-skill procedure exactly.\n"
            "Output ONLY the raw SKILL.md content — no fences, no commentary."
        )

        return {
            "goal": effective_goal,
            "session_id": self._session_id,
            "started_at": self._started_at,
            "turns": turns,
            "trace": trace_text,
            "system_prompt": system_prompt,
        }

    def _format_trace(self, goal: str, *, include_tool_calls: bool = True) -> str:
        """Render the trace buffer as a clean text block for skill_creator."""
        lines = [
            f"=== WORKFLOW TRACE ===",
            f"Goal: {goal}",
            f"Session: {self._session_id}",
            f"Turns captured: {len(self._trace)}",
            "",
        ]
        for e in self._trace:
            lines.append(f"--- Turn {e.turn} ---")
            lines.append(f"User: {e.user}")
            if include_tool_calls and e.tool_calls:
                for tc in e.tool_calls:
                    tool = tc.get("tool") or tc.get("name") or "tool"
                    args = tc.get("args") or tc.get("command") or tc.get("input") or ""
                    out = tc.get("output") or tc.get("result") or ""
                    lines.append(f"  [tool:{tool}] {str(args)[:200]}")
                    if out:
                        lines.append(f"  [output] {str(out)[:400]}")
            lines.append(f"Assistant: {e.assistant}")
            if e.annotation:
                lines.append(f"Annotation: {e.annotation}")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def dump_json(self) -> str:
        """Dump the current trace to JSON (for persistence or debugging)."""
        return json.dumps(
            {
                "session_id": self._session_id,
                "goal": self._goal,
                "started_at": self._started_at,
                "turns": [e.to_dict() for e in self._trace],
            },
            indent=2,
        )

    def status(self) -> Dict:
        return {
            "active": self._active,
            "session_id": self._session_id,
            "goal": self._goal,
            "turns_captured": self._turn_counter,
            "buffer_size": len(self._trace),
            "started_at": self._started_at,
        }
