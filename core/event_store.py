"""EventStore — Append-only domain event log (C70).

Persists immutable domain events in an append-only SQLite store.
Supports event sourcing patterns: replay, projection, and snapshots.

Features:
  - Append-only: events are never mutated or deleted
  - Optimistic concurrency: expected_version guard on append
  - Stream-per-aggregate: each entity has its own ordered event stream
  - Global ordering: monotonic global sequence number
  - Snapshots: store and retrieve aggregate state snapshots
  - Replay: iterate events from position N for rebuilding projections
  - In-memory mode (db_path=":memory:") for testing

Public API:
  es = EventStore(db_path=":memory:")
  es.append(stream_id, event_type, payload, *, expected_version)
  events = es.load(stream_id, *, from_version, to_version)
  events = es.load_all(*, from_seq, event_type)
  es.save_snapshot(stream_id, version, state)
  snap = es.load_snapshot(stream_id)  -> dict | None
  es.stream_version(stream_id)        -> int
"""
from __future__ import annotations
import json, logging, sqlite3, threading, time, uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class StoredEvent:
    seq:        int
    stream_id:  str
    version:    int
    event_type: str
    payload:    Dict[str, Any]
    event_id:   str
    occurred_at: float


class ConcurrencyError(Exception):
    pass


class EventStore:
    """Append-only SQLite event store with optimistic concurrency."""

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    def append(
        self,
        stream_id:        str,
        event_type:       str,
        payload:          Dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> StoredEvent:
        with self._lock:
            current = self._stream_version(stream_id)
            if expected_version is not None and current != expected_version:
                raise ConcurrencyError(
                    f"Stream '{stream_id}': expected version {expected_version}, "
                    f"got {current}"
                )
            version = current + 1
            event_id = uuid.uuid4().hex
            occurred_at = time.time()
            cur = self._db.execute(
                "INSERT INTO events(stream_id, version, event_type, payload, event_id, occurred_at) "
                "VALUES(?,?,?,?,?,?)",
                (stream_id, version, event_type,
                 json.dumps(payload, default=str), event_id, occurred_at)
            )
            self._db.commit()
            seq = cur.lastrowid
        return StoredEvent(
            seq=seq, stream_id=stream_id, version=version,
            event_type=event_type, payload=payload,
            event_id=event_id, occurred_at=occurred_at,
        )

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(
        self,
        stream_id:    str,
        *,
        from_version: int = 1,
        to_version:   Optional[int] = None,
    ) -> List[StoredEvent]:
        q = "SELECT seq, stream_id, version, event_type, payload, event_id, occurred_at "\
            "FROM events WHERE stream_id=? AND version>=?"
        params: list = [stream_id, from_version]
        if to_version is not None:
            q += " AND version<=?"
            params.append(to_version)
        q += " ORDER BY version"
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def load_all(
        self,
        *,
        from_seq:   int = 0,
        event_type: Optional[str] = None,
        limit:      int = 1000,
    ) -> List[StoredEvent]:
        q = "SELECT seq, stream_id, version, event_type, payload, event_id, occurred_at "\
            "FROM events WHERE seq>?"
        params: list = [from_seq]
        if event_type:
            q += " AND event_type=?"
            params.append(event_type)
        q += f" ORDER BY seq LIMIT {int(limit)}"
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def stream_version(self, stream_id: str) -> int:
        with self._lock:
            return self._stream_version(stream_id)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, stream_id: str, version: int, state: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO snapshots(stream_id, version, state, saved_at) "
                "VALUES(?,?,?,?)",
                (stream_id, version, json.dumps(state, default=str), time.time())
            )
            self._db.commit()

    def load_snapshot(self, stream_id: str) -> Optional[Dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT version, state, saved_at FROM snapshots WHERE stream_id=?",
                (stream_id,)
            ).fetchone()
        if not row: return None
        return {"version": row[0], "state": json.loads(row[1]), "saved_at": row[2]}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _stream_version(self, stream_id: str) -> int:
        row = self._db.execute(
            "SELECT MAX(version) FROM events WHERE stream_id=?", (stream_id,)
        ).fetchone()
        return row[0] or 0

    def _init_db(self) -> None:
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS events(
                seq         INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id   TEXT NOT NULL,
                version     INTEGER NOT NULL,
                event_type  TEXT NOT NULL,
                payload     TEXT NOT NULL,
                event_id    TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                UNIQUE(stream_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, version);
            CREATE INDEX IF NOT EXISTS idx_events_type   ON events(event_type);
            CREATE TABLE IF NOT EXISTS snapshots(
                stream_id TEXT PRIMARY KEY,
                version   INTEGER NOT NULL,
                state     TEXT NOT NULL,
                saved_at  REAL NOT NULL
            );
        """)
        self._db.commit()

    @staticmethod
    def _row_to_event(row) -> StoredEvent:
        return StoredEvent(
            seq=row[0], stream_id=row[1], version=row[2],
            event_type=row[3], payload=json.loads(row[4]),
            event_id=row[5], occurred_at=row[6],
        )
