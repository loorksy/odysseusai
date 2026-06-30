"""ReflectionEngine — Post-turn self-reflection and lesson extraction (C31).

After each agent turn the ReflectionEngine can be invoked to:
  1. Evaluate whether the assistant's response actually solved the task.
  2. Identify gaps: missing steps, wrong assumptions, skipped verification.
  3. Extract a concise "lesson" sentence usable by ImprovementPlanner.
  4. Flag the turn for teacher escalation if quality is below threshold.

The engine works in two modes:
  - lightweight : heuristic-only (no LLM call); fast, used on every turn
  - deep        : LLM-driven reflection; used when lightweight flags issues
                  or when explicitly requested (e.g., after a FAIL audit)

Output is a ReflectionReport dataclass.  The agent_harness calls
`reflect(turn)` and may invoke TeacherEscalation if `should_escalate` is True.

Public API:
  engine = ReflectionEngine(llm_fn=None, quality_scorer=None)
  report = engine.reflect(turn, *, mode="lightweight")  → ReflectionReport
  engine.reflect_batch(turns)                           → list[ReflectionReport]
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_QUALITY_ESCALATE_THRESHOLD = 0.40   # below this → flag for escalation
_QUALITY_DEEP_THRESHOLD     = 0.60   # below this → trigger deep reflection

_REFLECT_PROMPT = """\
You are a rigorous self-reviewer. Analyse the following assistant turn.

User task: {task}
Assistant response: {response}
Skill used (if any): {skill}

Answer these questions in order:
1. Did the response fully solve the task? (YES / PARTIAL / NO)
2. What was the biggest weakness or gap, if any? (one sentence or "none")
3. What lesson should be remembered for next time? (one sentence)
4. Quality score 1-10.

Respond in EXACTLY this format:
SOLVED: <YES|PARTIAL|NO>
GAP: <sentence or none>
LESSON: <sentence>
SCORE: <1-10>
"""


@dataclass
class ReflectionReport:
    turn_id:          str
    solved:           str           # "YES" | "PARTIAL" | "NO" | "UNKNOWN"
    gap:              str           # biggest weakness, or "none"
    lesson:           str           # extractable lesson
    quality_score:    float         # 0.0 – 1.0
    should_escalate:  bool
    mode:             str           # "lightweight" | "deep"
    skill_used:       Optional[str]
    reflected_at:     float = field(default_factory=time.time)
    raw_response:     str = ""


class ReflectionEngine:
    """Evaluates agent turns and extracts lessons for self-improvement."""

    def __init__(
        self,
        llm_fn: Optional[Callable[[List[Dict]], str]] = None,
        quality_scorer=None,
    ):
        self._llm     = llm_fn
        self._scorer  = quality_scorer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reflect(
        self,
        turn: Dict[str, Any],
        *,
        mode: str = "lightweight",
    ) -> ReflectionReport:
        """Reflect on a single agent turn dict.

        Expected turn keys:
          id           str   — unique turn identifier
          task         str   — user's request
          response     str   — assistant's reply
          skill_used   str   — skill name or ""
          messages     list  — full message history (optional)
        """
        turn_id   = str(turn.get("id", f"turn-{time.time():.0f}"))
        task      = str(turn.get("task", ""))
        response  = str(turn.get("response", ""))
        skill     = turn.get("skill_used") or ""

        lw_report = self._lightweight(turn_id, task, response, skill)

        if mode == "lightweight" and lw_report.quality_score >= _QUALITY_DEEP_THRESHOLD:
            return lw_report

        if self._llm:
            return self._deep(turn_id, task, response, skill, lw_report)

        return lw_report

    def reflect_batch(
        self,
        turns: List[Dict[str, Any]],
        *,
        mode: str = "lightweight",
    ) -> List[ReflectionReport]:
        return [self.reflect(t, mode=mode) for t in turns]

    # ------------------------------------------------------------------
    # Lightweight heuristic reflection
    # ------------------------------------------------------------------

    def _lightweight(self, turn_id, task, response, skill) -> ReflectionReport:
        score = self._heuristic_score(task, response)
        if self._scorer and skill:
            # Boost score if a high-quality skill was used
            skills = []
            try:
                pass  # scorer.score_skill needs the skill dict; skip here
            except Exception:
                pass

        gap    = self._heuristic_gap(task, response)
        solved = "YES" if score >= 0.75 else ("PARTIAL" if score >= 0.45 else "NO")
        lesson = f"Improve response completeness for task type: {task[:60]}" if score < 0.75 else "none"

        return ReflectionReport(
            turn_id=turn_id,
            solved=solved,
            gap=gap,
            lesson=lesson,
            quality_score=score,
            should_escalate=score < _QUALITY_ESCALATE_THRESHOLD,
            mode="lightweight",
            skill_used=skill or None,
        )

    @staticmethod
    def _heuristic_score(task: str, response: str) -> float:
        """Fast heuristic quality estimate."""
        if not response or not response.strip():
            return 0.0
        score = 0.5
        # Response length relative to task complexity
        task_words     = max(len(task.split()), 1)
        response_words = len(response.split())
        length_ratio   = min(response_words / (task_words * 3), 1.0)
        score += 0.2 * length_ratio
        # Presence of uncertainty markers (hedging without delivery)
        hedges = ("i'm not sure", "i don't know", "i cannot", "i can't", "sorry")
        if any(h in response.lower() for h in hedges):
            score -= 0.15
        # Code / structure suggests effort
        if "```" in response or "\n1." in response or "\n- " in response:
            score += 0.15
        return round(min(max(score, 0.0), 1.0), 4)

    @staticmethod
    def _heuristic_gap(task: str, response: str) -> str:
        if not response.strip():
            return "Empty response"
        if len(response.split()) < 10:
            return "Response too short for the task"
        hedges = ("i'm not sure", "i don't know", "i cannot", "i can't")
        if any(h in response.lower() for h in hedges):
            return "Response expressed uncertainty without resolving it"
        return "none"

    # ------------------------------------------------------------------
    # Deep LLM-driven reflection
    # ------------------------------------------------------------------

    def _deep(self, turn_id, task, response, skill, lw_report) -> ReflectionReport:
        prompt = _REFLECT_PROMPT.format(
            task=task[:500],
            response=response[:800],
            skill=skill or "(none)",
        )
        try:
            raw = self._llm([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.warning(f"ReflectionEngine: LLM call failed: {e}")
            return lw_report

        solved, gap, lesson, score = self._parse_deep(raw, lw_report.quality_score)
        return ReflectionReport(
            turn_id=turn_id,
            solved=solved,
            gap=gap,
            lesson=lesson,
            quality_score=score,
            should_escalate=score < _QUALITY_ESCALATE_THRESHOLD,
            mode="deep",
            skill_used=skill or None,
            raw_response=raw,
        )

    @staticmethod
    def _parse_deep(raw: str, fallback_score: float) -> tuple:
        solved = "UNKNOWN"
        gap    = "none"
        lesson = ""
        score  = fallback_score
        for line in raw.splitlines():
            s = line.strip()
            if s.upper().startswith("SOLVED:"):
                v = s.split(":", 1)[1].strip().upper()
                if v in ("YES", "PARTIAL", "NO"):
                    solved = v
            elif s.upper().startswith("GAP:"):
                gap = s.split(":", 1)[1].strip()
            elif s.upper().startswith("LESSON:"):
                lesson = s.split(":", 1)[1].strip()
            elif s.upper().startswith("SCORE:"):
                try:
                    raw_score = int(s.split(":", 1)[1].strip().split()[0])
                    score = round(max(0.0, min((raw_score - 1) / 9.0, 1.0)), 4)
                except Exception:
                    pass
        return solved, gap, lesson or "none", score
