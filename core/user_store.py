"""UserStore — User account persistence and lookup (C76).

Manages user accounts with hashed passwords, roles, and profile data.
Backend: SQLite (default) or in-memory dict for testing.

Features:
  - Password hashing: PBKDF2-HMAC-SHA256 (no external deps)
  - Role assignment (list of strings)
  - Account states: active / suspended / deleted (soft delete)
  - Lookup by id, username, or email
  - Paginated listing
  - Last-login tracking
  - Profile metadata: arbitrary JSON blob per user

Public API:
  us = UserStore(db_path=":memory:")
  user = us.create(username, email, password, *, roles, profile)
  user = us.get(user_id)
  user = us.get_by_username(username)
  user = us.get_by_email(email)
  ok   = us.verify_password(user_id, password) -> bool
  us.update(user_id, *, email, roles, profile, state)
  us.record_login(user_id)
  users = us.list(*, state, limit, offset)  -> list[User]
  us.delete(user_id)   # soft delete
"""
from __future__ import annotations
import hashlib, json, logging, os, sqlite3, threading, time, uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PBKDF2_ITER = 260_000
_SALT_BYTES  = 16


@dataclass
class User:
    user_id:    str
    username:   str
    email:      str
    roles:      List[str]
    state:      str          # active | suspended | deleted
    profile:    Dict[str, Any]
    created_at: float
    last_login: Optional[float] = None


class UserStore:
    """SQLite-backed user account store with PBKDF2 password hashing."""

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    def create(
        self,
        username: str,
        email:    str,
        password: str,
        *,
        roles:   Optional[List[str]] = None,
        profile: Optional[Dict]      = None,
    ) -> User:
        user_id    = uuid.uuid4().hex
        pw_hash    = self._hash_password(password)
        created_at = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)",
                (user_id, username, email, pw_hash,
                 json.dumps(roles or []), "active",
                 json.dumps(profile or {}), created_at, None)
            )
            self._db.commit()
        return User(user_id=user_id, username=username, email=email,
                    roles=roles or [], state="active",
                    profile=profile or {}, created_at=created_at)

    def get(self, user_id: str) -> Optional[User]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_username(self, username: str) -> Optional[User]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        return self._row(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM users WHERE email=?", (email,)
            ).fetchone()
        return self._row(row) if row else None

    def verify_password(self, user_id: str, password: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT pw_hash FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
        if not row: return False
        return self._check_password(password, row[0])

    def update(
        self,
        user_id: str,
        *,
        email:   Optional[str]       = None,
        roles:   Optional[List[str]] = None,
        profile: Optional[Dict]      = None,
        state:   Optional[str]       = None,
        password: Optional[str]      = None,
    ) -> bool:
        sets, params = [], []
        if email    is not None: sets.append("email=?");   params.append(email)
        if roles    is not None: sets.append("roles=?");   params.append(json.dumps(roles))
        if profile  is not None: sets.append("profile=?"); params.append(json.dumps(profile))
        if state    is not None: sets.append("state=?");   params.append(state)
        if password is not None: sets.append("pw_hash=?"); params.append(self._hash_password(password))
        if not sets: return False
        params.append(user_id)
        with self._lock:
            c = self._db.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE user_id=?", params
            )
            self._db.commit()
        return c.rowcount > 0

    def record_login(self, user_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE users SET last_login=? WHERE user_id=?", (time.time(), user_id)
            )
            self._db.commit()

    def list(
        self,
        *,
        state:  Optional[str] = "active",
        limit:  int = 50,
        offset: int = 0,
    ) -> List[User]:
        q = "SELECT * FROM users"
        params: list = []
        if state:
            q += " WHERE state=?"; params.append(state)
        q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        with self._lock:
            rows = self._db.execute(q, params).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, user_id: str) -> bool:
        return self.update(user_id, state="deleted")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id TEXT PRIMARY KEY, username TEXT UNIQUE,
                email TEXT UNIQUE, pw_hash TEXT,
                roles TEXT, state TEXT, profile TEXT,
                created_at REAL, last_login REAL
            )
        """)
        self._db.commit()

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = os.urandom(_SALT_BYTES)
        dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
        return salt.hex() + ":" + dk.hex()

    @staticmethod
    def _check_password(password: str, stored: str) -> bool:
        try:
            salt_hex, dk_hex = stored.split(":")
            salt = bytes.fromhex(salt_hex)
            dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
            import hmac as _hmac
            return _hmac.compare_digest(dk.hex(), dk_hex)
        except Exception:
            return False

    @staticmethod
    def _row(row) -> User:
        return User(
            user_id=row[0], username=row[1], email=row[2],
            roles=json.loads(row[4]), state=row[5],
            profile=json.loads(row[6]), created_at=row[7],
            last_login=row[8],
        )
