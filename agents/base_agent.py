"""
C121 — Base Agent
Abstract base class for all ShadowRealm agents. Provides the core
think-act loop, monitor integration, checkpoint hooks, tier-aware
feature gating, and graceful pause/stop handling.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from core.agent_monitor import AgentMonitor, EventKind
from core.checkpoint_manager import CheckpointManager
from core.tier_config import TierConfig, feature_enabled

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    All agents inherit from BaseAgent.

    Subclasses must implement:
        think(state) -> thought: str
        act(thought, state)  -> result: Any
        is_done(state)       -> bool

    Optional hooks:
        on_start(state)
        on_step_end(step, thought, result, state)
        on_done(state)
        on_error(exc, state)
        state_dict()   -> dict   (for checkpointing)
        load_state(d)            (for restoration)
    """

    def __init__(
        self,
        agent_id: str,
        monitor: AgentMonitor,
        checkpoint_mgr: Optional[CheckpointManager] = None,
        tier_cfg: Optional[TierConfig] = None,
        max_steps: int = 100,
        step_delay: float = 0.0,
        auto_checkpoint_every: int = 0,
    ):
        self.agent_id = agent_id
        self.monitor = monitor
        self.checkpoint_mgr = checkpoint_mgr
        self.tier_cfg = tier_cfg
        self.max_steps = max_steps
        self.step_delay = step_delay
        self.auto_checkpoint_every = auto_checkpoint_every
        self.step = 0
        self._state: dict[str, Any] = {}

    def run(self, initial_state: Optional[dict] = None) -> dict:
        self._state = initial_state or {}
        self.monitor.register(self.agent_id)
        self.monitor.set_status(self.agent_id, "thinking")
        self._emit(EventKind.STATUS, "Agent started")
        try:
            self.on_start(self._state)
        except Exception as e:
            self._handle_error(e)
            return self._state

        while self.step < self.max_steps:
            while self.monitor.is_paused(self.agent_id):
                self.monitor.set_status(self.agent_id, "paused")
                time.sleep(0.5)
            if self.monitor.is_stop_requested(self.agent_id):
                self._emit(EventKind.STATUS, "Stop requested — halting")
                break

            self.monitor.set_status(self.agent_id, "thinking")
            try:
                thought = self.think(self._state)
            except Exception as e:
                self._handle_error(e)
                break

            self._emit(EventKind.THOUGHT, thought)
            self.monitor.set_last_thought(self.agent_id, thought)
            self.monitor.set_status(self.agent_id, "tool_call")

            try:
                result = self.act(thought, self._state)
            except Exception as e:
                self._handle_error(e)
                break

            self.step += 1
            self.monitor.set_step(self.agent_id, self.step)

            try:
                self.on_step_end(self.step, thought, result, self._state)
            except Exception as e:
                logger.warning("on_step_end raised: %s", e)

            if (
                self.auto_checkpoint_every > 0
                and self.checkpoint_mgr
                and self.step % self.auto_checkpoint_every == 0
            ):
                self.checkpoint_mgr.save(
                    agent_id=self.agent_id,
                    state=self.state_dict(),
                    auto=True,
                )

            if self.is_done(self._state):
                break
            if self.step_delay > 0:
                time.sleep(self.step_delay)

        self.monitor.set_status(self.agent_id, "done")
        self._emit(EventKind.STATUS, f"Done after {self.step} steps")
        try:
            self.on_done(self._state)
        except Exception as e:
            logger.warning("on_done raised: %s", e)
        return self._state

    @abstractmethod
    def think(self, state: dict) -> str: ...

    @abstractmethod
    def act(self, thought: str, state: dict) -> Any: ...

    @abstractmethod
    def is_done(self, state: dict) -> bool: ...

    def on_start(self, state: dict) -> None: pass
    def on_step_end(self, step: int, thought: str, result: Any, state: dict) -> None: pass
    def on_done(self, state: dict) -> None: pass
    def on_error(self, exc: Exception, state: dict) -> None: pass

    def state_dict(self) -> dict:
        return {"step": self.step, "state": self._state}

    def load_state(self, d: dict) -> None:
        self.step = d.get("step", 0)
        self._state = d.get("state", {})

    def requires_feature(self, feature: str) -> bool:
        if self.tier_cfg is None:
            return True
        if not feature_enabled(self.tier_cfg, feature):
            self._emit(EventKind.WARNING, f"Feature '{feature}' not available on tier '{self.tier_cfg.tier}'")
            return False
        return True

    def _emit(self, kind: EventKind, message: str) -> None:
        self.monitor.emit(self.agent_id, kind, message)

    def _handle_error(self, exc: Exception) -> None:
        logger.error("[%s] Error: %s", self.agent_id, exc, exc_info=True)
        self.monitor.set_status(self.agent_id, "error")
        self.monitor.set_error(self.agent_id, str(exc))
        self._emit(EventKind.ERROR, str(exc))
        try:
            self.on_error(exc, self._state)
        except Exception:
            pass
