"""
C118 — Session Manager
Manages the lifecycle of a ShadowRealm session: creation, persistence,
resumption, and teardown. Ties together the monitor, checkpoint manager,
tier config, and active agents for a single run.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.agent_monitor import AgentMonitor
from core.checkpoint_manager import CheckpointManager
from core.tier_config import TierConfig

logger = logging.getLogger(__name__)

SESSION_DIR = Path.home() / ".shadowrealm" / "sessions"


@dataclass
class SessionMeta:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tier: str = "standard"
    label: str = ""
    status: str = "active"   # active | paused | completed | failed
    agent_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def summary(self) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at))
        return (f"[{self.session_id}] {ts}  tier={self.tier}  "
                f"status={self.status}  {self.label or '(no label)'}")


class SessionManager:
    """
    Create, persist, resume, and close sessions.

    Usage::

        sm = SessionManager()
        session = sm.create(tier_cfg, label="research-run")
        sm.register_agent(session.session_id, "planner")

        # Resume after crash:
        session = sm.resume(session_id="abc123")
        state = session.checkpoint_mgr.latest()

        sm.close(session.session_id, status="completed")
    """

    def __init__(self, base_dir: Path = SESSION_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, "Session"] = {}

    def create(
        self,
        tier_cfg: TierConfig,
        label: str = "",
        metadata: Optional[dict] = None,
    ) -> "Session":
        meta = SessionMeta(tier=tier_cfg.tier, label=label, metadata=metadata or {})
        session = Session(
            meta=meta,
            monitor=AgentMonitor(),
            checkpoint_mgr=CheckpointManager(session_id=meta.session_id),
            tier_cfg=tier_cfg,
        )
        self._active[meta.session_id] = session
        self._save_meta(meta)
        logger.info("Session created: %s", meta.summary())
        return session

    def resume(self, session_id: str) -> "Session":
        if session_id in self._active:
            return self._active[session_id]
        meta = self._load_meta(session_id)
        from core.tier_config import TIER_PROFILES
        tier_cfg = TIER_PROFILES.get(meta.tier)
        session = Session(
            meta=meta,
            monitor=AgentMonitor(),
            checkpoint_mgr=CheckpointManager(session_id=session_id),
            tier_cfg=tier_cfg,
        )
        self._active[session_id] = session
        logger.info("Session resumed: %s", meta.summary())
        return session

    def close(self, session_id: str, status: str = "completed") -> None:
        session = self._active.pop(session_id, None)
        if session:
            session.meta.status = status
            session.meta.updated_at = time.time()
            self._save_meta(session.meta)
            logger.info("Session closed: %s (%s)", session_id, status)

    def list_sessions(self) -> list[SessionMeta]:
        metas = []
        for f in sorted(self.base_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                metas.append(SessionMeta(**json.loads(f.read_text())))
            except Exception as e:
                logger.warning("Skipping corrupt session file %s: %s", f.name, e)
        return metas

    def register_agent(self, session_id: str, agent_id: str) -> None:
        session = self._active.get(session_id)
        if session and agent_id not in session.meta.agent_ids:
            session.meta.agent_ids.append(agent_id)
            self._save_meta(session.meta)

    def _save_meta(self, meta: SessionMeta) -> None:
        path = self.base_dir / f"{meta.session_id}.json"
        path.write_text(json.dumps(asdict(meta), indent=2))

    def _load_meta(self, session_id: str) -> SessionMeta:
        path = self.base_dir / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return SessionMeta(**json.loads(path.read_text()))


@dataclass
class Session:
    meta: SessionMeta
    monitor: AgentMonitor
    checkpoint_mgr: CheckpointManager
    tier_cfg: Optional[TierConfig] = None

    @property
    def session_id(self) -> str:
        return self.meta.session_id
