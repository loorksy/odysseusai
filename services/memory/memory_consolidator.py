"""MemoryConsolidator — Merges and deduplicates LTM + Episodes (C30).

Runs as a background task (or on-demand) after each session ends:
  1. Extracts new facts from the session's message history (delegates to
     memory_extractor.py which already exists).
  2. Deduplicates new facts against LongTermMemoryStore (Jaccard ≥ 0.85).
  3. Records the finished session as an EpisodicMemoryIndex episode.
  4. Optionally asks the LLM to generate a 1-3 sentence session summary.
  5. Merges near-duplicate LTM entries (Jaccard ≥ 0.90) by keeping the
     higher-confidence entry and updating its text to the most recent.

Public API:
  mc = MemoryConsolidator(data_dir, owner, ltm_store, episodic_index,
                          extractor=None, llm_fn=None)
  mc.consolidate(session_id, messages, *, outcome, skills_used, token_count)
      → ConsolidationResult
  mc.merge_duplicates()  → int  (entries removed)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEDUP_THRESHOLD  = 0.85   # new fact vs existing — skip if too similar
_MERGE_THRESHOLD  = 0.90   # merge existing entries that are near-identical
_SUMMARY_PROMPT   = """\
Summarise this conversation in 1-3 sentences. Be concise. Focus on what was
accomplished and any important outcomes.
Conversation (last {n} turns):
{turns}
Respond with ONLY the summary text.
"""


@dataclass
class ConsolidationResult:
    session_id: str
    facts_extracted:   int = 0
    facts_added:       int = 0
    facts_skipped:     int = 0   # near-duplicates of existing LTM entries
    episode_id:        Optional[str] = None
    summary:           str = ""
    duplicates_merged: int = 0
    duration_ms:       float = 0.0
    error:             Optional[str] = None


def _tokenize(text: str) -> set:
    return {w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class MemoryConsolidator:
    """Post-session memory consolidation pipeline."""

    def __init__(
        self,
        data_dir: str,
        owner: str,
        ltm_store,                       # LongTermMemoryStore instance
        episodic_index,                  # EpisodicMemoryIndex instance
        *,
        extractor=None,                  # memory_extractor.MemoryExtractor instance
        llm_fn: Optional[Callable[[List[Dict]], str]] = None,
    ):
        self._data_dir = data_dir
        self.owner = owner
        self._ltm = ltm_store
        self._epi = episodic_index
        self._extractor = extractor
        self._llm = llm_fn

    # ------------------------------------------------------------------
    # Main consolidation pipeline
    # ------------------------------------------------------------------

    def consolidate(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        *,
        outcome: str = "unknown",
        skills_used: Optional[List[str]] = None,
        token_count: int = 0,
        started_at: Optional[float] = None,
    ) -> ConsolidationResult:
        """Run the full post-session consolidation pipeline."""
        t0 = time.time()
        result = ConsolidationResult(session_id=session_id)

        try:
            # Step 1: Extract new facts
            raw_facts = self._extract_facts(messages)
            result.facts_extracted = len(raw_facts)

            # Step 2: Deduplicate against existing LTM
            existing_texts = [e["text"] for e in self._ltm.all()]
            existing_tokens = [_tokenize(t) for t in existing_texts]
            for fact in raw_facts:
                fact_tok = _tokenize(fact.get("text", ""))
                if any(_jaccard(fact_tok, ex) >= _DEDUP_THRESHOLD for ex in existing_tokens):
                    result.facts_skipped += 1
                    continue
                try:
                    self._ltm.add(
                        text=fact["text"],
                        category=fact.get("category", "other"),
                        source="extracted",
                        session_id=session_id,
                        confidence=float(fact.get("confidence", 0.75)),
                    )
                    result.facts_added += 1
                    existing_tokens.append(fact_tok)  # prevent intra-batch dups
                except Exception as e:
                    logger.debug(f"MemoryConsolidator: failed to add fact: {e}")

            # Step 3: Generate session summary
            summary = self._summarise(messages)
            result.summary = summary

            # Step 4: Record episode
            key_facts = [f["text"] for f in raw_facts[:10]]
            ep = self._epi.record(
                session_id=session_id,
                summary=summary,
                key_facts=key_facts,
                skills_used=skills_used or [],
                outcome=outcome,
                started_at=started_at,
                token_count=token_count,
            )
            result.episode_id = ep["id"]

            # Step 5: Merge near-duplicate LTM entries created this session
            result.duplicates_merged = self.merge_duplicates()

        except Exception as e:
            result.error = str(e)
            logger.error(f"MemoryConsolidator.consolidate failed: {e}")

        result.duration_ms = (time.time() - t0) * 1000
        return result

    # ------------------------------------------------------------------
    # Duplicate merger
    # ------------------------------------------------------------------

    def merge_duplicates(self) -> int:
        """Merge near-duplicate LTM entries. Returns count removed."""
        entries = self._ltm.all()
        removed = 0
        merged_ids: set = set()

        for i, a in enumerate(entries):
            if a["id"] in merged_ids:
                continue
            a_tok = _tokenize(a["text"])
            for b in entries[i + 1:]:
                if b["id"] in merged_ids:
                    continue
                if _jaccard(a_tok, _tokenize(b["text"])) >= _MERGE_THRESHOLD:
                    # Keep higher confidence; update its text to the more recent
                    if a.get("confidence", 0) >= b.get("confidence", 0):
                        keeper, dropper = a, b
                    else:
                        keeper, dropper = b, a
                    # Use more recent text if dropper was updated later
                    if dropper.get("updated_at", 0) > keeper.get("updated_at", 0):
                        self._ltm.update(keeper["id"], text=dropper["text"])
                    self._ltm.delete(dropper["id"])
                    merged_ids.add(dropper["id"])
                    removed += 1

        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_facts(self, messages: List[Dict[str, Any]]) -> List[Dict]:
        """Extract facts via MemoryExtractor, or fall back to a minimal heuristic."""
        if self._extractor:
            try:
                return self._extractor.extract(messages) or []
            except Exception as e:
                logger.debug(f"MemoryConsolidator: extractor failed: {e}")

        # Minimal heuristic fallback: pull explicit user preference statements
        facts = []
        for m in messages:
            if m.get("role") != "user":
                continue
            content = str(m.get("content", "")).strip()
            for trigger in ("i prefer", "i like", "i always", "i never", "my ",
                            "please always", "please never", "remember that",
                            "you were wrong", "that was incorrect"):
                if trigger in content.lower():
                    facts.append({
                        "text":       content[:300],
                        "category":   "preference" if "prefer" in trigger or "like" in trigger else "correction",
                        "confidence": 0.7,
                    })
                    break
        return facts

    def _summarise(self, messages: List[Dict[str, Any]]) -> str:
        """Generate a 1-3 sentence session summary."""
        if not self._llm:
            # Fallback: first user message as the summary topic
            first_user = next(
                (str(m.get("content", ""))[:200]
                 for m in messages if m.get("role") == "user"),
                "(no content)",
            )
            return f"Session covered: {first_user}"

        recent = messages[-10:]
        turns = "\n".join(
            f"[{m.get('role','?')}]: {str(m.get('content',''))[:300]}"
            for m in recent
        )
        prompt = _SUMMARY_PROMPT.format(n=len(recent), turns=turns)
        try:
            return self._llm([{"role": "user", "content": prompt}]).strip()
        except Exception as e:
            logger.debug(f"MemoryConsolidator: summary LLM call failed: {e}")
            return "(summary unavailable)"
