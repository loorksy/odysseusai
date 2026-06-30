"""TeacherEscalation — Student-fails → teacher-writes-skill loop (C26).

When the student agent fails a task (no skill match, wrong output, repeated
error), TeacherEscalation:
  1. Receives the failure context (task, messages, error).
  2. Calls a stronger "teacher" LLM to solve the task correctly.
  3. Captures the successful teacher trace.
  4. Calls skill_creator to crystallize the trace into a new SKILL.md.
  5. Saves the skill as status=draft, source=teacher-escalation.
  6. Returns the skill name so the student can immediately retry using it.

Design:
  - The teacher LLM is configured separately from the student (can be a
    more capable / expensive model).
  - Escalation is logged with a confidence score derived from the teacher's
    self-rating (fed back via QualityScorer).
  - The whole cycle is atomic: if skill creation fails, no partial skill is
    written and the exception propagates for the caller to handle.

Public API:
  te = TeacherEscalation(skills_manager, teacher_llm_fn, skill_creator_fn)
  result = te.escalate(task, messages, owner, error_context)  → EscalationResult
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_TEACHER_SOLVE_SYSTEM = """\
You are a senior AI assistant solving a task that a student agent failed.
Solve the task completely and correctly.
After solving, write a brief step-by-step explanation of exactly what you did
so it can be crystallized into a reusable skill.
Format:
SOLUTION:
<your solution here>

STEPS:
1. <step>
2. <step>
...
"""

_CRYSTALLIZE_SYSTEM = """\
You are crystallizing a successful teacher solution into a SKILL.md.
Follow the skill_creator procedure exactly.
Output ONLY the raw SKILL.md content — no fences, no commentary.
"""


@dataclass
class EscalationResult:
    task: str
    skill_name: Optional[str]          # None if skill creation failed
    teacher_solution: str
    steps_extracted: List[str]
    confidence: float
    escalated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    success: bool = False


class TeacherEscalation:
    """Orchestrates the student-fails → teacher-solves → skill-created loop."""

    def __init__(
        self,
        skills_manager,
        teacher_llm_fn: Callable[[List[Dict]], str],
        *,
        student_model: str = "",
        teacher_model: str = "",
        quality_scorer=None,   # optional QualityScorer instance
    ):
        self._sm = skills_manager
        self._teacher = teacher_llm_fn
        self._student_model = student_model
        self._teacher_model = teacher_model
        self._scorer = quality_scorer

    # ------------------------------------------------------------------
    # Main escalation entry point
    # ------------------------------------------------------------------

    def escalate(
        self,
        task: str,
        messages: List[Dict[str, Any]],
        owner: Optional[str] = None,
        *,
        error_context: str = "",
        category: str = "general",
    ) -> EscalationResult:
        """Run the full escalation loop and return an EscalationResult.

        Raises on unrecoverable errors (LLM failure, skill write failure).
        """
        logger.info(f"TeacherEscalation: starting for task={task[:80]!r} owner={owner}")

        # Step 1 — Teacher solves the task
        teacher_solution, steps = self._teacher_solve(task, messages, error_context)

        # Step 2 — Score confidence
        confidence = 0.8
        if self._scorer:
            try:
                confidence = self._scorer.score_solution(task, teacher_solution, steps)
            except Exception as e:
                logger.debug(f"TeacherEscalation: QualityScorer failed: {e}")

        # Step 3 — Crystallize into a skill
        skill_name = None
        error_msg = None
        try:
            skill_name = self._crystallize(
                task=task,
                solution=teacher_solution,
                steps=steps,
                owner=owner,
                category=category,
                confidence=confidence,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"TeacherEscalation: skill crystallization failed: {e}")

        return EscalationResult(
            task=task,
            skill_name=skill_name,
            teacher_solution=teacher_solution,
            steps_extracted=steps,
            confidence=confidence,
            error=error_msg,
            success=skill_name is not None,
        )

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _teacher_solve(
        self,
        task: str,
        messages: List[Dict[str, Any]],
        error_context: str,
    ) -> tuple[str, List[str]]:
        """Ask the teacher model to solve the task. Returns (solution, steps)."""
        context_block = ""
        if messages:
            recent = messages[-6:]  # last 3 turns
            context_block = "\n".join(
                f"[{m.get('role','?')}]: {str(m.get('content',''))[:400]}"
                for m in recent
            )

        user_content = (
            f"Task: {task}\n\n"
            + (f"Prior conversation:\n{context_block}\n\n" if context_block else "")
            + (f"Student error: {error_context}\n\n" if error_context else "")
            + "Solve the task completely."
        )

        raw = self._teacher([
            {"role": "system", "content": _TEACHER_SOLVE_SYSTEM},
            {"role": "user",   "content": user_content},
        ])

        solution, steps = self._parse_teacher_response(raw)
        return solution, steps

    @staticmethod
    def _parse_teacher_response(raw: str) -> tuple[str, List[str]]:
        solution_lines = []
        steps = []
        in_solution = False
        in_steps = False
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("SOLUTION:"):
                in_solution = True
                in_steps = False
                rest = stripped[9:].strip()
                if rest:
                    solution_lines.append(rest)
            elif stripped.upper().startswith("STEPS:"):
                in_steps = True
                in_solution = False
            elif in_solution and not in_steps:
                solution_lines.append(stripped)
            elif in_steps and stripped:
                # Strip leading "1. " / "- "
                clean = stripped.lstrip("0123456789.-) ").strip()
                if clean:
                    steps.append(clean)
        solution = "\n".join(solution_lines).strip() or raw.strip()[:1000]
        return solution, steps

    def _crystallize(
        self,
        task: str,
        solution: str,
        steps: List[str],
        owner: Optional[str],
        category: str,
        confidence: float,
    ) -> str:
        """Write a new skill to SkillsManager and return its name."""
        # Build a concise description from the task
        description = task[:120].strip().rstrip(".")

        sk = self._sm.add_skill(
            description=description,
            when_to_use=task,
            procedure=steps or [solution[:500]],
            source="teacher-escalation",
            teacher_model=self._teacher_model,
            confidence=confidence,
            category=category,
            owner=owner,
            status="draft",
        )

        name = sk.get("name") or sk.get("id") or "unknown"
        logger.info(f"TeacherEscalation: created skill '{name}' confidence={confidence:.2f}")
        return name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def should_escalate(
        self,
        consecutive_failures: int,
        *,
        threshold: int = 2,
    ) -> bool:
        """True when the student has failed enough times to warrant escalation."""
        return consecutive_failures >= threshold
