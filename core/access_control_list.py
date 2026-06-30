"""AccessControlList — Per-resource ACL with inheritance (C84).

Manages explicit access control lists per resource. Each ACL entry
specifies which principals (users/roles) may perform which actions
on a specific resource. Supports inheritance: a resource can inherit
its parent's ACL entries.

Features:
  - Per-resource ACL: set_acl(resource, principal, actions)
  - Inheritance chain: set_parent(resource, parent_resource)
  - Explicit deny entries override inherited allows
  - check(principal, action, resource) walks the inheritance chain
  - Supports wildcard principals and actions
  - SQLite persistence or in-memory
  - Thread-safe

Public API:
  acl = AccessControlList(db_path=":memory:")
  acl.set_acl(resource, principal, actions, effect="allow")
  acl.remove_acl(resource, principal)
  acl.set_parent(resource, parent_resource)
  allowed, reason = acl.check(principal, action, resource)
  entries = acl.list_acl(resource, inherited=True)
"""
from __future__ import annotations
import fnmatch, json, logging, sqlite3, threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class AccessControlList:
    """Per-resource ACL with principal wildcards and parent inheritance."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS acl_entries (
            resource  TEXT NOT NULL,
            principal TEXT NOT NULL,
            actions   TEXT NOT NULL,   -- JSON list
            effect    TEXT NOT NULL DEFAULT 'allow',
            PRIMARY KEY (resource, principal)
        );
        CREATE TABLE IF NOT EXISTS acl_parents (
            resource TEXT PRIMARY KEY,
            parent   TEXT NOT NULL
        );
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db   = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(self._DDL)
            self._db.commit()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def set_acl(
        self,
        resource:  str,
        principal: str,
        actions:   List[str],
        effect:    str = "allow",
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO acl_entries VALUES(?,?,?,?)",
                (resource, principal, json.dumps(actions), effect)
            )
            self._db.commit()
        logger.debug(
            f"ACL: {effect} {principal} [{', '.join(actions)}] on '{resource}'"
        )

    def remove_acl(self, resource: str, principal: str) -> bool:
        with self._lock:
            c = self._db.execute(
                "DELETE FROM acl_entries WHERE resource=? AND principal=?",
                (resource, principal)
            )
            self._db.commit()
        return c.rowcount > 0

    def set_parent(self, resource: str, parent_resource: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO acl_parents VALUES(?,?)",
                (resource, parent_resource)
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def check(
        self, principal: str, action: str, resource: str
    ) -> Tuple[bool, str]:
        """
        Walk the resource inheritance chain.
        Explicit DENY at any level overrides inherited ALLOW.
        """
        chain = self._resource_chain(resource)
        allow_found = False
        allow_src   = ""

        for res in chain:
            with self._lock:
                rows = self._db.execute(
                    "SELECT principal, actions, effect FROM acl_entries "
                    "WHERE resource=?", (res,)
                ).fetchall()
            for row_principal, row_actions_json, effect in rows:
                if not fnmatch.fnmatch(principal, row_principal):
                    continue
                row_actions = json.loads(row_actions_json)
                if any(fnmatch.fnmatch(action, a) for a in row_actions):
                    if effect == "deny":
                        return False, (
                            f"explicit deny: {row_principal} on '{res}'"
                        )
                    allow_found = True
                    allow_src   = res

        if allow_found:
            return True, f"allowed via '{allow_src}'"
        return False, f"no ACL entry: {principal} → {action} on '{resource}'"

    def list_acl(
        self, resource: str, *, inherited: bool = True
    ) -> List[Tuple[str, str, List[str], str]]:
        """Return (resource, principal, actions, effect) rows."""
        resources = self._resource_chain(resource) if inherited else [resource]
        result = []
        for res in resources:
            with self._lock:
                rows = self._db.execute(
                    "SELECT resource, principal, actions, effect "
                    "FROM acl_entries WHERE resource=?", (res,)
                ).fetchall()
            result.extend(
                (r[0], r[1], json.loads(r[2]), r[3]) for r in rows
            )
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resource_chain(self, resource: str) -> List[str]:
        """Build inheritance chain from resource up to root."""
        chain, visited = [], set()
        current = resource
        while current and current not in visited:
            visited.add(current)
            chain.append(current)
            with self._lock:
                row = self._db.execute(
                    "SELECT parent FROM acl_parents WHERE resource=?",
                    (current,)
                ).fetchone()
            current = row[0] if row else None
        return chain
