"""LongTermMemoryStore — Persistent, owner-scoped fact store (C28).

Stores durable facts extracted from conversations:
  - User preferences, stated goals, project context
  - Corrective feedback ("you were wrong about X")
  - Established facts that should survive compaction

Storage: one JSON file per owner under data/memory/ltm/<owner>.json
All writes are atomic (atomic_write_json).

Schema per entry:
  id         str   — uuid4
  text       str   — the fact in plain English
  category   str   — "preference" | "correction" | "context" | "goal" | "other"
  source     str   — "user" | "extracted" | "agent"
  session_id str   — session that produced this fact
  created_at float — unix timestamp
  updated_at float
  confidence float — 0.0–1.0
  pinned     bool  — pinned facts are never evicted
  tags       list[str]

Public API:
  store = LongTermMemoryStore(data_dir, owner="alice")
  store.add(text, category, source, ...)  → entry dict
  store.search(query, limit)              → list[entry]
  store.get(id)                           → entry | None
  store.update(id, **fields)              → bool
  store.delete(id)                        → bool
  store.all()                             → list[entry]
  store.prompt_block(limit)               → str for system prompt injection
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"preference", "correction", "context", "goal", "other"}
_VALID_SOURCES    = {"user", "extracted", "agent"}
_MAX_ENTRIES      = 2_000   # hard cap per owner to prevent unbounded growth
_DEFAULT_LIMIT    = 10      # default search / prompt_block results


def _tokenize(text: str) -> set:
    return {w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class LongTermMemoryStore:
    """Durable fact store for a single owner."""

    def __init__(self, data_dir: str, owner: str):
        self.owner = owner
        safe = "".join(c for c in owner if c.isalnum() or c in "-_")
        self._path = os.path.join(data_dir, "memory", "ltm", f"{safe}.json")
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._entries: List[Dict] = self._load()

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
            logger.warning(f"LongTermMemoryStore: failed to load {self._path}: {e}")
            return []

    def _save(self) -> None:
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self._path, self._entries, indent=2)
        except Exception as e:
            logger.warning(f"LongTermMemoryStore: failed to save: {e}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        text: str,
        category: str = "other",
        source: str = "extracted",
        *,
        session_id: Optional[str] = None,
        confidence: float = 0.8,
        pinned: bool = False,
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """Add a new fact. Returns the stored entry dict."""
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        category = category if category in _VALID_CATEGORIES else "other"
        source   = source   if source   in _VALID_SOURCES    else "extracted"

        # Evict oldest non-pinned entries if at capacity
        if len(self._entries) >= _MAX_ENTRIES:
            self._evict()

        now = time.time()
        entry: Dict = {
            "id":         str(uuid.uuid4()),
            "text":       text.strip(),
            "category":   category,
            "source":     source,
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "confidence": float(max(0.0, min(confidence, 1.0))),
            "pinned":     bool(pinned),
            "tags":       list(tags or []),
        }
        self._entries.append(entry)
        self._save()
        return entry

    def get(self, entry_id: str) -> Optional[Dict]:
        return next((e for e in self._entries if e["id"] == entry_id), None)

    def update(self, entry_id: str, **fields) -> bool:
        """Update mutable fields on an existing entry."""
        _immutable = {"id", "created_at", "owner"}
        for e in self._entries:
            if e["id"] == entry_id:
                for k, v in fields.items():
                    if k not in _immutable:
                        e[k] = v
                e["updated_at"] = time.time()
                self._save()
                return True
        return False

    def delete(self, entry_id: str) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        if len(self._entries) < before:
            self._save()
            return True
        return False

    def all(self) -> List[Dict]:
        return list(self._entries)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = _DEFAULT_LIMIT,
        *,
        category: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> List[Dict]:
        """Jaccard-ranked search over all entries."""
        q_tokens = _tokenize(query)
        candidates = [
            e for e in self._entries
            if (not category or e.get("category") == category)
            and e.get("confidence", 1.0) >= min_confidence
        ]
        scored = []
        for e in candidates:
            score = _jaccard(q_tokens, _tokenize(e["text"]))
            # Boost pinned and high-confidence entries
            score *= (1.0 + 0.2 * float(e.get("pinned", False)))
            score *= (1.0 + 0.1 * float(e.get("confidence", 0.8)))
            scored.append((score, e))
        scored.sort(key=lambda x: (-x[0], -x[1].get("updated_at", 0)))
        return [e for _, e in scored[:limit]]

    # ------------------------------------------------------------------
    # System-prompt block
    # ------------------------------------------------------------------

    def prompt_block(
        self,
        limit: int = _DEFAULT_LIMIT,
        *,
        header: str = "## Long-Term Memory",
        categories: Optional[List[str]] = None,
    ) -> str:
        """Format the most recent / pinned facts for system-prompt injection."""
        entries = sorted(
            [
                e for e in self._entries
                if (not categories or e.get("category") in categories)
            ],
            key=lambda e: (
                -int(e.get("pinned", False)),
                -e.get("confidence", 0.8),
                -e.get("updated_at", 0),
            ),
        )[:limit]
        if not entries:
            return ""
        lines = [header]
        for e in entries:
            pin = "📌 " if e.get("pinned") else ""
            lines.append(f"- {pin}{e['text']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Eviction (FIFO, non-pinned only)
    # ------------------------------------------------------------------

    def _evict(self, target: int = 100) -> int:
        """Remove the oldest `target` non-pinned entries."""
        evictable = sorted(
            [e for e in self._entries if not e.get("pinned")],
            key=lambda e: e.get("created_at", 0),
        )
        to_remove = {e["id"] for e in evictable[:target]}
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["id"] not in to_remove]
        return before - len(self._entries)
