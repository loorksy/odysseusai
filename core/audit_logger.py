"""AuditLogger — Tamper-evident structured audit trail (C71).

Records who did what to which resource and when.
Entries are chained with a SHA-256 hash of the previous entry
(blockchain-lite) so any tampering is detectable.

Features:
  - Structured entries: actor, action, resource, outcome, metadata
  - Hash chaining: each entry includes hash of previous
  - SQLite persistence with optional in-memory mode
  - Query by actor, resource, action, time range
  - Integrity verification: walk chain and re-check hashes
  - Export to JSON for compliance reporting

Public API:
  al = AuditLogger(db_path=":memory:")
  al.log(actor, action, resource, *, outcome, metadata)
  entries = al.query(*, actor, resource, action, since, until, limit)
  ok, failures = al.verify_integrity()
  al.export(path)
"""
from __future__ import annotations
import hashlib, json, logging, sqlite3, threading, time, uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    entry_id:   str
    actor:      str
    action:     str
    resource:   str
    outcome:    str          # success | failure | error
    metadata:   Dict[str, Any]
    occurred_at: float
    prev_hash:  str
    entry_hash: str


class AuditLogger:
    """Tamper-evident audit log with SHA-256 hash chaining."""

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    def log(
        self,
        actor:    str,
        action:   str,
        resource: str,
        *,
        outcome:  str = "success",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        meta_str    = json.dumps(metadata or {}, sort_keys=True, default=str)
        occurred_at = time.time()
        entry_id    = uuid.uuid4().hex
        with self._lock:
            prev_hash = self._last_hash()
            raw = f"{entry_id}|{actor}|{action}|{resource}|{outcome}|{meta_str}|{occurred_at}|{prev_hash}"
            entry_hash = hashlib.sha256(raw.encode()).hexdigest()
            self._db.execute(
                "INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?)",
                (entry_id, actor, action, resource, outcome,
                 meta_str, occurred_at, prev_hash, entry_hash)
            )
            self._db.commit()
        return AuditEntry(
            entry_id=entry_id, actor=actor, action=action,
            resource=resource, outcome=outcome,
            metadata=metadata or {}, occurred_at=occurred_at,
            prev_hash=prev_hash, entry_hash=entry_hash,
        )

    def query(
        self,
        *,
        actor:    Optional[str]   = None,
        resource: Optional[str]   = None,
        action:   Optional[str]   = None,
        since:    Optional[float] = None,
        until:    Optional[float] = None,
        limit:    int = 100,
    ) -> List[AuditEntry]:
        q      = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if actor:    q += " AND actor=?";    params.append(actor)
        if resource: q += " AND resource=?"; params.append(resource)
        if action:   q += " AND action=?";   params.append(action)
        if since:    q += " AND occurred_at>=?"; params.append(since)
        if until:    q += " AND occurred_at<=?"; params.append(until)
        q += f" ORDER BY occurred_at DESC LIMIT {int(limit)}"
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM audit_log ORDER BY occurred_at"
            ).fetchall()
        failures = []
        prev_hash = ""
        for row in rows:
            entry_id, actor, action, resource, outcome, meta_str, occurred_at, stored_prev, stored_hash = row
            if stored_prev != prev_hash:
                failures.append(f"{entry_id}: prev_hash mismatch")
            raw = f"{entry_id}|{actor}|{action}|{resource}|{outcome}|{meta_str}|{occurred_at}|{stored_prev}"
            expected = hashlib.sha256(raw.encode()).hexdigest()
            if expected != stored_hash:
                failures.append(f"{entry_id}: entry_hash mismatch")
            prev_hash = stored_hash
        return (len(failures) == 0, failures)

    def export(self, path: str = "audit_export.json") -> str:
        entries = self.query(limit=100_000)
        data = [{"entry_id": e.entry_id, "actor": e.actor, "action": e.action,
                 "resource": e.resource, "outcome": e.outcome,
                 "metadata": e.metadata, "occurred_at": e.occurred_at,
                 "entry_hash": e.entry_hash} for e in entries]
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def _last_hash(self) -> str:
        row = self._db.execute(
            "SELECT entry_hash FROM audit_log ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else ""

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log(
                entry_id    TEXT PRIMARY KEY,
                actor       TEXT NOT NULL,
                action      TEXT NOT NULL,
                resource    TEXT NOT NULL,
                outcome     TEXT NOT NULL,
                metadata    TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                prev_hash   TEXT NOT NULL,
                entry_hash  TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource)")
        self._db.commit()

    @staticmethod
    def _row_to_entry(row) -> AuditEntry:
        return AuditEntry(
            entry_id=row[0], actor=row[1], action=row[2],
            resource=row[3], outcome=row[4],
            metadata=json.loads(row[5]), occurred_at=row[6],
            prev_hash=row[7], entry_hash=row[8],
        )
