"""
C122 — Planner Agent
A concrete BaseAgent that decomposes a high-level goal into an ordered
list of sub-tasks, then iterates through them, delegating each to a
registered executor or tool. Supports dynamic task insertion mid-run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agents.base_agent import BaseAgent
from core.agent_monitor import AgentMonitor, EventKind
from core.checkpoint_manager import CheckpointManager
from core.tier_config import TierConfig

logger = logging.getLogger(__name__)


@dataclass
class Task:
    task_id: str
    description: str
    status: str = "pending"
    result: Any = None
    error: str = ""
    priority: int = 0

    def summary(self) -> str:
        return f"[{self.task_id}] {self.description[:60]} ({self.status})"


class PlannerAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str,
        monitor: AgentMonitor,
        goal: str,
        decompose_fn: Callable[[str], list[Task]],
        executor_fn: Callable[[Task, dict], Any],
        checkpoint_mgr: Optional[CheckpointManager] = None,
        tier_cfg: Optional[TierConfig] = None,
        max_steps: int = 200,
        auto_checkpoint_every: int = 5,
    ):
        super().__init__(
            agent_id=agent_id, monitor=monitor,
            checkpoint_mgr=checkpoint_mgr, tier_cfg=tier_cfg,
            max_steps=max_steps, auto_checkpoint_every=auto_checkpoint_every,
        )
        self.goal = goal
        self.decompose_fn = decompose_fn
        self.executor_fn = executor_fn
        self.tasks: list[Task] = []
        self._current_task_idx: int = 0

    def on_start(self, state: dict) -> None:
        self._emit(EventKind.THOUGHT, f"Decomposing goal: {self.goal}")
        self.tasks = self.decompose_fn(self.goal)
        state["goal"] = self.goal
        state["tasks"] = [t.task_id for t in self.tasks]
        self._emit(EventKind.STATUS, f"{len(self.tasks)} tasks generated")

    def think(self, state: dict) -> str:
        if self._current_task_idx >= len(self.tasks):
            return "All tasks complete."
        task = self.tasks[self._current_task_idx]
        return f"Execute task {task.task_id}: {task.description}"

    def act(self, thought: str, state: dict) -> Any:
        if self._current_task_idx >= len(self.tasks):
            return None
        task = self.tasks[self._current_task_idx]
        task.status = "running"
        self._emit(EventKind.TOOL_CALL, f"task={task.task_id} | {task.description[:60]}")
        try:
            result = self.executor_fn(task, state)
            task.status = "done"
            task.result = result
            state[f"result_{task.task_id}"] = result
            self._emit(EventKind.TOOL_RESULT, f"task={task.task_id} done")
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            self._emit(EventKind.ERROR, f"task={task.task_id} failed: {e}")
        self._current_task_idx += 1
        return task.result

    def is_done(self, state: dict) -> bool:
        return self._current_task_idx >= len(self.tasks)

    def insert_task(self, task: Task, after_current: bool = True) -> None:
        idx = self._current_task_idx + (1 if after_current else 0)
        self.tasks.insert(idx, task)
        self._emit(EventKind.STATUS, f"Task injected: {task.summary()}")

    def failed_tasks(self) -> list[Task]:
        return [t for t in self.tasks if t.status == "failed"]

    def state_dict(self) -> dict:
        base = super().state_dict()
        base["tasks"] = [
            {"task_id": t.task_id, "description": t.description,
             "status": t.status, "error": t.error, "priority": t.priority}
            for t in self.tasks
        ]
        base["current_task_idx"] = self._current_task_idx
        return base

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self._current_task_idx = d.get("current_task_idx", 0)
        self.tasks = [
            Task(**{k: v for k, v in t.items() if k in Task.__dataclass_fields__})
            for t in d.get("tasks", [])
        ]
