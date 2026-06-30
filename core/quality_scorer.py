"""QualityScorer — Multi-signal quality scoring for skills and agent outputs (C27).

Scores are used by:
  - TeacherEscalation: to set the confidence field on newly crystallized skills
  - SkillAuditor: to rank skills for the "audit next" queue
  - Token Panel: to surface low-quality skills for human review

Scoring signals (all weighted, combined into 0.0–1.0):
  1. Completeness    : does the skill have all required sections?
  2. Procedure depth : are there enough specific steps?
  3. Verification    : does it have a checklist?
  4. LLM self-rating : optional; calls the LLM to rate the output 1–10
  5. Usage history   : skills with high use + no FAIL audits score higher
  6. Freshness       : skills updated recently score higher

Public API:
  scorer = QualityScorer(llm_fn=None)
  scorer.score_skill(skill_dict)              → float 0.0–1.0
  scorer.score_solution(task, solution, steps) → float 0.0–1.0
  scorer.rank_skills(skill_list)              → sorted list (highest first)
  scorer.score_all(skills_manager, owner)     → [{name, score, signals}]
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal weights (must sum to 1.0)
# ---------------------------------------------------------------------------
_W_COMPLETENESS  = 0.20
_W_DEPTH         = 0.20
_W_VERIFICATION  = 0.15
_W_LLM_RATING    = 0.25
_W_USAGE         = 0.10
_W_FRESHNESS     = 0.10

_LLM_RATE_PROMPT = """\
Rate the quality of this skill procedure on a scale of 1 to 10.
Skill: {name}
Description: {description}
Procedure:
{procedure}

Respond with ONLY a single integer between 1 and 10.
"""

_SOLUTION_RATE_PROMPT = """\
Rate the quality of this solution on a scale of 1 to 10.
Task: {task}
Solution: {solution}
Steps taken:
{steps}

Respond with ONLY a single integer between 1 and 10.
"""


class QualityScorer:
    """Multi-signal skill and solution quality scorer."""

    def __init__(self, llm_fn: Optional[Callable[[List[Dict]], str]] = None):
        self._llm = llm_fn

    # ------------------------------------------------------------------
    # Skill scoring
    # ------------------------------------------------------------------

    def score_skill(self, skill: Dict[str, Any]) -> float:
        """Return a 0.0–1.0 quality score for a skill dict."""
        signals = self._compute_skill_signals(skill)
        return self._combine(signals)

    def _compute_skill_signals(self, skill: Dict[str, Any]) -> Dict[str, float]:
        signals: Dict[str, float] = {}

        # 1. Completeness: required fields present and non-empty
        required = ["name", "description", "when_to_use", "procedure"]
        filled = sum(1 for f in required if skill.get(f))
        signals["completeness"] = filled / len(required)

        # 2. Procedure depth: 1+ steps is minimum; 5+ is ideal
        steps = skill.get("procedure") or []
        if isinstance(steps, list):
            n = len([s for s in steps if str(s).strip()])
        else:
            n = 1 if str(steps).strip() else 0
        signals["depth"] = min(n / 5.0, 1.0)

        # 3. Verification checklist
        verification = skill.get("verification") or []
        has_verification = len(verification) > 0 if isinstance(verification, list) else bool(verification)
        signals["verification"] = 1.0 if has_verification else 0.0

        # 4. LLM self-rating (optional)
        if self._llm:
            signals["llm_rating"] = self._llm_rate_skill(skill)
        else:
            # No LLM: redistribute weight across other signals
            signals["llm_rating"] = self._heuristic_quality(skill)

        # 5. Usage history
        uses = int(skill.get("uses", 0))
        audit = skill.get("audit_verdict")
        if audit == "FAIL":
            usage_score = 0.2
        elif uses == 0:
            usage_score = 0.5
        else:
            usage_score = min(1.0, 0.5 + uses / 20.0)
        signals["usage"] = usage_score

        # 6. Freshness: skills used/updated within 30 days score higher
        last_used = skill.get("last_used") or skill.get("audited_at")
        if last_used:
            age_days = (time.time() - float(last_used)) / 86_400
            signals["freshness"] = max(0.0, 1.0 - age_days / 30.0)
        else:
            signals["freshness"] = 0.5  # unknown age — neutral

        return signals

    @staticmethod
    def _combine(signals: Dict[str, float]) -> float:
        weights = {
            "completeness": _W_COMPLETENESS,
            "depth":        _W_DEPTH,
            "verification": _W_VERIFICATION,
            "llm_rating":   _W_LLM_RATING,
            "usage":        _W_USAGE,
            "freshness":    _W_FRESHNESS,
        }
        total = sum(signals.get(k, 0.0) * w for k, w in weights.items())
        return round(min(max(total, 0.0), 1.0), 4)

    def _llm_rate_skill(self, skill: Dict[str, Any]) -> float:
        """Call the LLM to rate the skill 1–10, return normalized 0.0–1.0."""
        steps = skill.get("procedure") or []
        prompt = _LLM_RATE_PROMPT.format(
            name=skill.get("name", ""),
            description=skill.get("description", ""),
            procedure="\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) or "(none)",
        )
        try:
            raw = self._llm([{"role": "user", "content": prompt}])
            rating = int(raw.strip().split()[0])
            return max(0.0, min((rating - 1) / 9.0, 1.0))
        except Exception:
            return 0.5

    @staticmethod
    def _heuristic_quality(skill: Dict[str, Any]) -> float:
        """Cheap heuristic quality estimate (no LLM required)."""
        score = 0.5
        desc_len = len(skill.get("description", ""))
        if desc_len > 80:
            score += 0.2
        elif desc_len < 20:
            score -= 0.2
        if skill.get("tags"):
            score += 0.1
        if skill.get("pitfalls"):
            score += 0.1
        return round(min(max(score, 0.0), 1.0), 4)

    # ------------------------------------------------------------------
    # Solution scoring (used by TeacherEscalation)
    # ------------------------------------------------------------------

    def score_solution(
        self,
        task: str,
        solution: str,
        steps: List[str],
    ) -> float:
        """Score a teacher solution for confidence when crystallizing a skill."""
        # Heuristic base: solution length + step count
        step_score = min(len(steps) / 5.0, 1.0)
        length_score = min(len(solution) / 500.0, 1.0)
        base = 0.4 * step_score + 0.3 * length_score + 0.3

        if not self._llm:
            return round(min(base, 1.0), 4)

        prompt = _SOLUTION_RATE_PROMPT.format(
            task=task[:300],
            solution=solution[:600],
            steps="\n".join(f"{i+1}. {s}" for i, s in enumerate(steps[:10])),
        )
        try:
            raw = self._llm([{"role": "user", "content": prompt}])
            rating = int(raw.strip().split()[0])
            llm_score = max(0.0, min((rating - 1) / 9.0, 1.0))
            return round(0.5 * base + 0.5 * llm_score, 4)
        except Exception:
            return round(min(base, 1.0), 4)

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def rank_skills(
        self,
        skills: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Return skills sorted by quality score descending."""
        scored = [(self.score_skill(s), s) for s in skills]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    def score_all(
        self,
        skills_manager,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Score every skill for the owner and return [{name, score, signals}]."""
        skills = skills_manager.load(owner=owner)
        results = []
        for sk in skills:
            signals = self._compute_skill_signals(sk)
            score   = self._combine(signals)
            results.append({
                "name":    sk["name"],
                "score":   score,
                "signals": {k: round(v, 4) for k, v in signals.items()},
            })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results
