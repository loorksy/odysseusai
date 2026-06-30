"""EpisodicMemoryIndex — Session-level episode store with Jaccard retrieval (C29).

An episode is a single finished conversation session, stored as:
  id          str
  session_id  str
  owner       str
  summary     str   — 1-3 sentence recap of what happened
  key_facts   list[str]
  skills_used list[str]
  outcome     str   — "success" | "partial" | "failure" | "unknown"
  started_at  float
  ended_at    float
  token_count int

Retrieval is Jaccard over (summary + key_facts).  The index file lives at
data/memory/episodes/<owner>.json and uses atomic_write_json.

Public API:
  idx = EpisodicMemoryIndex(data_dir, owner)
  idx.record(session_id, summary, key_facts, ...)  → episode dict
  idx.search(query, limit)                         → list[episode]
  idx.recent(n)                                    → list[episode]
  idx.get(episode_id)                              → episode | None
  idx.prompt_block(query, limit)                   → str
  idx.stats()                                      → dict
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_EPISODES  = 5_000
_DEFAULT_LIMIT = 5
_OUTCOME_VALID = {"success", "partial", "failure", "unknown"}


def _tokenize(text: str) -> set:
    return {w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class EpisodicMemoryIndex:
    """Stores and retrieves finished session episodes."""

    def __init__(self, data_dir: str, owner: str):
        self.owner = owner
        safe = "".join(c for c in owner if c.isalnum() or c in "-_")
        self._path = os.path.join(data_dir, "memory", "episodes", f"{safe}.json")
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._episodes: List[Dict] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> List[Dict]:
        if not os.path.exists(self._path):
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"EpisodicMemoryIndex: failed to load {self._path}: {e}")
            return []

    def _save(self) -> None:
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self._path, self._episodes, indent=2)
        except Exception as e:
            logger.warning(f"EpisodicMemoryIndex: failed to save: {e}")

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    def record(
        self,
        session_id: str,
        summary: str,
        *,
        key_facts: Optional[List[str]] = None,
        skills_used: Optional[List[str]] = None,
        outcome: str = "unknown",
        started_at: Optional[float] = None,
        ended_at: Optional[float] = None,
        token_count: int = 0,
    ) -> Dict:
        """Record a finished session as an episode."""
        if len(self._episodes) >= _MAX_EPISODES:
            self._evict()

        now = time.time()
        episode: Dict = {
            "id":          str(uuid.uuid4()),
            "session_id":  session_id,
            "owner":       self.owner,
            "summary":     summary.strip(),
            "key_facts":   list(key_facts or []),
            "skills_used": list(skills_used or []),
            "outcome":     outcome if outcome in _OUTCOME_VALID else "unknown",
            "started_at":  started_at or now,
            "ended_at":    ended_at or now,
            "token_count": int(token_count),
            "recorded_at": now,
        }
        self._episodes.append(episode)
        self._save()
        return episode

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, episode_id: str) -> Optional[Dict]:
        return next((e for e in self._episodes if e["id"] == episode_id), None)

    def recent(self, n: int = _DEFAULT_LIMIT) -> List[Dict]:
        """Return the n most recently recorded episodes."""
        return sorted(self._episodes, key=lambda e: e.get("recorded_at", 0), reverse=True)[:n]

    def search(
        self,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        *,
        outcome: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> List[Dict]:
        """Jaccard search over summary + key_facts."""
        q_tokens = _tokenize(query)
        candidates = [
            e for e in self._episodes
            if (not outcome or e.get("outcome") == outcome)
            and (not skill or skill in (e.get("skills_used") or []))
        ]
        scored = []
        for e in candidates:
            text = e.get("summary", "") + " " + " ".join(e.get("key_facts") or [])
            score = _jaccard(q_tokens, _tokenize(text))
            scored.append((score, e))
        scored.sort(key=lambda x: (-x[0], -x[1].get("recorded_at", 0)))
        return [e for _, e in scored[:limit]]

    def by_skill(self, skill_name: str, limit: int = 20) -> List[Dict]:
        """Return episodes where a specific skill was used."""
        hits = [e for e in self._episodes if skill_name in (e.get("skills_used") or [])]
        return sorted(hits, key=lambda e: e.get("recorded_at", 0), reverse=True)[:limit]

    # ------------------------------------------------------------------
    # System-prompt block
    # ------------------------------------------------------------------

    def prompt_block(
        self,
        query: Optional[str] = None,
        limit: int = 3,
        *,
        header: str = "## Relevant Past Sessions",
    ) -> str:
        """Format relevant episodes for system-prompt injection."""
        if query:
            episodes = self.search(query, limit=limit)
        else:
            episodes = self.recent(limit)
        if not episodes:
            return ""
        lines = [header]
        for e in episodes:
            outcome_tag = {"success": "✅", "partial": "⚠️", "failure": "❌"}.get(e.get("outcome", ""), "❓")
            lines.append(f"- {outcome_tag} {e['summary']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict:
        total = len(self._episodes)
        outcomes: Dict[str, int] = {}
        for e in self._episodes:
            o = e.get("outcome", "unknown")
            outcomes[o] = outcomes.get(o, 0) + 1
        return {
            "total_episodes":  total,
            "outcomes":        outcomes,
            "total_tokens":    sum(e.get("token_count", 0) for e in self._episodes),
            "skills_seen":     list({s for e in self._episodes for s in (e.get("skills_used") or [])}),
        }

    # ------------------------------------------------------------------
    # Eviction (oldest non-success first)
    # ------------------------------------------------------------------

    def _evict(self, target: int = 200) -> int:
        evictable = sorted(
            [e for e in self._episodes if e.get("outcome") != "success"],
            key=lambda e: e.get("recorded_at", 0),
        )
        evictable += sorted(
            [e for e in self._episodes if e.get("outcome") == "success"],
            key=lambda e: e.get("recorded_at", 0),
        )
        to_remove = {e["id"] for e in evictable[:target]}
        before = len(self._episodes)
        self._episodes = [e for e in self._episodes if e["id"] not in to_remove]
        return before - len(self._episodes)
