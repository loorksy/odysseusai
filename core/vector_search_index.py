"""VectorSearchIndex — Cosine-similarity vector search (C74).

Stores float32 embedding vectors and retrieves the top-K nearest
neighbours by cosine similarity.  No external deps required;
uses numpy if available for fast dot-product, falls back to pure Python.

Features:
  - Add / update / delete vectors by doc_id
  - Top-K cosine similarity search
  - Namespace isolation
  - Metadata stored alongside each vector
  - Persistence: save/load to .npz (numpy) or JSON fallback
  - Batch add for efficient bulk ingestion

Public API:
  vi = VectorSearchIndex(dim=1536)
  vi.add(doc_id, vector, *, metadata, namespace)
  vi.add_batch(items)   # [{doc_id, vector, metadata, namespace}]
  vi.delete(doc_id, *, namespace)
  results = vi.search(query_vector, k=10, *, namespace, threshold)  -> list[VectorResult]
  vi.save(path)
  vi.load(path)
  vi.count(namespace) -> int
"""
from __future__ import annotations
import json, logging, math, threading, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    logger.debug("VectorSearchIndex: numpy not available, using pure-Python fallback")


@dataclass
class VectorResult:
    doc_id:    str
    score:     float
    metadata:  Dict[str, Any]
    namespace: str


@dataclass
class _VectorEntry:
    doc_id:    str
    vector:    List[float]
    metadata:  Dict[str, Any]
    namespace: str
    added_at:  float = field(default_factory=time.time)


class VectorSearchIndex:
    """In-memory cosine similarity index with optional numpy acceleration."""

    def __init__(self, dim: int = 1536):
        self._dim    = dim
        # namespace -> list[_VectorEntry]
        self._stores: Dict[str, List[_VectorEntry]] = {}
        self._lock   = threading.Lock()

    def add(
        self,
        doc_id:    str,
        vector:    List[float],
        *,
        metadata:  Optional[Dict] = None,
        namespace: str = "default",
    ) -> None:
        self._validate(vector)
        with self._lock:
            store = self._stores.setdefault(namespace, [])
            store[:] = [e for e in store if e.doc_id != doc_id]
            store.append(_VectorEntry(
                doc_id=doc_id, vector=list(vector),
                metadata=metadata or {}, namespace=namespace,
            ))

    def add_batch(self, items: List[Dict]) -> int:
        count = 0
        for item in items:
            self.add(
                item["doc_id"], item["vector"],
                metadata=item.get("metadata", {}),
                namespace=item.get("namespace", "default"),
            )
            count += 1
        return count

    def delete(self, doc_id: str, *, namespace: str = "default") -> bool:
        with self._lock:
            store = self._stores.get(namespace, [])
            before = len(store)
            self._stores[namespace] = [e for e in store if e.doc_id != doc_id]
        return len(self._stores.get(namespace, [])) < before

    def search(
        self,
        query_vector: List[float],
        k:            int   = 10,
        *,
        namespace:    str   = "default",
        threshold:    float = 0.0,
    ) -> List[VectorResult]:
        self._validate(query_vector)
        with self._lock:
            store = list(self._stores.get(namespace, []))
        if not store:
            return []
        if _HAS_NUMPY:
            scores = self._cosine_numpy(query_vector, store)
        else:
            scores = self._cosine_python(query_vector, store)
        ranked = sorted(zip(scores, store), key=lambda x: -x[0])
        results = []
        for score, entry in ranked[:k]:
            if score < threshold: break
            results.append(VectorResult(
                doc_id=entry.doc_id, score=float(score),
                metadata=entry.metadata, namespace=entry.namespace,
            ))
        return results

    def count(self, namespace: str = "default") -> int:
        with self._lock:
            return len(self._stores.get(namespace, []))

    def save(self, path: str) -> None:
        data = {}
        with self._lock:
            for ns, entries in self._stores.items():
                data[ns] = [{"doc_id": e.doc_id, "vector": e.vector,
                              "metadata": e.metadata, "added_at": e.added_at}
                             for e in entries]
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    def load(self, path: str) -> int:
        data = json.loads(Path(path).read_text())
        count = 0
        for ns, entries in data.items():
            for e in entries:
                self.add(e["doc_id"], e["vector"],
                         metadata=e.get("metadata", {}), namespace=ns)
                count += 1
        return count

    def _validate(self, vector: List[float]) -> None:
        if len(vector) != self._dim:
            raise ValueError(f"VectorSearchIndex: expected dim={self._dim}, got {len(vector)}")

    @staticmethod
    def _cosine_numpy(query: List[float], store: List[_VectorEntry]) -> List[float]:
        q = np.array(query, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0: return [0.0] * len(store)
        q = q / q_norm
        mat = np.array([e.vector for e in store], dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1
        mat = mat / norms
        return (mat @ q).tolist()

    @staticmethod
    def _cosine_python(query: List[float], store: List[_VectorEntry]) -> List[float]:
        def dot(a, b): return sum(x * y for x, y in zip(a, b))
        def norm(a): return math.sqrt(sum(x * x for x in a))
        q_norm = norm(query)
        if q_norm == 0: return [0.0] * len(store)
        scores = []
        for entry in store:
            n = norm(entry.vector)
            scores.append(dot(query, entry.vector) / (q_norm * n) if n else 0.0)
        return scores
