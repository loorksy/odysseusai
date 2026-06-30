"""PermissionManager — Fine-grained permission graph (C82).

Models permissions as a directed graph:
  subject (user/role/agent) → action (read/write/execute/admin)
                             → resource (path/type/id pattern)

Features:
  - Grant / revoke individual permissions
  - Wildcard matching on action and resource (glob-style)
  - Role inheritance: role A extends role B
  - Agent-scoped permissions separate from user permissions
  - check() returns bool with optional reason string
  - Batch check: check_all(subject, [(action, resource), ...])
  - SQLite persistence or in-memory
  - Thread-safe

Public API:
  pm = PermissionManager(db_path=":memory:")
  pm.grant(subject, action, resource)
  pm.revoke(subject, action, resource)
  pm.add_role_inheritance(child_role, parent_role)
  allowed, reason = pm.check(subject, action, resource)
  all_pass = pm.check_all(subject, [(action, resource), ...])
  perms = pm.list_permissions(subject)
"""
from __future__ import annotations
import fnmatch, logging, sqlite3, threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class PermissionManager:
    """Fine-grained subject→action→resource permission graph."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS permissions (
            subject  TEXT NOT NULL,
            action   TEXT NOT NULL,
            resource TEXT NOT NULL,
            PRIMARY KEY (subject, action, resource)
        );
        CREATE TABLE IF NOT EXISTS role_inheritance (
            child  TEXT NOT NULL,
            parent TEXT NOT NULL,
            PRIMARY KEY (child, parent)
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

    def grant(self, subject: str, action: str, resource: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO permissions VALUES(?,?,?)",
                (subject, action, resource)
            )
            self._db.commit()
        logger.debug(f"PermissionManager: grant {subject} → {action}:{resource}")

    def revoke(self, subject: str, action: str, resource: str) -> bool:
        with self._lock:
            c = self._db.execute(
                "DELETE FROM permissions WHERE subject=? AND action=? AND resource=?",
                (subject, action, resource)
            )
            self._db.commit()
        return c.rowcount > 0

    def add_role_inheritance(self, child_role: str, parent_role: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO role_inheritance VALUES(?,?)",
                (child_role, parent_role)
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def check(
        self, subject: str, action: str, resource: str
    ) -> Tuple[bool, str]:
        """Return (allowed, reason). Checks subject + all inherited roles."""
        subjects = self._expand_subjects(subject)
        with self._lock:
            rows = self._db.execute(
                "SELECT action, resource FROM permissions WHERE subject IN (%s)"
                % ",".join("?" * len(subjects)),
                subjects
            ).fetchall()
        for row_action, row_resource in rows:
            if (fnmatch.fnmatch(action, row_action) and
                    fnmatch.fnmatch(resource, row_resource)):
                return True, f"matched ({row_action}, {row_resource})"
        return False, f"no permission: {subject} → {action}:{resource}"

    def check_all(
        self, subject: str, checks: List[Tuple[str, str]]
    ) -> bool:
        """Return True only if ALL (action, resource) pairs are permitted."""
        return all(self.check(subject, a, r)[0] for a, r in checks)

    def list_permissions(self, subject: str) -> List[Tuple[str, str, str]]:
        """Return all (subject, action, resource) rows for a subject."""
        subjects = self._expand_subjects(subject)
        with self._lock:
            return self._db.execute(
                "SELECT subject, action, resource FROM permissions "
                "WHERE subject IN (%s)" % ",".join("?" * len(subjects)),
                subjects
            ).fetchall()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _expand_subjects(self, subject: str) -> List[str]:
        """Collect subject + all ancestor roles (BFS)."""
        visited, queue = set(), [subject]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            with self._lock:
                parents = self._db.execute(
                    "SELECT parent FROM role_inheritance WHERE child=?",
                    (current,)
                ).fetchall()
            queue.extend(p[0] for p in parents)
        return list(visited)
