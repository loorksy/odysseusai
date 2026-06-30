"""ChangeTracker — Field-level diff and change history (C72).

Tracks before/after state of any dict-like object and records a
structured diff.  Integrates with AuditLogger and EventStore to
provide a complete record of what changed, when, and who changed it.

Features:
  - Deep field-level diff: added, removed, modified fields
  - Type-aware comparison: lists, dicts, scalars
  - Change history per entity: last N diffs
  - Rollback: reconstruct previous state from diff chain
  - Ignore list: skip noisy fields (e.g. updated_at)
  - Integration hooks: emit to AuditLogger / EventStore on change

Public API:
  ct = ChangeTracker(audit_logger=None, event_store=None)
  diff = ct.diff(before, after, *, ignore)
  ct.record(entity_id, before, after, *, actor, ignore)
  history = ct.history(entity_id, n)  -> list[ChangeRecord]
  state   = ct.rollback(entity_id, steps)  -> dict | None
"""
from __future__ import annotations
import copy, logging, time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class FieldChange:
    field:    str
    op:       str       # added | removed | modified
    before:   Any
    after:    Any


@dataclass
class ChangeRecord:
    entity_id:   str
    actor:       str
    occurred_at: float
    changes:     List[FieldChange]
    before_snap: Dict
    after_snap:  Dict


class ChangeTracker:
    """Field-level diff and rollback-capable change history."""

    def __init__(
        self,
        audit_logger = None,
        event_store  = None,
        max_history:   int = 100,
    ):
        self._audit   = audit_logger
        self._es      = event_store
        self._max     = max_history
        self._history: Dict[str, List[ChangeRecord]] = {}

    # ------------------------------------------------------------------
    # Diff
    # ------------------------------------------------------------------

    def diff(
        self,
        before: Dict,
        after:  Dict,
        *,
        ignore: Optional[Set[str]] = None,
        prefix: str = "",
    ) -> List[FieldChange]:
        ignore  = ignore or set()
        changes = []
        all_keys = set(before) | set(after)
        for key in sorted(all_keys):
            if key in ignore:
                continue
            full_key = f"{prefix}.{key}" if prefix else key
            b_val = before.get(key)
            a_val = after.get(key)
            if key not in before:
                changes.append(FieldChange(field=full_key, op="added",   before=None,  after=a_val))
            elif key not in after:
                changes.append(FieldChange(field=full_key, op="removed", before=b_val, after=None))
            elif isinstance(b_val, dict) and isinstance(a_val, dict):
                changes.extend(self.diff(b_val, a_val, ignore=ignore, prefix=full_key))
            elif b_val != a_val:
                changes.append(FieldChange(field=full_key, op="modified", before=b_val, after=a_val))
        return changes

    # ------------------------------------------------------------------
    # Record
    # ------------------------------------------------------------------

    def record(
        self,
        entity_id: str,
        before:    Dict,
        after:     Dict,
        *,
        actor:     str = "system",
        ignore:    Optional[Set[str]] = None,
    ) -> Optional[ChangeRecord]:
        changes = self.diff(before, after, ignore=ignore)
        if not changes:
            return None
        record = ChangeRecord(
            entity_id=entity_id,
            actor=actor,
            occurred_at=time.time(),
            changes=changes,
            before_snap=copy.deepcopy(before),
            after_snap=copy.deepcopy(after),
        )
        hist = self._history.setdefault(entity_id, [])
        hist.append(record)
        if len(hist) > self._max:
            self._history[entity_id] = hist[-self._max:]

        if self._audit:
            try:
                summary = ", ".join(f"{c.field}:{c.op}" for c in changes[:5])
                self._audit.log(actor, "update", entity_id,
                                metadata={"changes": summary, "count": len(changes)})
            except Exception as e:
                logger.debug(f"ChangeTracker: audit emit failed: {e}")

        if self._es:
            try:
                self._es.append(entity_id, "entity.updated", {
                    "actor":   actor,
                    "changes": [{"field": c.field, "op": c.op,
                                 "before": c.before, "after": c.after}
                                for c in changes],
                })
            except Exception as e:
                logger.debug(f"ChangeTracker: event store emit failed: {e}")

        return record

    def history(self, entity_id: str, n: int = 20) -> List[ChangeRecord]:
        return list(self._history.get(entity_id, []))[-n:]

    def rollback(
        self,
        entity_id: str,
        steps:     int = 1,
    ) -> Optional[Dict]:
        hist = self._history.get(entity_id, [])
        if not hist or steps < 1 or steps > len(hist):
            return None
        return copy.deepcopy(hist[-steps].before_snap)
