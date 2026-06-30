"""
C93 · WorkflowStore
===================
Persistence layer for WorkflowDefinition graphs (C91) and RunRecord
execution history (C92).

Design principles
-----------------
* Backend-agnostic interface — swap JSON files for SQLite/Redis/etc.
  by subclassing BaseWorkflowStore and implementing the six abstract methods.
* Thread-safe JSON backend (FileLock via threading.Lock per store instance).
* Atomic writes: write to *.tmp then os.replace() to avoid corruption.
* Zero external deps — stdlib only (json, pathlib, threading, uuid).

Storage layout (JSON backend)
------------------------------
    <root>/
        workflows/
            <workflow_id>.json     ← WorkflowDefinition.to_dict()
        runs/
            <workflow_id>/
                <run_id>.json      ← RunRecord.to_dict()

Public API
----------
    store = WorkflowStore("/var/shadowrealm/store")

    # Definitions
    store.save_workflow(wf)
    wf   = store.load_workflow(workflow_id)
    wfs  = store.list_workflows(status="active", tag="daily")
    store.delete_workflow(workflow_id)

    # Run records
    store.save_run(record)
    rec  = store.load_run(run_id)
    recs = store.list_runs(workflow_id, status="completed", limit=20)
    store.delete_run(run_id)

Usage
-----
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType
    from core.workflow_engine    import WorkflowEngine
    from core.workflow_store     import WorkflowStore

    store  = WorkflowStore("./data/store")
    engine = WorkflowEngine()
    engine.register_stub(ActionType.TOOL_CALL, {"ok": True})

    wf = (
        WorkflowBuilder("demo")
        .trigger(TriggerType.MANUAL)
        .action("step1", ActionType.TOOL_CALL, tool="noop")
        .build()
    )
    store.save_workflow(wf)

    record = engine.run(wf, {})
    store.save_run(record)

    for r in store.list_runs(wf.workflow_id, limit=10):
        print(r["run_id"], r["status"])
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.workflow_definition import WorkflowDefinition, WorkflowStatus
from core.workflow_engine import RunRecord, RunStatus


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkflowNotFound(KeyError):
    pass


class RunNotFound(KeyError):
    pass


class StoreError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Abstract base — swap backends by subclassing
# ---------------------------------------------------------------------------

class BaseWorkflowStore(ABC):

    @abstractmethod
    def save_workflow(self, wf: WorkflowDefinition) -> None:
        """Persist (create or overwrite) a WorkflowDefinition."""

    @abstractmethod
    def load_workflow(self, workflow_id: str) -> WorkflowDefinition:
        """Return the WorkflowDefinition for *workflow_id*; raise WorkflowNotFound."""

    @abstractmethod
    def list_workflows(
        self,
        *,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkflowDefinition]:
        """Return a page of workflows, optionally filtered by status/tag."""

    @abstractmethod
    def delete_workflow(self, workflow_id: str) -> None:
        """Remove a workflow and all its run records."""

    @abstractmethod
    def save_run(self, record: RunRecord) -> None:
        """Persist (create or overwrite) a RunRecord."""

    @abstractmethod
    def load_run(self, run_id: str) -> RunRecord:
        """Return the RunRecord for *run_id*; raise RunNotFound."""

    @abstractmethod
    def list_runs(
        self,
        workflow_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Return a page of run summary dicts (to_dict() minus the steps list)
        for *workflow_id*, newest first.
        """

    @abstractmethod
    def delete_run(self, run_id: str) -> None:
        """Remove a single run record."""


# ---------------------------------------------------------------------------
# JSON file backend
# ---------------------------------------------------------------------------

class WorkflowStore(BaseWorkflowStore):
    """
    File-backed JSON store.

    Parameters
    ----------
    root : str | Path
        Root directory for all store files.  Created if absent.
    indent : int
        JSON indentation for human-readable files (default 2).
    """

    def __init__(self, root: str | Path, indent: int = 2) -> None:
        self._root   = Path(root).expanduser().resolve()
        self._indent = indent
        self._lock   = threading.Lock()
        self._wf_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    @property
    def _wf_dir(self) -> Path:
        return self._root / "workflows"

    @property
    def _run_dir(self) -> Path:
        return self._root / "runs"

    def _wf_path(self, workflow_id: str) -> Path:
        return self._wf_dir / f"{workflow_id}.json"

    def _run_wf_dir(self, workflow_id: str) -> Path:
        return self._run_dir / workflow_id

    def _run_path(self, workflow_id: str, run_id: str) -> Path:
        return self._run_wf_dir(workflow_id) / f"{run_id}.json"

    # ------------------------------------------------------------------
    # Atomic I/O helpers
    # ------------------------------------------------------------------

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=self._indent), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            raise StoreError(f"Failed to write {path}: {exc}") from exc

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            raise StoreError(f"Failed to read {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # WorkflowDefinition CRUD
    # ------------------------------------------------------------------

    def save_workflow(self, wf: WorkflowDefinition) -> None:
        with self._lock:
            self._write_json(self._wf_path(wf.workflow_id), wf.to_dict())

    def load_workflow(self, workflow_id: str) -> WorkflowDefinition:
        with self._lock:
            data = self._read_json(self._wf_path(workflow_id))
        if data is None:
            raise WorkflowNotFound(workflow_id)
        return WorkflowDefinition.from_dict(data)

    def list_workflows(
        self,
        *,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkflowDefinition]:
        results: List[WorkflowDefinition] = []
        with self._lock:
            paths = sorted(self._wf_dir.glob("*.json"))
        for p in paths:
            data = self._read_json(p)
            if data is None:
                continue
            if status and data.get("status") != status:
                continue
            if tag and tag not in data.get("tags", []):
                continue
            results.append(WorkflowDefinition.from_dict(data))
        return results[offset : offset + limit]

    def delete_workflow(self, workflow_id: str) -> None:
        with self._lock:
            wf_path = self._wf_path(workflow_id)
            if not wf_path.exists():
                raise WorkflowNotFound(workflow_id)
            wf_path.unlink(missing_ok=True)
            # Remove all associated run records
            run_dir = self._run_wf_dir(workflow_id)
            if run_dir.exists():
                for rp in run_dir.glob("*.json"):
                    rp.unlink(missing_ok=True)
                try:
                    run_dir.rmdir()
                except OSError:
                    pass  # non-empty dir edge-case; leave it

    # ------------------------------------------------------------------
    # RunRecord CRUD
    # ------------------------------------------------------------------

    def save_run(self, record: RunRecord) -> None:
        if not record.workflow_id:
            raise StoreError("RunRecord.workflow_id must be set before saving")
        with self._lock:
            self._write_json(
                self._run_path(record.workflow_id, record.run_id),
                _run_to_full_dict(record),
            )

    def load_run(self, run_id: str) -> RunRecord:
        # Search across all workflow subdirs — run_id is globally unique
        with self._lock:
            for p in self._run_dir.glob(f"*/{run_id}.json"):
                data = self._read_json(p)
                if data is not None:
                    return _run_from_dict(data)
        raise RunNotFound(run_id)

    def list_runs(
        self,
        workflow_id: str,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        run_dir = self._run_wf_dir(workflow_id)
        if not run_dir.exists():
            return []
        summaries: List[Dict[str, Any]] = []
        with self._lock:
            paths = sorted(run_dir.glob("*.json"), reverse=True)  # newest filename first
        for p in paths:
            data = self._read_json(p)
            if data is None:
                continue
            if status and data.get("status") != status:
                continue
            summaries.append(_run_summary(data))
        return summaries[offset : offset + limit]

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            for p in self._run_dir.glob(f"*/{run_id}.json"):
                p.unlink(missing_ok=True)
                return
        raise RunNotFound(run_id)

    # ------------------------------------------------------------------
    # Convenience: stats
    # ------------------------------------------------------------------

    def workflow_stats(self, workflow_id: str) -> Dict[str, Any]:
        """Return a summary dict: total runs, counts by status, last run."""
        runs = self.list_runs(workflow_id, limit=10_000)
        counts: Dict[str, int] = {}
        for r in runs:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {
            "workflow_id": workflow_id,
            "total_runs":  len(runs),
            "by_status":   counts,
            "last_run":    runs[0] if runs else None,
        }

    def __repr__(self) -> str:
        return f"WorkflowStore(root={self._root!r})"


# ---------------------------------------------------------------------------
# RunRecord serialisation helpers
# (RunRecord.to_dict() is defined in C92 but we need full round-trip here)
# ---------------------------------------------------------------------------

def _run_to_full_dict(record: RunRecord) -> Dict[str, Any]:
    """Serialise a RunRecord to a JSON-safe dict including all step data."""
    d = record.to_dict()
    # Add raw timestamps for accurate round-trip
    d["_started_at"] = record.started_at
    d["_ended_at"]   = record.ended_at
    d["_context"]    = record.context
    return d


def _run_from_dict(d: Dict[str, Any]) -> RunRecord:
    """Reconstruct a RunRecord from a persisted dict."""
    from core.workflow_engine import StepResult, StepStatus  # local import avoids circular

    steps: List[StepResult] = []
    for s in d.get("steps", []):
        steps.append(StepResult(
            node_id=s["node_id"],
            node_type=s["node_type"],
            status=StepStatus(s["status"]),
            output=s.get("output", {}),
            error=s.get("error"),
            started_at=s.get("_started_at", 0.0),
            ended_at=s.get("_ended_at", 0.0),
        ))

    record = RunRecord(
        run_id=d["run_id"],
        workflow_id=d["workflow_id"],
        workflow_name=d.get("workflow_name", ""),
        status=RunStatus(d["status"]),
        steps=steps,
        context=d.get("_context", {}),
        started_at=d.get("_started_at", 0.0),
        ended_at=d.get("_ended_at"),
        error=d.get("error"),
    )
    return record


def _run_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    """Slim summary dict — drops steps payload for list views."""
    return {
        "run_id":        d.get("run_id", ""),
        "workflow_id":   d.get("workflow_id", ""),
        "workflow_name": d.get("workflow_name", ""),
        "status":        d.get("status", ""),
        "elapsed_s":     d.get("elapsed_s"),
        "error":         d.get("error"),
    }
