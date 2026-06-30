"""
C107 — Secret Manager
Secure in-process secret store with environment variable fallback,
optional encryption, and access auditing.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SecretEntry:
    name: str
    value: str
    masked: bool = True
    created_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed: Optional[float] = None
    tags: list[str] = field(default_factory=list)

    def touch(self) -> None:
        self.access_count += 1
        self.last_accessed = time.time()


class SecretNotFoundError(KeyError):
    pass


class SecretManager:
    """
    In-process secret manager with env-var fallback and optional XOR obfuscation.

    Usage::

        sm = SecretManager(env_prefix="SHADOW_")
        sm.set("openai_key", "sk-...")
        key = sm.get("openai_key")
    """

    def __init__(
        self,
        env_prefix: str = "",
        obfuscate: bool = True,
        audit: bool = True,
    ):
        self._secrets: dict[str, SecretEntry] = {}
        self._lock = threading.RLock()
        self.env_prefix = env_prefix.upper()
        self._obfuscate = obfuscate
        self._audit = audit
        self._audit_log: list[dict] = []
        self._key = hashlib.sha256(os.urandom(16)).digest()

    # ------------------------------------------------------------------ #
    #  Core API                                                            #
    # ------------------------------------------------------------------ #

    def set(
        self,
        name: str,
        value: str,
        masked: bool = True,
        tags: Optional[list[str]] = None,
    ) -> None:
        with self._lock:
            stored = self._encode(value) if self._obfuscate else value
            self._secrets[name.lower()] = SecretEntry(
                name=name.lower(),
                value=stored,
                masked=masked,
                tags=tags or [],
            )
            self._log("set", name)

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        key = name.lower()
        with self._lock:
            entry = self._secrets.get(key)
            if entry is not None:
                entry.touch()
                self._log("get", name)
                return self._decode(entry.value) if self._obfuscate else entry.value
            # Fallback to environment variable
            env_key = (self.env_prefix + name).upper()
            val = os.environ.get(env_key)
            if val is not None:
                self._log("get_env", env_key)
                return val
            return default

    def require(self, name: str) -> str:
        val = self.get(name)
        if val is None:
            raise SecretNotFoundError(
                f"Secret '{name}' not found (also checked env: "
                f"{(self.env_prefix + name).upper()})"
            )
        return val

    def delete(self, name: str) -> bool:
        with self._lock:
            key = name.lower()
            if key in self._secrets:
                del self._secrets[key]
                self._log("delete", name)
                return True
            return False

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def list_names(self, tag: Optional[str] = None) -> list[str]:
        with self._lock:
            if tag:
                return [e.name for e in self._secrets.values() if tag in e.tags]
            return list(self._secrets.keys())

    def load_from_env(self, names: list[str]) -> int:
        count = 0
        for name in names:
            env_key = (self.env_prefix + name).upper()
            val = os.environ.get(env_key)
            if val:
                self.set(name, val)
                count += 1
        return count

    # ------------------------------------------------------------------ #
    #  Audit log                                                           #
    # ------------------------------------------------------------------ #

    def audit_log(self, limit: int = 50) -> list[dict]:
        return self._audit_log[-limit:]

    def _log(self, action: str, name: str) -> None:
        if not self._audit:
            return
        self._audit_log.append({
            "action": action,
            "name": name,
            "timestamp": time.time(),
        })
        if len(self._audit_log) > 2000:
            self._audit_log = self._audit_log[-2000:]

    # ------------------------------------------------------------------ #
    #  Obfuscation (XOR + base64, not cryptographic security)             #
    # ------------------------------------------------------------------ #

    def _encode(self, value: str) -> str:
        data = value.encode()
        key = (self._key * (len(data) // len(self._key) + 1))[:len(data)]
        xored = bytes(a ^ b for a, b in zip(data, key))
        return base64.b64encode(xored).decode()

    def _decode(self, encoded: str) -> str:
        xored = base64.b64decode(encoded.encode())
        key = (self._key * (len(xored) // len(self._key) + 1))[:len(xored)]
        return bytes(a ^ b for a, b in zip(xored, key)).decode()

    def __repr__(self) -> str:
        return f"SecretManager(secrets={len(self._secrets)}, env_prefix={self.env_prefix!r})"
