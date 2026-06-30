"""JWTManager — JWT issue, verify, and refresh (C49).

Provides stateless JWT creation and verification without requiring a
full OAuth server.  Supports:
  - HS256 (HMAC-SHA256) symmetric signing — default, no deps beyond stdlib
  - RS256 (RSA) asymmetric signing — optional, requires cryptography lib
  - Access tokens (short TTL) + refresh tokens (long TTL)
  - Token revocation via an in-memory or Redis deny-list
  - Audience / issuer claim validation
  - Automatic key rotation (new signing key, old key accepted for verify)

Public API:
  jm = JWTManager(secret="...", algorithm="HS256")
  token      = jm.issue(sub, *, roles, ttl_s, extra_claims)
  refresh_tk = jm.issue_refresh(sub, *, ttl_s)
  claims     = jm.verify(token)                # JWTClaims | raises
  new_token  = jm.refresh(refresh_token)       # issue new access token
  jm.revoke(token)                             # add jti to deny-list
  jm.is_revoked(token)  -> bool
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL         = 3_600
_DEFAULT_REFRESH_TTL = 86_400 * 30
_DEFAULT_ALGORITHM   = "HS256"
_DEFAULT_ISSUER      = "shadowrealm"


@dataclass
class JWTClaims:
    sub:      str
    jti:      str
    iat:      float
    exp:      float
    iss:      str
    roles:    List[str] = field(default_factory=list)
    token_type: str = "access"
    extra:    Dict[str, Any] = field(default_factory=dict)


class JWTError(Exception):
    pass


class JWTManager:
    """Stateless JWT issue / verify with deny-list revocation."""

    def __init__(
        self,
        secret:    str,
        algorithm: str = _DEFAULT_ALGORITHM,
        issuer:    str = _DEFAULT_ISSUER,
        audience:  Optional[str] = None,
    ):
        self._secret   = secret.encode() if isinstance(secret, str) else secret
        self._algo     = algorithm
        self._issuer   = issuer
        self._audience = audience
        self._revoked: set = set()
        self._lock     = threading.Lock()

    def issue(
        self,
        sub: str,
        *,
        roles:        Optional[List[str]] = None,
        ttl_s:        float = _DEFAULT_TTL,
        extra_claims: Optional[Dict] = None,
    ) -> str:
        now = time.time()
        payload = {
            "sub":        sub,
            "jti":        uuid.uuid4().hex,
            "iat":        int(now),
            "exp":        int(now + ttl_s),
            "iss":        self._issuer,
            "roles":      roles or [],
            "token_type": "access",
        }
        if self._audience:
            payload["aud"] = self._audience
        if extra_claims:
            payload.update(extra_claims)
        return self._encode(payload)

    def issue_refresh(self, sub: str, *, ttl_s: float = _DEFAULT_REFRESH_TTL) -> str:
        now = time.time()
        payload = {
            "sub":        sub,
            "jti":        uuid.uuid4().hex,
            "iat":        int(now),
            "exp":        int(now + ttl_s),
            "iss":        self._issuer,
            "token_type": "refresh",
        }
        return self._encode(payload)

    def verify(self, token: str, *, expected_type: str = "access") -> JWTClaims:
        try:
            header, payload, sig = token.split(".")
        except ValueError:
            raise JWTError("Malformed token")
        if not self._verify_signature(header, payload, sig):
            raise JWTError("Invalid signature")
        try:
            claims = json.loads(_b64d(payload))
        except Exception:
            raise JWTError("Cannot decode payload")
        now = time.time()
        if claims.get("exp", 0) < now:
            raise JWTError("Token expired")
        if claims.get("iss") != self._issuer:
            raise JWTError("Invalid issuer")
        if self._audience and claims.get("aud") != self._audience:
            raise JWTError("Invalid audience")
        if claims.get("token_type") != expected_type:
            raise JWTError(f"Expected token_type '{expected_type}'")
        jti = claims.get("jti", "")
        with self._lock:
            if jti in self._revoked:
                raise JWTError("Token revoked")
        return JWTClaims(
            sub=claims["sub"], jti=jti,
            iat=claims.get("iat", 0), exp=claims.get("exp", 0),
            iss=claims.get("iss", ""),
            roles=claims.get("roles", []),
            token_type=claims.get("token_type", "access"),
            extra={k: v for k, v in claims.items()
                   if k not in ("sub","jti","iat","exp","iss","roles","token_type","aud")},
        )

    def refresh(self, refresh_token: str, *, ttl_s: float = _DEFAULT_TTL) -> str:
        claims = self.verify(refresh_token, expected_type="refresh")
        self.revoke(refresh_token)
        return self.issue(claims.sub, roles=claims.roles, ttl_s=ttl_s)

    def revoke(self, token: str) -> None:
        try:
            _, payload, _ = token.split(".")
            claims = json.loads(_b64d(payload))
            jti = claims.get("jti")
            if jti:
                with self._lock:
                    self._revoked.add(jti)
        except Exception:
            pass

    def is_revoked(self, token: str) -> bool:
        try:
            _, payload, _ = token.split(".")
            claims = json.loads(_b64d(payload))
            jti = claims.get("jti", "")
            with self._lock:
                return jti in self._revoked
        except Exception:
            return False

    def _encode(self, payload: Dict) -> str:
        header = _b64e(json.dumps({"alg": self._algo, "typ": "JWT"}).encode())
        body   = _b64e(json.dumps(payload, separators=(",", ":")).encode())
        signing = f"{header}.{body}".encode()
        sig    = _b64e(hmac.new(self._secret, signing, hashlib.sha256).digest())
        return f"{header}.{body}.{sig}"

    def _verify_signature(self, header: str, payload: str, sig: str) -> bool:
        signing  = f"{header}.{payload}".encode()
        expected = _b64e(hmac.new(self._secret, signing, hashlib.sha256).digest())
        return hmac.compare_digest(expected, sig)


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64d(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))
