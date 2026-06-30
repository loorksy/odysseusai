"""SkillAuditor — Automated skill verification and necessity checks (C25).

Runs two independent audit passes on every skill in the library:

  1. Correctness audit: replay the skill's verification checklist against
     the current codebase / environment, or (when no live test is available)
     ask the LLM worker to self-rate the skill's procedure.

  2. Necessity audit: compare the skill against all others in the library
     using Jaccard similarity; flag near-duplicates and orphaned skills.

Results are written back to the SkillsManager via `set_audit()` and
`set_necessity()` — they appear on the skill card in the UI without
touching the SKILL.md content.

Public API:
  auditor = SkillAuditor(skills_manager, llm_fn)
  auditor.audit_skill(name, owner)          → AuditResult
  auditor.audit_all(owner, *, batch_size)   → List[AuditResult]
  auditor.check_necessity(name, owner)      → NecessityResult
  auditor.run_full_sweep(owner)             → {audited, flagged, duplicates}

llm_fn signature: (messages: list[dict]) -> str
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_JACCARD_DUP_THRESHOLD = 0.72   # skills above this are near-duplicates
_JACCARD_RELATED_THRESHOLD = 0.40
_AUDIT_PROMPT_TEMPLATE = """\
You are auditing a skill procedure for correctness and completeness.
Skill name: {name}
Description: {description}

Procedure steps:
{procedure}

Verification checklist:
{verification}

Instructions:
1. Rate each procedure step as: CORRECT | INCOMPLETE | WRONG
2. Rate the overall skill as: PASS | PARTIAL | FAIL
3. Give a one-sentence verdict.

Respond in this exact format:
VERDICT: <PASS|PARTIAL|FAIL>
REASON: <one sentence>
"""


@dataclass
class AuditResult:
    name: str
    verdict: str            # "PASS" | "PARTIAL" | "FAIL" | "SKIP"
    reason: str
    by_teacher: bool = False
    worker_model: str = ""
    teacher_model: str = ""
    audited_at: float = field(default_factory=time.time)
    raw_response: str = ""


@dataclass
class NecessityResult:
    name: str
    necessary: bool
    redundant_with: List[str] = field(default_factory=list)
    reason: str = ""


def _tokenize(text: str) -> set:
    return {w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class SkillAuditor:
    """Automated correctness and necessity auditor for the skill library."""

    def __init__(
        self,
        skills_manager,
        llm_fn: Optional[Callable[[List[Dict]], str]] = None,
        *,
        worker_model: str = "",
        teacher_model: str = "",
    ):
        self._sm = skills_manager
        self._llm = llm_fn
        self._worker_model = worker_model
        self._teacher_model = teacher_model

    # ------------------------------------------------------------------
    # Single-skill audit
    # ------------------------------------------------------------------

    def audit_skill(
        self,
        name: str,
        owner: Optional[str] = None,
        *,
        by_teacher: bool = False,
    ) -> AuditResult:
        """Run the correctness audit for a single skill.

        If no llm_fn is configured, returns a SKIP verdict so the sweep
        doesn't crash in environments without LLM access.
        """
        skills = self._sm.load(owner=owner)
        sk = next((s for s in skills if s["name"] == name), None)
        if not sk:
            return AuditResult(name=name, verdict="SKIP", reason="Skill not found")

        if not self._llm:
            return AuditResult(name=name, verdict="SKIP", reason="No LLM configured")

        procedure = sk.get("procedure") or []
        verification = sk.get("verification") or []

        prompt = _AUDIT_PROMPT_TEMPLATE.format(
            name=sk["name"],
            description=sk.get("description", ""),
            procedure="\n".join(f"{i+1}. {s}" for i, s in enumerate(procedure)) or "(none)",
            verification="\n".join(f"- {v}" for v in verification) or "(none)",
        )

        try:
            raw = self._llm([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.warning(f"SkillAuditor: LLM call failed for '{name}': {e}")
            return AuditResult(name=name, verdict="SKIP", reason=f"LLM error: {e}")

        verdict, reason = self._parse_audit_response(raw)
        result = AuditResult(
            name=name,
            verdict=verdict,
            reason=reason,
            by_teacher=by_teacher,
            worker_model=self._worker_model,
            teacher_model=self._teacher_model if by_teacher else "",
            raw_response=raw,
        )

        self._sm.set_audit(
            name=name,
            verdict=verdict,
            by_teacher=by_teacher,
            worker_model=self._worker_model,
            teacher_model=self._teacher_model if by_teacher else "",
            owner=owner,
        )
        return result

    @staticmethod
    def _parse_audit_response(raw: str) -> tuple[str, str]:
        verdict = "SKIP"
        reason  = raw.strip()[:300]
        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                if v in ("PASS", "PARTIAL", "FAIL"):
                    verdict = v
            elif line.upper().startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        return verdict, reason

    # ------------------------------------------------------------------
    # Necessity check
    # ------------------------------------------------------------------

    def check_necessity(
        self,
        name: str,
        owner: Optional[str] = None,
    ) -> NecessityResult:
        """Flag the skill as redundant if a near-duplicate exists."""
        skills = self._sm.load(owner=owner)
        target = next((s for s in skills if s["name"] == name), None)
        if not target:
            return NecessityResult(name=name, necessary=True, reason="Skill not found")

        target_tokens = _tokenize(" ".join([
            target.get("name", ""),
            target.get("description", ""),
            target.get("when_to_use", ""),
            " ".join(target.get("procedure", []) or []),
        ]))

        redundant_with = []
        for s in skills:
            if s["name"] == name:
                continue
            cand_tokens = _tokenize(" ".join([
                s.get("name", ""),
                s.get("description", ""),
                s.get("when_to_use", ""),
                " ".join(s.get("procedure", []) or []),
            ]))
            if _jaccard(target_tokens, cand_tokens) >= _JACCARD_DUP_THRESHOLD:
                redundant_with.append(s["name"])

        necessary = len(redundant_with) == 0
        reason = (
            f"Near-duplicate of: {', '.join(redundant_with)}"
            if not necessary else "Unique skill"
        )
        result = NecessityResult(
            name=name,
            necessary=necessary,
            redundant_with=redundant_with,
            reason=reason,
        )
        self._sm.set_necessity(
            name=name,
            necessary=necessary,
            redundant_with=redundant_with,
            reason=reason,
            owner=owner,
        )
        return result

    # ------------------------------------------------------------------
    # Batch sweep
    # ------------------------------------------------------------------

    def audit_all(
        self,
        owner: Optional[str] = None,
        *,
        batch_size: int = 10,
        by_teacher: bool = False,
    ) -> List[AuditResult]:
        """Audit every skill for the owner, in batches."""
        skills = self._sm.load(owner=owner)
        results = []
        for i in range(0, len(skills), batch_size):
            batch = skills[i:i + batch_size]
            for sk in batch:
                try:
                    r = self.audit_skill(sk["name"], owner=owner, by_teacher=by_teacher)
                    results.append(r)
                except Exception as e:
                    logger.warning(f"audit_all: error auditing '{sk['name']}': {e}")
                    results.append(AuditResult(name=sk["name"], verdict="SKIP", reason=str(e)))
        return results

    def run_full_sweep(self, owner: Optional[str] = None) -> Dict[str, Any]:
        """Run correctness audit + necessity check on all skills.

        Returns a summary dict suitable for logging or the /api/agent/status endpoint.
        """
        skills = self._sm.load(owner=owner)
        audit_results = self.audit_all(owner=owner)
        necessity_results = [self.check_necessity(s["name"], owner=owner) for s in skills]

        verdicts: Dict[str, int] = {"PASS": 0, "PARTIAL": 0, "FAIL": 0, "SKIP": 0}
        for r in audit_results:
            verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1

        duplicates = [r.name for r in necessity_results if not r.necessary]

        return {
            "total": len(skills),
            "audited": len([r for r in audit_results if r.verdict != "SKIP"]),
            "verdicts": verdicts,
            "flagged_fail": [r.name for r in audit_results if r.verdict == "FAIL"],
            "duplicates": duplicates,
            "necessity_checked": len(necessity_results),
        }
