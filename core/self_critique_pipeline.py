"""SelfCritiquePipeline — Structured multi-pass self-critique (C32).

Drives a 3-pass critique loop on a response before it is sent:
  Pass 1 (Draft)    : Generate initial response (caller's responsibility)
  Pass 2 (Critique) : Identify flaws in the draft
  Pass 3 (Revise)   : Produce an improved response addressing the critique

Optionally a 4th verification pass checks whether the revision actually
fixed the flaws.  Each pass is logged as a CritiqueRecord.

The pipeline is opt-in and gated by a quality threshold: if the draft
already scores above `skip_threshold`, the critique passes are skipped
to save tokens.

Public API:
  pipe = SelfCritiquePipeline(llm_fn, quality_scorer=None)
  result = pipe.run(task, draft, *, context, max_passes, skip_threshold)
  result.final_response   — best response after revision
  result.records          — list[CritiqueRecord]
  result.improved         — True if revision was performed
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SKIP_THRESHOLD = 0.82
_DEFAULT_MAX_PASSES     = 2       # 1 critique + 1 revision

_CRITIQUE_PROMPT = """\
You are a strict quality reviewer. Critique the following response to this task.

Task: {task}
Draft response:
{draft}

Identify up to 3 specific flaws (factual errors, gaps, poor structure, wrong tone).
If there are no significant flaws, write: NO_FLAWS

Format:
FLAW 1: <description>
FLAW 2: <description>
FLAW 3: <description>
"""

_REVISE_PROMPT = """\
Revise the following response to fix the identified flaws.

Task: {task}
Original draft:
{draft}

Flaws to fix:
{flaws}

Write ONLY the improved response. Do not reference the critique or flaws.
"""

_VERIFY_PROMPT = """\
Verify that the revised response addresses the original flaws.

Flaws identified: {flaws}
Revised response: {revised}

Does the revision fix all the flaws? Answer YES, PARTIAL, or NO.
VERIFIED: <YES|PARTIAL|NO>
REMAINING_ISSUES: <brief description or none>
"""


@dataclass
class CritiqueRecord:
    pass_number: int
    pass_type:   str    # "critique" | "revision" | "verification"
    content:     str
    flaws:       List[str] = field(default_factory=list)
    timestamp:   float = field(default_factory=time.time)


@dataclass
class CritiqueResult:
    task:              str
    draft:             str
    final_response:    str
    improved:          bool
    records:           List[CritiqueRecord] = field(default_factory=list)
    verified:          str = ""   # "YES" | "PARTIAL" | "NO" | ""
    remaining_issues:  str = ""
    passes_run:        int = 0
    skipped:           bool = False


class SelfCritiquePipeline:
    """Multi-pass self-critique and revision pipeline."""

    def __init__(
        self,
        llm_fn: Callable[[List[Dict]], str],
        quality_scorer=None,
    ):
        self._llm    = llm_fn
        self._scorer = quality_scorer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        task: str,
        draft: str,
        *,
        context: Optional[List[Dict[str, Any]]] = None,
        max_passes: int = _DEFAULT_MAX_PASSES,
        skip_threshold: float = _DEFAULT_SKIP_THRESHOLD,
        run_verification: bool = False,
    ) -> CritiqueResult:
        """Run critique loop. Returns CritiqueResult."""
        # Gate: skip if draft is already high quality
        if self._scorer:
            try:
                draft_score = self._scorer.score_solution(task, draft, [])
                if draft_score >= skip_threshold:
                    return CritiqueResult(
                        task=task, draft=draft, final_response=draft,
                        improved=False, skipped=True,
                    )
            except Exception:
                pass

        records: List[CritiqueRecord] = []
        current  = draft
        improved = False

        for pass_num in range(1, max_passes + 1):
            # --- Critique pass ---
            flaws = self._critique(task, current)
            records.append(CritiqueRecord(
                pass_number=pass_num,
                pass_type="critique",
                content="\n".join(flaws),
                flaws=flaws,
            ))

            if not flaws or flaws == ["NO_FLAWS"]:
                logger.debug(f"SelfCritiquePipeline: pass {pass_num} found no flaws, stopping")
                break

            # --- Revision pass ---
            revised = self._revise(task, current, flaws)
            records.append(CritiqueRecord(
                pass_number=pass_num,
                pass_type="revision",
                content=revised,
            ))
            current  = revised
            improved = True

        result = CritiqueResult(
            task=task,
            draft=draft,
            final_response=current,
            improved=improved,
            records=records,
            passes_run=len([r for r in records if r.pass_type == "critique"]),
        )

        # Optional verification pass
        if run_verification and improved:
            all_flaws = [f for r in records if r.pass_type == "critique" for f in r.flaws]
            verified, remaining = self._verify(current, all_flaws)
            result.verified          = verified
            result.remaining_issues  = remaining

        return result

    # ------------------------------------------------------------------
    # Critique pass
    # ------------------------------------------------------------------

    def _critique(self, task: str, draft: str) -> List[str]:
        prompt = _CRITIQUE_PROMPT.format(task=task[:400], draft=draft[:800])
        try:
            raw = self._llm([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.warning(f"SelfCritiquePipeline: critique LLM failed: {e}")
            return []

        if "NO_FLAWS" in raw.upper():
            return ["NO_FLAWS"]

        flaws = []
        for line in raw.splitlines():
            s = line.strip()
            for prefix in ("FLAW 1:", "FLAW 2:", "FLAW 3:", "FLAW:", "-"):
                if s.upper().startswith(prefix):
                    flaw = s[len(prefix):].strip()
                    if flaw:
                        flaws.append(flaw)
                    break
        return flaws or [raw.strip()[:300]]

    # ------------------------------------------------------------------
    # Revision pass
    # ------------------------------------------------------------------

    def _revise(self, task: str, draft: str, flaws: List[str]) -> str:
        flaws_text = "\n".join(f"- {f}" for f in flaws)
        prompt = _REVISE_PROMPT.format(
            task=task[:400],
            draft=draft[:800],
            flaws=flaws_text,
        )
        try:
            return self._llm([{"role": "user", "content": prompt}]).strip()
        except Exception as e:
            logger.warning(f"SelfCritiquePipeline: revision LLM failed: {e}")
            return draft

    # ------------------------------------------------------------------
    # Verification pass
    # ------------------------------------------------------------------

    def _verify(self, revised: str, flaws: List[str]) -> tuple[str, str]:
        flaws_text = "\n".join(f"- {f}" for f in flaws)
        prompt = _VERIFY_PROMPT.format(flaws=flaws_text, revised=revised[:800])
        try:
            raw = self._llm([{"role": "user", "content": prompt}])
        except Exception as e:
            logger.warning(f"SelfCritiquePipeline: verify LLM failed: {e}")
            return "", ""
        verified = ""
        remaining = ""
        for line in raw.splitlines():
            s = line.strip()
            if s.upper().startswith("VERIFIED:"):
                v = s.split(":", 1)[1].strip().upper()
                if v in ("YES", "PARTIAL", "NO"):
                    verified = v
            elif s.upper().startswith("REMAINING_ISSUES:"):
                remaining = s.split(":", 1)[1].strip()
        return verified, remaining
