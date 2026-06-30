"""
C93 · WorkflowRegistry
=======================
Store, version, activate, and deactivate WorkflowDefinitions (C91).

Design principles
-----------------
* Single source of truth for all known workflows.
* Versioning — every save bumps a monotonic version counter; old versions
  are retained in history up to a configurable cap.
* Status lifecycle — DRAFT → ACTIVE → PAUSED → ARCHIVED.
* Persistence — optional SQLite backend (via C19 pattern); falls back to
  in-memory store for zero-config / test use.
* Thread-safe — asyncio.Lock guards all mutating operations.
* Event hooks — on_register / on_activate / on_deactivate / on_archive
  callbacks for downstream integration (EventBus C4, AuditLogger C71).
* stdlib only — no external deps.

Usage
-----
    from core.workflow_registry import WorkflowRegistry
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType

    registry = WorkflowRegistry()               # in-memory
    # registry = WorkflowRegistry(db_path="workflows.db")  # SQLite

    wf = WorkflowBuilder("daily-digest") \
           .trigger(TriggerType.SCHEDULE, cron="0 7 * * *") \
           .action("fetch", ActionType.TOOL_CALL, tool="rss_reader") \
           .build()

    await registry.register(wf)          # saves as DRAFT
    await registry.activate(wf.workflow_id)
    active = await registry.get_active()  # all ACTIVE workflows
    wf2 = await registry.get(wf.workflow_id)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.workflow_definition import WorkflowDefinition, WorkflowStatus


# ---------------------------------------------------------------------------
# Enumerations & constants
# ---------------------------------------------------------------------------

class RegistryEvent(str, Enum):
    REGISTERED  = "registered"
    UPDATED     = "updated"
    ACTIVATED   = "activated"
    PAUSED      = "paused"
    ARCHIVED    = "archived"
    DELETED     = "deleted"


_VALID_TRANSITIONS: Dict[WorkflowStatus, List[WorkflowStatus]] = {
    WorkflowStatus.DRAFT:    [WorkflowStatus.ACTIVE, WorkflowStatus.ARCHIVED],
    WorkflowStatus.ACTIVE:   [WorkflowStatus.PAUSED, WorkflowStatus.ARCHIVED],
    WorkflowStatus.PAUSED:   [WorkflowStatus.ACTIVE, WorkflowStatus.ARCHIVED],
    WorkflowStatus.ARCHIVED: [],   # terminal
}

_DEFAULT_HISTORY_CAP = 20  # max old versions kept per workflow


# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------

@dataclass
class RegistryEntry:
    workflow_id:  str
    current:      WorkflowDefinition
    history:      List[WorkflowDefinition] = field(default_factory=list)
    created_at:   float = field(default_factory=time.time)
    updated_at:   float = field(default_factory=time.time)
    tags:         List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "current":     self.current.to_dict(),
            "history":     [w.to_dict() for w in self.history],
            "created_at":  self.created_at,
            "updated_at":  self.updated_at,
            "tags":        list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RegistryEntry":
        return cls(
            workflow_id=d["workflow_id"],
            current=WorkflowDefinition.from_dict(d["current"]),
            history=[WorkflowDefinition.from_dict(h) for h in d.get("history", [])],
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            tags=d.get("tags", []),
        )


# ---------------------------------------------------------------------------
# Hook type alias
# ---------------------------------------------------------------------------

RegistryHook = Callable[[RegistryEvent, WorkflowDefinition], Awaitable[None]]


# ---------------------------------------------------------------------------
# WorkflowRegistry
# ---------------------------------------------------------------------------

class WorkflowRegistry:
    """
    Central store for WorkflowDefinitions with versioning and lifecycle.

    Parameters
    ----------
    db_path     : path to SQLite file; None = in-memory only
    history_cap : max old versions kept per workflow (default 20)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        history_cap: int = _DEFAULT_HISTORY_CAP,
    ) -> None:
        self._db_path     = db_path
        self._history_cap = history_cap
        self._lock        = asyncio.Lock()
        self._store: Dict[str, RegistryEntry] = {}
        self._hooks: List[RegistryHook] = []
        self._db: Optional[sqlite3.Connection] = None

        if db_path:
            self._init_db(db_path)
            self._load_from_db()

    # ------------------------------------------------------------------
    # Hook registration
    # ------------------------------------------------------------------

    def add_hook(self, hook: RegistryHook) -> None:
        """Register an async callback for registry events."""
        self._hooks.append(hook)

    async def _fire(self, event: RegistryEvent, wf: WorkflowDefinition) -> None:
        for hook in self._hooks:
            try:
                await hook(event, wf)
            except Exception:
                pass  # hooks must not crash registry operations

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def register(
        self,
        workflow: WorkflowDefinition,
        *,
        tags: Optional[List[str]] = None,
    ) -> WorkflowDefinition:
        """
        Save a new workflow (status forced to DRAFT on first registration).
        If workflow_id already exists, updates and bumps version.
        """
        async with self._lock:
            existing = self._store.get(workflow.workflow_id)
            if existing is None:
                # First registration — force DRAFT
                workflow.status  = WorkflowStatus.DRAFT
                workflow.version = 1
                entry = RegistryEntry(
                    workflow_id=workflow.workflow_id,
                    current=workflow,
                    tags=list(tags or workflow.tags),
                )
                self._store[workflow.workflow_id] = entry
                self._persist(entry)
                await self._fire(RegistryEvent.REGISTERED, workflow)
            else:
                # Update: push current to history, bump version
                old = existing.current
                existing.history.append(old)
                if len(existing.history) > self._history_cap:
                    existing.history = existing.history[-self._history_cap:]
                workflow.version   = old.version + 1
                existing.current   = workflow
                existing.updated_at = time.time()
                if tags:
                    existing.tags = list(tags)
                self._persist(existing)
                await self._fire(RegistryEvent.UPDATED, workflow)

        return workflow

    async def get(
        self,
        workflow_id: str,
        *,
        version: Optional[int] = None,
    ) -> WorkflowDefinition:
        """
        Retrieve a workflow by ID.
        Pass version= to retrieve a historical snapshot.
        """
        entry = self._store.get(workflow_id)
        if entry is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        if version is None or version == entry.current.version:
            return entry.current
        for wf in entry.history:
            if wf.version == version:
                return wf
        raise KeyError(f"Workflow '{workflow_id}' version {version} not found")

    async def delete(
        self,
        workflow_id: str,
        *,
        force: bool = False,
    ) -> None:
        """
        Remove a workflow. Raises if ACTIVE unless force=True.
        """
        async with self._lock:
            entry = self._store.get(workflow_id)
            if entry is None:
                return
            if entry.current.status == WorkflowStatus.ACTIVE and not force:
                raise ValueError(
                    f"Cannot delete ACTIVE workflow '{workflow_id}'. "
                    "Archive or deactivate first, or pass force=True."
                )
            wf = entry.current
            del self._store[workflow_id]
            self._delete_from_db(workflow_id)
            await self._fire(RegistryEvent.DELETED, wf)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    async def activate(self, workflow_id: str) -> WorkflowDefinition:
        """Transition workflow to ACTIVE."""
        return await self._transition(
            workflow_id, WorkflowStatus.ACTIVE, RegistryEvent.ACTIVATED
        )

    async def pause(self, workflow_id: str) -> WorkflowDefinition:
        """Transition workflow to PAUSED."""
        return await self._transition(
            workflow_id, WorkflowStatus.PAUSED, RegistryEvent.PAUSED
        )

    async def archive(self, workflow_id: str) -> WorkflowDefinition:
        """Transition workflow to ARCHIVED (terminal)."""
        return await self._transition(
            workflow_id, WorkflowStatus.ARCHIVED, RegistryEvent.ARCHIVED
        )

    async def _transition(
        self,
        workflow_id: str,
        target: WorkflowStatus,
        event: RegistryEvent,
    ) -> WorkflowDefinition:
        async with self._lock:
            entry = self._store.get(workflow_id)
            if entry is None:
                raise KeyError(f"Workflow '{workflow_id}' not found")
            wf = entry.current
            allowed = _VALID_TRANSITIONS.get(wf.status, [])
            if target not in allowed:
                raise ValueError(
                    f"Cannot transition '{workflow_id}' from "
                    f"{wf.status.value} → {target.value}. "
                    f"Allowed: {[s.value for s in allowed]}"
                )
            wf.status      = target
            entry.updated_at = time.time()
            self._persist(entry)
        await self._fire(event, wf)
        return wf

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_all(self) -> List[WorkflowDefinition]:
        """Return current version of all registered workflows."""
        return [e.current for e in self._store.values()]

    async def get_active(self) -> List[WorkflowDefinition]:
        """Return all ACTIVE workflows."""
        return [
            e.current for e in self._store.values()
            if e.current.status == WorkflowStatus.ACTIVE
        ]

    async def get_by_status(self, status: WorkflowStatus) -> List[WorkflowDefinition]:
        """Return all workflows with a given status."""
        return [
            e.current for e in self._store.values()
            if e.current.status == status
        ]

    async def search(
        self,
        *,
        name_contains: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowDefinition]:
        """Filter workflows by name substring, tag, and/or status."""
        results = []
        for entry in self._store.values():
            wf = entry.current
            if name_contains and name_contains.lower() not in wf.name.lower():
                continue
            if tag and tag not in entry.tags:
                continue
            if status and wf.status != status:
                continue
            results.append(wf)
        return results

    async def get_history(self, workflow_id: str) -> List[WorkflowDefinition]:
        """Return all historical versions of a workflow (oldest first)."""
        entry = self._store.get(workflow_id)
        if entry is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        return list(entry.history)

    async def entry(self, workflow_id: str) -> RegistryEntry:
        """Return the full RegistryEntry for a workflow."""
        entry = self._store.get(workflow_id)
        if entry is None:
            raise KeyError(f"Workflow '{workflow_id}' not found")
        return entry

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, workflow_id: str) -> bool:
        return workflow_id in self._store

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    def _init_db(self, path: str) -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS workflow_registry (
                workflow_id TEXT PRIMARY KEY,
                entry_json  TEXT NOT NULL,
                updated_at  REAL NOT NULL
            )
        """)
        self._db.commit()

    def _load_from_db(self) -> None:
        if not self._db:
            return
        cursor = self._db.execute(
            "SELECT entry_json FROM workflow_registry ORDER BY updated_at ASC"
        )
        for (raw,) in cursor.fetchall():
            try:
                entry = RegistryEntry.from_dict(json.loads(raw))
                self._store[entry.workflow_id] = entry
            except Exception:
                pass  # skip corrupted rows

    def _persist(self, entry: RegistryEntry) -> None:
        if not self._db:
            return
        raw = json.dumps(entry.to_dict())
        self._db.execute(
            """
            INSERT INTO workflow_registry (workflow_id, entry_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                entry_json = excluded.entry_json,
                updated_at = excluded.updated_at
            """,
            (entry.workflow_id, raw, entry.updated_at),
        )
        self._db.commit()

    def _delete_from_db(self, workflow_id: str) -> None:
        if not self._db:
            return
        self._db.execute(
            "DELETE FROM workflow_registry WHERE workflow_id = ?",
            (workflow_id,),
        )
        self._db.commit()

    def close(self) -> None:
        """Close the SQLite connection (call on shutdown)."""
        if self._db:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------

    def export_all(self) -> List[Dict[str, Any]]:
        """Serialise all registry entries to a list of dicts."""
        return [e.to_dict() for e in self._store.values()]

    def import_all(
        self,
        entries: List[Dict[str, Any]],
        *,
        overwrite: bool = False,
    ) -> int:
        """
        Bulk-import registry entries from a list of dicts.
        Returns count of entries imported.
        """
        imported = 0
        for raw in entries:
            try:
                entry = RegistryEntry.from_dict(raw)
                if entry.workflow_id in self._store and not overwrite:
                    continue
                self._store[entry.workflow_id] = entry
                self._persist(entry)
                imported += 1
            except Exception:
                pass
        return imported

    def __repr__(self) -> str:
        counts: Dict[str, int] = {}
        for e in self._store.values():
            s = e.current.status.value
            counts[s] = counts.get(s, 0) + 1
        return f"WorkflowRegistry(total={len(self)}, {counts})"
