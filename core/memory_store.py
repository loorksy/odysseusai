"""
C124 — Memory Store
Persistent + in-memory store for agent memories: facts, summaries,
conversation turns, and retrieved context. Supports semantic similarity
search when a vector backend is available (falls back to keyword search).
Thread-safe. Persisted as newline-delimited JSON.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MEMORY_DIR = Path.home() / ".shadowrealm" / "memory"


@dataclass
class MemoryEntry:
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    agent_id: str = ""
    kind: str = "fact"  # fact | summary | conversation | context
    content: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    importance: float = 1.0
    tags: list[str] = field(default_factory=list)

    def snippet(self) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{ts}|{self.kind}|{self.agent_id}] {self.content[:80]}"


class MemoryStore:
    def __init__(
        self,
        session_id: str,
        base_dir: Path = MEMORY_DIR,
        embed_fn=None,
        max_entries: int = 10_000,
    ):
        self.session_id = session_id
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._path = base_dir / f"{session_id}.jsonl"
        self._embed_fn = embed_fn
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: list[MemoryEntry] = []
        self._embeddings: list[list[float]] = []
        self._load()

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        with self._lock:
            if len(self._entries) >= self.max_entries:
                self._entries.sort(key=lambda e: e.importance)
                self._entries.pop(0)
                if self._embeddings:
                    self._embeddings.pop(0)
            self._entries.append(entry)
            if self._embed_fn:
                try:
                    self._embeddings.append(self._embed_fn(entry.content))
                except Exception as e:
                    logger.warning("Embed failed: %s", e)
                    self._embeddings.append([])
            self._append_to_disk(entry)
        return entry

    def add_bulk(self, entries: list[MemoryEntry]) -> None:
        for e in entries:
            self.add(e)

    def search(
        self,
        query: str,
        top_k: int = 5,
        kind: Optional[str] = None,
        agent_id: Optional[str] = None,
        min_importance: float = 0.0,
        tags: Optional[list[str]] = None,
    ) -> list[MemoryEntry]:
        with self._lock:
            candidates = [
                e for e in self._entries
                if (kind is None or e.kind == kind)
                and (agent_id is None or e.agent_id == agent_id)
                and e.importance >= min_importance
                and (tags is None or any(t in e.tags for t in tags))
            ]
        if self._embed_fn and self._embeddings:
            return self._semantic_search(query, candidates, top_k)
        return self._keyword_search(query, candidates, top_k)

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        with self._lock:
            for e in self._entries:
                if e.entry_id == entry_id:
                    return e
        return None

    def all(self) -> list[MemoryEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self, agent_id: Optional[str] = None) -> int:
        with self._lock:
            before = len(self._entries)
            if agent_id:
                self._entries = [e for e in self._entries if e.agent_id != agent_id]
            else:
                self._entries = []
                self._embeddings = []
            return before - len(self._entries)

    def _keyword_search(self, query: str, candidates: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        q_words = set(query.lower().split())
        def score(e: MemoryEntry) -> float:
            return len(q_words & set(e.content.lower().split())) * e.importance
        return sorted(candidates, key=score, reverse=True)[:top_k]

    def _semantic_search(self, query: str, candidates: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        try:
            q_vec = self._embed_fn(query)
        except Exception as e:
            logger.warning("Query embed failed: %s — falling back to keyword", e)
            return self._keyword_search(query, candidates, top_k)
        with self._lock:
            entry_idx = {e.entry_id: i for i, e in enumerate(self._entries)}
        scores = []
        for e in candidates:
            idx = entry_idx.get(e.entry_id)
            if idx is None or idx >= len(self._embeddings):
                continue
            vec = self._embeddings[idx]
            if not vec:
                continue
            scores.append((self._cosine(q_vec, vec) * e.importance, e))
        scores.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scores[:top_k]]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb + 1e-9)

    def _append_to_disk(self, entry: MemoryEntry) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except Exception as e:
            logger.warning("Memory write failed: %s", e)

    def _load(self) -> None:
        if not self._path.exists():
            return
        loaded = 0
        try:
            with self._path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._entries.append(MemoryEntry(**json.loads(line)))
                        loaded += 1
                    except Exception as e:
                        logger.warning("Skipping corrupt memory line: %s", e)
        except Exception as e:
            logger.warning("Memory load failed: %s", e)
        logger.info("Memory loaded: %d entries (%s)", loaded, self._path.name)
