"""ImprovementPlanner — Synthesises lessons into ranked improvement actions (C33).

Consumed inputs:
  - ReflectionReport  list  from ReflectionEngine
  - AuditResult       list  from SkillAuditor
  - CritiqueResult    list  from SelfCritiquePipeline (optional)
  - QualityScorer     scores from skill_all()

Outputs a prioritised ImprovementPlan with three action types:
  SKILL_EDIT    — update an existing skill's procedure
  SKILL_CREATE  — crystallize a new skill (routes to TeacherEscalation)
  SKILL_DELETE  — retire a near-duplicate or low-quality skill

The planner is intentionally stateless: it reads evidence, produces a plan,
and returns it.  The caller (agent_harness / background worker) decides
when and how to execute the actions.

Public API:
  planner = ImprovementPlanner(skills_manager=None)
  plan = planner.build_plan(reflections, audits, scores, *, max_actions)
  plan.actions          — list[ImprovementAction] sorted by priority
  plan.summary          — human-readable summary string
  planner.top_n(plan, n) — top-n actions
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ACTIONS = 20

# Priority weights
_W_AUDIT_FAIL    = 10.0
_W_AUDIT_PARTIAL =  5.0
_W_LOW_QUALITY   =  7.0   # score < 0.4
_W_MED_QUALITY   =  3.0   # score 0.4 – 0.6
_W_ESCALATION    =  8.0   # reflection flagged for escalation
_W_LESSON        =  2.0   # unique lesson from reflection
_W_DUPLICATE     =  6.0   # near-duplicate from necessity check


@dataclass
class ImprovementAction:
    action_type:  str            # "SKILL_EDIT" | "SKILL_CREATE" | "SKILL_DELETE"
    skill_name:   Optional[str]
    reason:       str
    priority:     float
    evidence:     List[str] = field(default_factory=list)  # source IDs / descriptions
    lesson:       str = ""
    created_at:   float = field(default_factory=time.time)


@dataclass
class ImprovementPlan:
    actions:    List[ImprovementAction]
    summary:    str
    built_at:   float = field(default_factory=time.time)
    input_counts: Dict[str, int] = field(default_factory=dict)


class ImprovementPlanner:
    """Builds a prioritised improvement plan from reflection and audit evidence."""

    def __init__(self, skills_manager=None):
        self._sm = skills_manager

    # ------------------------------------------------------------------
    # Main plan builder
    # ------------------------------------------------------------------

    def build_plan(
        self,
        reflections: Optional[List[Any]] = None,
        audits:      Optional[List[Any]] = None,
        scores:      Optional[List[Dict]] = None,
        *,
        max_actions: int = _DEFAULT_MAX_ACTIONS,
    ) -> ImprovementPlan:
        """Synthesise evidence into a ranked ImprovementPlan."""
        reflections = reflections or []
        audits      = audits      or []
        scores      = scores      or []

        actions: List[ImprovementAction] = []

        # 1. Audit-driven actions
        for audit in audits:
            if getattr(audit, "verdict", None) == "FAIL":
                actions.append(ImprovementAction(
                    action_type="SKILL_EDIT",
                    skill_name=audit.name,
                    reason=f"Audit FAIL: {getattr(audit, 'reason', '')}",
                    priority=_W_AUDIT_FAIL,
                    evidence=[f"audit:{audit.name}:FAIL"],
                ))
            elif getattr(audit, "verdict", None) == "PARTIAL":
                actions.append(ImprovementAction(
                    action_type="SKILL_EDIT",
                    skill_name=audit.name,
                    reason=f"Audit PARTIAL: {getattr(audit, 'reason', '')}",
                    priority=_W_AUDIT_PARTIAL,
                    evidence=[f"audit:{audit.name}:PARTIAL"],
                ))
            # Necessity flag — from NecessityResult mixed into audits list
            if hasattr(audit, "necessary") and not audit.necessary:
                redundant = getattr(audit, "redundant_with", [])
                actions.append(ImprovementAction(
                    action_type="SKILL_DELETE",
                    skill_name=audit.name,
                    reason=f"Near-duplicate of {', '.join(redundant)}",
                    priority=_W_DUPLICATE,
                    evidence=[f"necessity:{audit.name}"],
                ))

        # 2. Quality-score-driven actions
        for entry in scores:
            score = entry.get("score", 1.0)
            name  = entry.get("name", "")
            if score < 0.40:
                actions.append(ImprovementAction(
                    action_type="SKILL_EDIT",
                    skill_name=name,
                    reason=f"Low quality score: {score:.2f}",
                    priority=_W_LOW_QUALITY * (1.0 - score),
                    evidence=[f"score:{name}:{score:.2f}"],
                ))
            elif score < 0.60:
                actions.append(ImprovementAction(
                    action_type="SKILL_EDIT",
                    skill_name=name,
                    reason=f"Medium quality score: {score:.2f}",
                    priority=_W_MED_QUALITY * (1.0 - score),
                    evidence=[f"score:{name}:{score:.2f}"],
                ))

        # 3. Reflection-driven actions
        seen_lessons: set = set()
        for rpt in reflections:
            if getattr(rpt, "should_escalate", False):
                actions.append(ImprovementAction(
                    action_type="SKILL_CREATE",
                    skill_name=None,
                    reason=f"Agent failed task (score={getattr(rpt,'quality_score',0):.2f}): {getattr(rpt,'gap','')}",
                    priority=_W_ESCALATION * (1.0 - getattr(rpt, "quality_score", 0)),
                    evidence=[f"reflection:{getattr(rpt,'turn_id','?')}"],
                    lesson=getattr(rpt, "lesson", ""),
                ))
            lesson = getattr(rpt, "lesson", "").strip()
            if lesson and lesson != "none" and lesson not in seen_lessons:
                seen_lessons.add(lesson)
                skill_name = getattr(rpt, "skill_used", None)
                if skill_name:
                    actions.append(ImprovementAction(
                        action_type="SKILL_EDIT",
                        skill_name=skill_name,
                        reason=f"Lesson from reflection: {lesson}",
                        priority=_W_LESSON,
                        evidence=[f"lesson:{getattr(rpt,'turn_id','?')}"],
                        lesson=lesson,
                    ))

        # Deduplicate and rank
        actions = self._dedup(actions)
        actions.sort(key=lambda a: a.priority, reverse=True)
        actions = actions[:max_actions]

        summary = self._summarise(actions)
        return ImprovementPlan(
            actions=actions,
            summary=summary,
            input_counts={
                "reflections": len(reflections),
                "audits":      len(audits),
                "scores":      len(scores),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup(actions: List[ImprovementAction]) -> List[ImprovementAction]:
        """Merge duplicate (action_type, skill_name) entries, keeping highest priority."""
        seen: Dict[tuple, ImprovementAction] = {}
        for a in actions:
            key = (a.action_type, a.skill_name or "__new__")
            if key not in seen or a.priority > seen[key].priority:
                seen[key] = a
            else:
                # Merge evidence lists
                seen[key].evidence.extend(a.evidence)
        return list(seen.values())

    @staticmethod
    def _summarise(actions: List[ImprovementAction]) -> str:
        if not actions:
            return "No improvement actions needed."
        edits   = [a for a in actions if a.action_type == "SKILL_EDIT"]
        creates = [a for a in actions if a.action_type == "SKILL_CREATE"]
        deletes = [a for a in actions if a.action_type == "SKILL_DELETE"]
        parts = []
        if edits:   parts.append(f"{len(edits)} skill(s) to edit")
        if creates: parts.append(f"{len(creates)} skill(s) to create")
        if deletes: parts.append(f"{len(deletes)} skill(s) to retire")
        top = actions[0]
        return f"{', '.join(parts)}. Top priority: {top.action_type} '{top.skill_name or 'new'}' (score={top.priority:.1f})."

    def top_n(self, plan: ImprovementPlan, n: int) -> List[ImprovementAction]:
        return plan.actions[:n]
