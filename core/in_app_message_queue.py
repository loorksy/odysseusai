"""InAppMessageQueue — Per-user in-app message inbox (C80).

SQLite-backed inbox with read/unread/archived state, topic threading,
priority pinning, TTL expiry, and badge count.

Features:
  - Per-user message inbox stored in SQLite
  - Topic grouping for threaded views
  - States: unread → read → archived
  - Priority: 0=CRITICAL pins to top; 2=NORMAL default
  - TTL: messages auto-expire after N seconds (sweep_expired)
  - Badge count: unread per user
  - Thread-safe: single SQLite connection with threading.Lock

Public API:
  mq = InAppMessageQueue(db_path=":memory:")
  msg_id = mq.push(user_id, title, body, topic, priority, ttl_s, metadata)
  msgs   = mq.inbox(user_id, unread_only, topic, limit, offset)
  mq.mark_read(msg_id)          -> bool
  mq.mark_all_read(user_id)     -> int
  mq.archive(msg_id)            -> bool
  mq.delete(msg_id)             -> bool
  count  = mq.unread_count(user_id)
  swept  = mq.sweep_expired()   -> int
"""
from __future__ import annotations
import json, logging, sqlite3, threading, time, uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InAppMessage:
    msg_id:     str
    user_id:    str
    title:      str
    body:       str
    topic:      str
    priority:   int
    state:      str
    metadata:   Dict[str, Any]
    created_at: float
    expires_at: Optional[float]
    read_at:    Optional[float] = None


class InAppMessageQueue:
    """SQLite-backed per-user in-app inbox with TTL, topics, and badge count."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS messages (
            msg_id     TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            title      TEXT NOT NULL,
            body       TEXT NOT NULL,
            topic      TEXT NOT NULL DEFAULT 'general',
            priority   INTEGER NOT NULL DEFAULT 2,
            state      TEXT NOT NULL DEFAULT 'unread',
            metadata   TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            expires_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_user_state
            ON messages(user_id, state);
        CREATE INDEX IF NOT EXISTS idx_msg_topic
            ON messages(user_id, topic);
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(self._DDL)
            self._db.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def push(
        self,
        user_id:  str,
        title:    str,
        body:     str,
        *,
        topic:    str            = "general",
        priority: int            = 2,
        ttl_s:    Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        msg_id     = uuid.uuid4().hex
        now        = time.time()
        expires_at = (now + ttl_s) if ttl_s else None
        with self._lock:
            self._db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)",
                (msg_id, user_id, title, body, topic, priority,
                 "unread", json.dumps(metadata or {}), now, expires_at)
            )
            self._db.commit()
        return msg_id

    def mark_read(self, msg_id: str) -> bool:
        with self._lock:
            c = self._db.execute(
                "UPDATE messages SET state='read', read_at=? "
                "WHERE msg_id=? AND state='unread'",
                (time.time(), msg_id)
            )
            self._db.commit()
        return c.rowcount > 0

    def mark_all_read(self, user_id: str) -> int:
        with self._lock:
            c = self._db.execute(
                "UPDATE messages SET state='read', read_at=? "
                "WHERE user_id=? AND state='unread'",
                (time.time(), user_id)
            )
            self._db.commit()
        return c.rowcount

    def archive(self, msg_id: str) -> bool:
        with self._lock:
            c = self._db.execute(
                "UPDATE messages SET state='archived' WHERE msg_id=?",
                (msg_id,)
            )
            self._db.commit()
        return c.rowcount > 0

    def delete(self, msg_id: str) -> bool:
        with self._lock:
            c = self._db.execute(
                "DELETE FROM messages WHERE msg_id=?", (msg_id,)
            )
            self._db.commit()
        return c.rowcount > 0

    def sweep_expired(self) -> int:
        """Delete all messages past their TTL. Call periodically from JobScheduler."""
        with self._lock:
            c = self._db.execute(
                "DELETE FROM messages "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),)
            )
            self._db.commit()
        if c.rowcount:
            logger.debug(f"InAppMessageQueue: swept {c.rowcount} expired messages")
        return c.rowcount

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def inbox(
        self,
        user_id:     str,
        *,
        unread_only: bool          = False,
        topic:       Optional[str] = None,
        limit:       int           = 50,
        offset:      int           = 0,
    ) -> List[InAppMessage]:
        q      = "SELECT * FROM messages WHERE user_id=? AND state != 'archived'"
        params: list = [user_id]
        if unread_only:
            q += " AND state='unread'"
        if topic:
            q += " AND topic=?"
            params.append(topic)
        q += f" ORDER BY priority ASC, created_at DESC LIMIT {int(limit)} OFFSET {int(offset)}"
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [self._hydrate(r) for r in rows]

    def unread_count(self, user_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=? AND state='unread'",
                (user_id,)
            ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _hydrate(row: tuple) -> InAppMessage:
        return InAppMessage(
            msg_id=row[0], user_id=row[1], title=row[2], body=row[3],
            topic=row[4], priority=row[5], state=row[6],
            metadata=json.loads(row[7]), created_at=row[8], expires_at=row[9],
        )
