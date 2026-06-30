"""
C110 — Vector Store Interface
Abstract vector store with an in-memory reference implementation.
Supports upsert, similarity search, metadata filtering, and namespaces.
"""
from __future__ import annotations

import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class VectorRecord:
    id: str
    vector: list[float]
    metadata: dict = field(default_factory=dict)
    text: str = ""
    namespace: str = "default"
    created_at: float = field(default_factory=time.time)


@dataclass
class SearchResult:
    record: VectorRecord
    score: float  # cosine similarity, higher = more similar


class VectorStoreBase(ABC):
    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> int: ...
    @abstractmethod
    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        namespace: str = "default",
        filter: Optional[dict] = None,
    ) -> list[SearchResult]: ...
    @abstractmethod
    def delete(self, ids: list[str], namespace: str = "default") -> int: ...
    @abstractmethod
    def get(self, id: str, namespace: str = "default") -> Optional[VectorRecord]: ...
    @abstractmethod
    def count(self, namespace: str = "default") -> int: ...
    def clear(self, namespace: str = "default") -> None: ...


class InMemoryVectorStore(VectorStoreBase):
    """
    Pure-Python cosine-similarity vector store.
    Suitable for development, testing, and small corpora (<50k vectors).

    Usage::

        store = InMemoryVectorStore()
        store.upsert([VectorRecord(id="1", vector=[0.1, 0.9], text="hello")])
        results = store.search([0.1, 0.9], top_k=3)
    """

    def __init__(self):
        self._store: dict[str, dict[str, VectorRecord]] = {}  # namespace -> id -> record

    def _ns(self, namespace: str) -> dict[str, VectorRecord]:
        if namespace not in self._store:
            self._store[namespace] = {}
        return self._store[namespace]

    def upsert(self, records: list[VectorRecord]) -> int:
        count = 0
        for r in records:
            if not r.id:
                r.id = uuid.uuid4().hex[:12]
            self._ns(r.namespace)[r.id] = r
            count += 1
        return count

    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        namespace: str = "default",
        filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        ns = self._ns(namespace)
        candidates = list(ns.values())
        if filter:
            candidates = [
                r for r in candidates
                if all(r.metadata.get(k) == v for k, v in filter.items())
            ]
        scored = [
            SearchResult(record=r, score=self._cosine(query_vector, r.vector))
            for r in candidates
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]

    def delete(self, ids: list[str], namespace: str = "default") -> int:
        ns = self._ns(namespace)
        removed = 0
        for id_ in ids:
            if id_ in ns:
                del ns[id_]
                removed += 1
        return removed

    def get(self, id: str, namespace: str = "default") -> Optional[VectorRecord]:
        return self._ns(namespace).get(id)

    def count(self, namespace: str = "default") -> int:
        return len(self._ns(namespace))

    def clear(self, namespace: str = "default") -> None:
        self._store[namespace] = {}

    def namespaces(self) -> list[str]:
        return list(self._store.keys())

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)
