"""APIKeyStore — Secure API key management (C50).

Issues, stores, and validates API keys for machine-to-machine access.
Keys are stored as SHA-256 hashes — the plain-text key is only returned
once at creation time.

Features:
  - Key generation: cryptographically random 32-byte hex
  - Hashed storage: SHA-256 of the plain-text key
  - Per-key metadata: owner, name, scopes, expiry, last_used, usage_count
  - Scope enforcement: key must declare the required scope to pass check()
  - Revocation: mark key as inactive without deleting audit record
  - Optional SQLite persistence

Public API:
  store = APIKeyStore(db_path=None)
  key, record = store.create(owner, *, name, scopes, ttl_s)
  record      = store.lookup(key)
  ok          = store.check(key, scope)
  store.revoke(key_id)
  store.list(owner) -> list[APIKeyRecord]
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_KEY_BYTES  = 32
_PREFIX_LEN = 8


@dataclass
class APIKeyRecord:
    key_id:      str
    owner:       str
    name:        str
    key_hash:    str
    scopes:      List[str] = field(default_factory=list)
    created_at:  float = field(default_factory=time.time)
    expires_at:  Optional[float] = None
    last_used:   Optional[float] = None
    usage_count: int = 0
    active:      bool = True


class APIKeyStore:
    """SHA-256 hashed API key store with scope enforcement."""

    def __init__(self, db_path: Optional[str] = None):
        self._records:     Dict[str, APIKeyRecord] = {}
        self._hash_index:  Dict[str, str] = {}
        self._lock         = threading.Lock()
        self._db_path      = db_path
        if db_path:
            self._init_db()
            self._load_db()

    def create(
        self,
        owner: str,
        *,
        name:   str = "",
        scopes: Optional[List[str]] = None,
        ttl_s:  Optional[float] = None,
    ) -> tuple:
        plain    = os.urandom(_KEY_BYTES).hex()
        prefix   = plain[:_PREFIX_LEN]
        key_hash = hashlib.sha256(plain.encode()).hexdigest()
        key_id   = f"{owner[:8]}_{prefix}_{int(time.time())}"
        record   = APIKeyRecord(
            key_id=key_id, owner=owner,
            name=name or f"{owner}-key",
            key_hash=key_hash,
            scopes=scopes or [],
            expires_at=(time.time() + ttl_s) if ttl_s else None,
        )
        with self._lock:
            self._records[key_id]      = record
            self._hash_index[key_hash] = key_id
        if self._db_path:
            self._save_record(record)
        logger.info(f"APIKeyStore: created key '{key_id}' for '{owner}'")
        return plain, record

    def lookup(self, plain_key: str) -> Optional[APIKeyRecord]:
        key_hash = hashlib.sha256(plain_key.encode()).hexdigest()
        with self._lock:
            key_id = self._hash_index.get(key_hash)
            return self._records.get(key_id) if key_id else None

    def check(self, plain_key: str, scope: str = "") -> bool:
        record = self.lookup(plain_key)
        if not record or not record.active:
            return False
        if record.expires_at and time.time() > record.expires_at:
            return False
        if scope and record.scopes and scope not in record.scopes:
            return False
        with self._lock:
            record.last_used    = time.time()
            record.usage_count += 1
        return True

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            record = self._records.get(key_id)
            if record:
                record.active = False
                return True
        return False

    def list(self, owner: str) -> List[APIKeyRecord]:
        with self._lock:
            return [r for r in self._records.values() if r.owner == owner]

    def get_by_id(self, key_id: str) -> Optional[APIKeyRecord]:
        return self._records.get(key_id)

    def _init_db(self) -> None:
        import sqlite3
        with sqlite3.connect(self._db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY, owner TEXT, name TEXT,
                    key_hash TEXT, scopes TEXT, created_at REAL,
                    expires_at REAL, last_used REAL, usage_count INTEGER,
                    active INTEGER
                )
            """)

    def _load_db(self) -> None:
        import sqlite3, json
        with sqlite3.connect(self._db_path) as con:
            for row in con.execute("SELECT * FROM api_keys"):
                r = APIKeyRecord(
                    key_id=row[0], owner=row[1], name=row[2],
                    key_hash=row[3], scopes=json.loads(row[4] or "[]"),
                    created_at=row[5], expires_at=row[6],
                    last_used=row[7], usage_count=row[8] or 0,
                    active=bool(row[9]),
                )
                self._records[r.key_id]      = r
                self._hash_index[r.key_hash] = r.key_id

    def _save_record(self, r: APIKeyRecord) -> None:
        import sqlite3, json
        with sqlite3.connect(self._db_path) as con:
            con.execute(
                "INSERT OR REPLACE INTO api_keys VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r.key_id, r.owner, r.name, r.key_hash,
                 json.dumps(r.scopes), r.created_at, r.expires_at,
                 r.last_used, r.usage_count, int(r.active)),
            )
