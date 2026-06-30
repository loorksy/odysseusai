"""
C115 — Checkpoint Manager
Save, list, restore, and delete agent/session checkpoints.
Checkpoints capture agent state, memory, tool outputs, and config
so any run can be resumed or rolled back to a known-good point.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(os.path.expanduser("~/.shadowrealm/checkpoints"))


@dataclass
class Checkpoint:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    label: str = ""
    session_id: str = ""
    agent_id: str = ""
    step: int = 0
    timestamp: float = field(default_factory=time.time)
    state: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    auto: bool = False

    def summary(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        auto_str = " [auto]" if self.auto else ""
        return f"[{self.id}] {ts}{auto_str} step={self.step}  {self.label or '(no label)'}"


class CheckpointManager:
    """
    Persist and restore checkpoints to/from disk.

    Usage::

        mgr = CheckpointManager(session_id="run-001")
        cp = mgr.save(agent_id="planner", step=10, state={...}, label="pre-web-search")
        mgr.save(agent_id="planner", step=20, state={...}, auto=True)

        all_cps = mgr.list()
        cp = mgr.load(cp.id)
        state = mgr.restore(cp.id)
        mgr.delete(cp.id)
        mgr.prune(keep=5)
    """

    def __init__(self, session_id: str, base_dir: Path = CHECKPOINT_DIR):
        self.session_id = session_id
        self.base_dir = base_dir / session_id
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        agent_id: str,
        step: int,
        state: dict,
        config: Optional[dict] = None,
        label: str = "",
        tags: Optional[list[str]] = None,
        auto: bool = False,
    ) -> Checkpoint:
        cp = Checkpoint(
            session_id=self.session_id,
            agent_id=agent_id,
            step=step,
            state=state,
            config=config or {},
            label=label,
            tags=tags or [],
            auto=auto,
        )
        path = self.base_dir / f"{cp.id}.json"
        path.write_text(json.dumps(asdict(cp), indent=2))
        logger.info("Checkpoint saved: %s", cp.summary())
        return cp

    def load(self, checkpoint_id: str) -> Checkpoint:
        path = self.base_dir / f"{checkpoint_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")
        data = json.loads(path.read_text())
        return Checkpoint(**data)

    def restore(self, checkpoint_id: str) -> dict:
        cp = self.load(checkpoint_id)
        logger.info("Restoring checkpoint: %s", cp.summary())
        return cp.state

    def latest(self, agent_id: Optional[str] = None) -> Optional[Checkpoint]:
        cps = self.list(agent_id=agent_id)
        return cps[-1] if cps else None

    def list(
        self,
        agent_id: Optional[str] = None,
        auto_only: bool = False,
    ) -> list[Checkpoint]:
        cps = []
        for f in sorted(self.base_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(f.read_text())
                cp = Checkpoint(**data)
                if agent_id and cp.agent_id != agent_id:
                    continue
                if auto_only and not cp.auto:
                    continue
                cps.append(cp)
            except Exception as e:
                logger.warning("Skipping corrupt checkpoint %s: %s", f.name, e)
        return cps

    def delete(self, checkpoint_id: str) -> bool:
        path = self.base_dir / f"{checkpoint_id}.json"
        if path.exists():
            path.unlink()
            logger.info("Deleted checkpoint: %s", checkpoint_id)
            return True
        return False

    def prune(self, keep: int = 10, auto_only: bool = True) -> int:
        cps = self.list(auto_only=auto_only)
        to_delete = cps[:-keep] if len(cps) > keep else []
        for cp in to_delete:
            self.delete(cp.id)
        return len(to_delete)

    def clear_all(self) -> int:
        cps = self.list()
        for cp in cps:
            self.delete(cp.id)
        return len(cps)
