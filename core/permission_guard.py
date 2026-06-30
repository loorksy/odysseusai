"""PermissionGuard — RBAC + scope-based access control (C51).

Enforces who can do what across the agent, routes, and tool calls.
Supports two complementary models:
  RBAC : role -> set of permissions  (e.g. "admin" -> {"skill:write"})
  Scopes: API key scopes treated as direct permission grants

Integrates with JWTManager (reads .roles from JWTClaims) and
APIKeyStore (reads .scopes from APIKeyRecord).

Public API:
  guard = PermissionGuard()
  guard.define_role(role, permissions, *, extend)
  guard.grant(owner, permission)
  guard.deny(owner, permission)
  ok = guard.check(owner, permission, *, roles, scopes, context)
  guard.require(permission)           # Flask decorator -> 403
  guard.require_any(*permissions)     # Flask decorator -> 403
  guard.from_jwt(claims)   -> PermissionContext
  guard.from_api_key(record) -> PermissionContext
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PermissionContext:
    owner:      str
    roles:      List[str] = field(default_factory=list)
    scopes:     List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)


class PermissionDenied(Exception):
    pass


class PermissionGuard:
    """RBAC + direct grant/deny access control."""

    def __init__(self):
        self._roles:  Dict[str, Set[str]] = {}
        self._grants: Dict[str, Set[str]] = {}
        self._denies: Dict[str, Set[str]] = {}

    def define_role(
        self,
        role: str,
        permissions: List[str],
        *,
        extend: Optional[str] = None,
    ) -> None:
        base = set(self._roles.get(extend, set())) if extend else set()
        self._roles[role] = base | set(permissions)

    def grant(self, owner: str, permission: str) -> None:
        self._grants.setdefault(owner, set()).add(permission)

    def deny(self, owner: str, permission: str) -> None:
        self._denies.setdefault(owner, set()).add(permission)

    def revoke_grant(self, owner: str, permission: str) -> None:
        self._grants.get(owner, set()).discard(permission)

    def check(
        self,
        owner: str,
        permission: str,
        *,
        roles:   Optional[List[str]] = None,
        scopes:  Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Evaluate access.  Explicit deny always wins."""
        denies = self._denies.get(owner, set())
        if permission in denies or "*" in denies:
            return False
        if permission in self._grants.get("*", set()):
            return True
        if permission in self._grants.get(owner, set()):
            return True
        if "*" in self._grants.get(owner, set()):
            return True
        for role in (roles or []):
            perms = self._roles.get(role, set())
            if permission in perms or "*" in perms:
                return True
        if scopes and (permission in scopes or "*" in scopes):
            return True
        return False

    def require_or_raise(self, owner: str, permission: str, **kwargs) -> None:
        if not self.check(owner, permission, **kwargs):
            raise PermissionDenied(f"Owner '{owner}' lacks permission '{permission}'")

    def require(self, permission: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                ctx = self._extract_flask_context()
                if not self.check(ctx.owner, permission,
                                  roles=ctx.roles, scopes=ctx.scopes):
                    return self._forbidden(permission)
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    def require_any(self, *permissions: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                ctx = self._extract_flask_context()
                if not any(self.check(ctx.owner, p,
                           roles=ctx.roles, scopes=ctx.scopes)
                           for p in permissions):
                    return self._forbidden(" | ".join(permissions))
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    @staticmethod
    def from_jwt(claims) -> PermissionContext:
        return PermissionContext(
            owner=claims.sub,
            roles=getattr(claims, "roles", []),
        )

    @staticmethod
    def from_api_key(record) -> PermissionContext:
        return PermissionContext(
            owner=record.owner,
            scopes=getattr(record, "scopes", []),
        )

    @staticmethod
    def _extract_flask_context() -> PermissionContext:
        try:
            from flask import g, request
            owner  = getattr(g, "throttle_owner", None) or \
                     request.headers.get("X-Owner-Id", "anonymous")
            roles  = getattr(g, "jwt_roles", [])
            scopes = getattr(g, "api_scopes", [])
            return PermissionContext(owner=owner, roles=roles, scopes=scopes)
        except Exception:
            return PermissionContext(owner="anonymous")

    @staticmethod
    def _forbidden(permission: str):
        try:
            from flask import jsonify
            resp = jsonify({"error": "forbidden", "required": permission})
            resp.status_code = 403
            return resp
        except Exception:
            raise PermissionDenied(f"Forbidden: requires '{permission}'")
