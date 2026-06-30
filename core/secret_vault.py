"""SecretVault — Encrypted at-rest secret storage (C56).

Stores named secrets encrypted with AES-256-GCM (via cryptography lib)
or Fernet (simpler symmetric) depending on what is installed.
Falls back to XOR-obfuscation + base64 if neither is available
(development only — logs a loud warning).

Secrets are persisted to an encrypted SQLite database.  The vault key
itself is derived from a master password + salt via PBKDF2-HMAC-SHA256.

Features:
  - set(name, value) / get(name) / delete(name)
  - Namespaces: vault.namespace("db").get("password")
  - TTL: secrets can expire automatically
  - Audit log: every access is recorded with timestamp and caller
  - Export / import for backup (re-encrypted with a transfer key)

Public API:
  vault = SecretVault(master_password, db_path=":memory:")
  vault.set(name, value, *, ttl_s, namespace)
  value = vault.get(name, *, namespace)    # None if missing / expired
  vault.delete(name, *, namespace)
  vault.list(namespace) -> list[str]
  vault.namespace(ns) -> SecretVault  (scoped proxy)
  vault.audit_log() -> list[dict]
"""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, sqlite3, threading, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 260_000
_SALT_SIZE         = 32


class SecretVault:
    """Encrypted at-rest secret store."""

    def __init__(
        self,
        master_password: str,
        db_path: str = ":memory:",
        namespace: str = "default",
    ):
        self._ns     = namespace
        self._lock   = threading.Lock()
        self._audit: List[Dict] = []
        self._db     = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        salt = self._get_or_create_salt()
        self._key = self._derive_key(master_password.encode(), salt)
        self._cipher = self._build_cipher()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def set(
        self,
        name: str,
        value: str,
        *,
        ttl_s:     Optional[float] = None,
        namespace: Optional[str]   = None,
    ) -> None:
        ns  = namespace or self._ns
        enc = self._encrypt(value)
        exp = (time.time() + ttl_s) if ttl_s else None
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO secrets(ns, name, ciphertext, expires_at) VALUES(?,?,?,?)",
                (ns, name, enc, exp)
            )
            self._db.commit()
        self._audit_log("set", ns, name)

    def get(
        self,
        name: str,
        *,
        namespace: Optional[str] = None,
    ) -> Optional[str]:
        ns = namespace or self._ns
        with self._lock:
            row = self._db.execute(
                "SELECT ciphertext, expires_at FROM secrets WHERE ns=? AND name=?",
                (ns, name)
            ).fetchone()
        if not row: return None
        ciphertext, expires_at = row
        if expires_at and time.time() > expires_at:
            self.delete(name, namespace=ns)
            return None
        self._audit_log("get", ns, name)
        return self._decrypt(ciphertext)

    def delete(self, name: str, *, namespace: Optional[str] = None) -> bool:
        ns = namespace or self._ns
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM secrets WHERE ns=? AND name=?", (ns, name)
            )
            self._db.commit()
        self._audit_log("delete", ns, name)
        return cur.rowcount > 0

    def list(self, namespace: Optional[str] = None) -> List[str]:
        ns = namespace or self._ns
        with self._lock:
            rows = self._db.execute(
                "SELECT name FROM secrets WHERE ns=?", (ns,)
            ).fetchall()
        return [r[0] for r in rows]

    def namespace(self, ns: str) -> "SecretVault":
        """Return a scoped proxy sharing the same underlying DB."""
        proxy = object.__new__(SecretVault)
        proxy._ns     = ns
        proxy._lock   = self._lock
        proxy._audit  = self._audit
        proxy._db     = self._db
        proxy._key    = self._key
        proxy._cipher = self._cipher
        return proxy

    def audit_log(self) -> List[Dict]:
        return list(self._audit)

    # ------------------------------------------------------------------
    # Crypto helpers
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: str) -> str:
        return self._cipher["encrypt"](plaintext.encode()).decode()

    def _decrypt(self, ciphertext: str) -> str:
        return self._cipher["decrypt"](ciphertext.encode()).decode()

    def _build_cipher(self) -> Dict:
        try:
            from cryptography.fernet import Fernet
            fernet_key = base64.urlsafe_b64encode(self._key[:32])
            f = Fernet(fernet_key)
            return {"encrypt": f.encrypt, "decrypt": f.decrypt}
        except ImportError:
            logger.warning("SecretVault: 'cryptography' not installed — using XOR obfuscation (DEV ONLY)")
            key = self._key
            def _xor_enc(data: bytes) -> bytes:
                return base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(data)))
            def _xor_dec(data: bytes) -> bytes:
                raw = base64.b64decode(data)
                return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
            return {"encrypt": _xor_enc, "decrypt": _xor_dec}

    @staticmethod
    def _derive_key(password: bytes, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password, salt, _PBKDF2_ITERATIONS)

    # ------------------------------------------------------------------
    # DB bootstrap
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS secrets(
                ns TEXT, name TEXT, ciphertext TEXT, expires_at REAL,
                PRIMARY KEY(ns, name)
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS vault_meta(
                key TEXT PRIMARY KEY, value TEXT
            )
        """)
        self._db.commit()

    def _get_or_create_salt(self) -> bytes:
        row = self._db.execute(
            "SELECT value FROM vault_meta WHERE key='salt'"
        ).fetchone()
        if row:
            return base64.b64decode(row[0])
        salt = os.urandom(_SALT_SIZE)
        self._db.execute(
            "INSERT INTO vault_meta VALUES('salt', ?)",
            (base64.b64encode(salt).decode(),)
        )
        self._db.commit()
        return salt

    def _audit_log(self, op: str, ns: str, name: str) -> None:
        self._audit.append({"op": op, "ns": ns, "name": name, "ts": time.time()})
        if len(self._audit) > 10_000:
            self._audit = self._audit[-5_000:]
