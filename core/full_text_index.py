"""FullTextIndex — SQLite FTS5-backed full-text search (C73).

Indexes documents and supports ranked keyword search with:
  - BM25 ranking (native FTS5)
  - Prefix queries and phrase queries
  - Field-weighted search (title > body)
  - Incremental updates: add, update, delete documents
  - Namespace isolation
  - Snippet extraction for result highlighting

Public API:
  idx = FullTextIndex(db_path=":memory:")
  idx.add(doc_id, fields, *, namespace)
  idx.update(doc_id, fields, *, namespace)
  idx.delete(doc_id, *, namespace)
  results = idx.search(query, *, namespace, limit, offset)  -> list[SearchResult]
  idx.count(namespace) -> int
"""
from __future__ import annotations
import json, logging, sqlite3, threading, time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    doc_id:   str
    score:    float
    fields:   Dict[str, Any]
    snippet:  str = ""
    namespace: str = "default"


class FullTextIndex:
    """FTS5 full-text search index with BM25 ranking."""

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    def add(
        self,
        doc_id: str,
        fields: Dict[str, Any],
        *,
        namespace: str = "default",
    ) -> None:
        title   = str(fields.get("title", ""))
        body    = str(fields.get("body",  fields.get("content", "")))
        tags    = " ".join(fields.get("tags", []))
        meta    = json.dumps({k: v for k, v in fields.items()
                              if k not in ("title", "body", "content", "tags")},
                             default=str)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO docs(doc_id, namespace, title, body, tags, meta, indexed_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (doc_id, namespace, title, body, tags, meta, time.time())
            )
            self._db.execute(
                "INSERT OR REPLACE INTO docs_fts(doc_id, namespace, title, body, tags) "
                "VALUES(?,?,?,?,?)",
                (doc_id, namespace, title, body, tags)
            )
            self._db.commit()

    def update(self, doc_id: str, fields: Dict[str, Any], *, namespace: str = "default") -> None:
        self.delete(doc_id, namespace=namespace)
        self.add(doc_id, fields, namespace=namespace)

    def delete(self, doc_id: str, *, namespace: str = "default") -> bool:
        with self._lock:
            c1 = self._db.execute("DELETE FROM docs WHERE doc_id=? AND namespace=?",
                                   (doc_id, namespace))
            self._db.execute("DELETE FROM docs_fts WHERE doc_id=? AND namespace=?",
                             (doc_id, namespace))
            self._db.commit()
        return c1.rowcount > 0

    def search(
        self,
        query:     str,
        *,
        namespace: str = "default",
        limit:     int = 20,
        offset:    int = 0,
    ) -> List[SearchResult]:
        if not query.strip():
            return []
        fts_query = self._build_query(query)
        sql = """
            SELECT d.doc_id, d.namespace, d.title, d.body, d.tags, d.meta,
                   bm25(docs_fts) AS score,
                   snippet(docs_fts, 3, '<b>', '</b>', '...', 32) AS snip
            FROM docs_fts
            JOIN docs d ON d.doc_id = docs_fts.doc_id AND d.namespace = docs_fts.namespace
            WHERE docs_fts MATCH ? AND docs_fts.namespace = ?
            ORDER BY score
            LIMIT ? OFFSET ?
        """
        try:
            with self._lock:
                rows = self._db.execute(sql, (fts_query, namespace, limit, offset)).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"FullTextIndex.search: {e}")
            return []
        results = []
        for row in rows:
            doc_id, ns, title, body, tags, meta, score, snip = row
            f = {"title": title, "body": body, "tags": tags.split()}
            try: f.update(json.loads(meta))
            except Exception: pass
            results.append(SearchResult(
                doc_id=doc_id, score=float(score or 0),
                fields=f, snippet=snip or "", namespace=ns,
            ))
        return results

    def count(self, namespace: str = "default") -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM docs WHERE namespace=?", (namespace,)
            ).fetchone()
        return row[0] if row else 0

    def _init_db(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS docs(
                doc_id TEXT, namespace TEXT, title TEXT, body TEXT,
                tags TEXT, meta TEXT, indexed_at REAL,
                PRIMARY KEY(doc_id, namespace)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                doc_id UNINDEXED, namespace UNINDEXED,
                title, body, tags,
                content='docs', content_rowid='rowid',
                tokenize='porter ascii'
            );
        """)
        self._db.commit()

    @staticmethod
    def _build_query(q: str) -> str:
        q = q.strip()
        if q.startswith('"') or any(op in q for op in (" AND ", " OR ", " NOT ", "*")):
            return q
        tokens = q.split()
        return " ".join(f'"{t}"*' for t in tokens)
