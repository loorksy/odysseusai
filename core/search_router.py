"""SearchRouter — Unified search dispatch across indices (C75).

Routes queries to the right index (full-text, vector, or both) and
merges/re-ranks results into a single response.

Search modes:
  text    -> FullTextIndex only
  vector  -> VectorSearchIndex only
  hybrid  -> both, results merged with Reciprocal Rank Fusion (RRF)
  auto    -> text if no query_vector provided, hybrid otherwise

Features:
  - RRF fusion: combines text BM25 rank and vector cosine rank
  - Namespace routing: different indices per namespace
  - Query pre-processing hook (pluggable)
  - Result post-processing / re-ranking hook
  - Telemetry: query latency, mode, result count

Public API:
  sr = SearchRouter(fts_index, vector_index, telemetry=None)
  results = sr.search(
      query, *, query_vector, namespace, mode, k, threshold
  ) -> list[SearchHit]
  sr.set_preprocessor(fn)
  sr.set_postprocessor(fn)
"""
from __future__ import annotations
import logging, time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_RRF_K = 60  # RRF constant


@dataclass
class SearchHit:
    doc_id:    str
    score:     float
    source:    str       # "text" | "vector" | "hybrid"
    fields:    Dict[str, Any] = field(default_factory=dict)
    snippet:   str = ""
    namespace: str = "default"


class SearchRouter:
    """Hybrid search router with RRF fusion."""

    def __init__(self, fts_index=None, vector_index=None, telemetry=None):
        self._fts    = fts_index
        self._vec    = vector_index
        self._tel    = telemetry
        self._pre:   Optional[Callable] = None
        self._post:  Optional[Callable] = None

    def set_preprocessor(self, fn: Callable) -> None:
        self._pre = fn

    def set_postprocessor(self, fn: Callable) -> None:
        self._post = fn

    def search(
        self,
        query:        str = "",
        *,
        query_vector: Optional[List[float]] = None,
        namespace:    str   = "default",
        mode:         str   = "auto",
        k:            int   = 20,
        threshold:    float = 0.0,
    ) -> List[SearchHit]:
        start = time.time()

        # Pre-processing
        if self._pre:
            try: query = self._pre(query) or query
            except Exception as e: logger.debug(f"SearchRouter preprocessor: {e}")

        # Resolve mode
        if mode == "auto":
            mode = "hybrid" if query_vector is not None else "text"

        hits: List[SearchHit] = []
        if mode == "text":
            hits = self._text_search(query, namespace, k)
        elif mode == "vector":
            hits = self._vector_search(query_vector, namespace, k, threshold)
        elif mode == "hybrid":
            hits = self._hybrid_search(query, query_vector, namespace, k, threshold)

        # Post-processing
        if self._post:
            try: hits = self._post(hits) or hits
            except Exception as e: logger.debug(f"SearchRouter postprocessor: {e}")

        elapsed = (time.time() - start) * 1000
        self._emit(mode, namespace, len(hits), elapsed)
        return hits

    # ------------------------------------------------------------------
    # Search modes
    # ------------------------------------------------------------------

    def _text_search(self, query: str, namespace: str, k: int) -> List[SearchHit]:
        if not self._fts or not query:
            return []
        results = self._fts.search(query, namespace=namespace, limit=k)
        return [SearchHit(doc_id=r.doc_id, score=r.score, source="text",
                          fields=r.fields, snippet=r.snippet, namespace=r.namespace)
                for r in results]

    def _vector_search(
        self, query_vector: Optional[List[float]], namespace: str,
        k: int, threshold: float,
    ) -> List[SearchHit]:
        if not self._vec or query_vector is None:
            return []
        results = self._vec.search(query_vector, k=k, namespace=namespace, threshold=threshold)
        return [SearchHit(doc_id=r.doc_id, score=r.score, source="vector",
                          fields=r.metadata, namespace=r.namespace)
                for r in results]

    def _hybrid_search(
        self, query: str, query_vector: Optional[List[float]],
        namespace: str, k: int, threshold: float,
    ) -> List[SearchHit]:
        text_hits   = self._text_search(query, namespace, k) if query else []
        vector_hits = self._vector_search(query_vector, namespace, k, threshold)
        return self._rrf_fuse(text_hits, vector_hits, k)

    # ------------------------------------------------------------------
    # RRF
    # ------------------------------------------------------------------

    @staticmethod
    def _rrf_fuse(
        list_a: List[SearchHit],
        list_b: List[SearchHit],
        k:      int,
    ) -> List[SearchHit]:
        scores: Dict[str, float] = {}
        by_id:  Dict[str, SearchHit] = {}
        for rank, hit in enumerate(list_a, 1):
            scores[hit.doc_id] = scores.get(hit.doc_id, 0) + 1 / (_RRF_K + rank)
            by_id[hit.doc_id]  = hit
        for rank, hit in enumerate(list_b, 1):
            scores[hit.doc_id] = scores.get(hit.doc_id, 0) + 1 / (_RRF_K + rank)
            if hit.doc_id not in by_id:
                by_id[hit.doc_id] = hit
        merged = sorted(scores.items(), key=lambda x: -x[1])
        results = []
        for doc_id, rrf_score in merged[:k]:
            hit = by_id[doc_id]
            results.append(SearchHit(
                doc_id=doc_id, score=rrf_score, source="hybrid",
                fields=hit.fields, snippet=hit.snippet, namespace=hit.namespace,
            ))
        return results

    def _emit(self, mode: str, namespace: str, count: int, ms: float) -> None:
        if self._tel:
            try:
                self._tel.emit("search", {"mode": mode, "namespace": namespace,
                                           "count": count, "latency_ms": ms})
            except Exception: pass
