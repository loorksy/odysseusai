"""
C114 — Agent Monitor
Real-time visibility into agent activity: thought stream, tool calls,
status, resource usage. Provides a TUI-friendly event log and
a live dashboard snapshot the setup wizard and UI layer can render.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class EventKind(str, Enum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATUS = "status"
    WARNING = "warning"
    ERROR = "error"
    CHECKPOINT = "checkpoint"
    USER_PAUSE = "user_pause"


@dataclass
class MonitorEvent:
    agent_id: str
    kind: EventKind
    message: str
    data: Any = None
    timestamp: float = field(default_factory=time.time)
    step: int = 0

    def display(self) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{ts}] [{self.agent_id}] {self.kind.value.upper():12s} {self.message}"


@dataclass
class AgentSnapshot:
    agent_id: str
    status: str = "idle"
    current_task: str = ""
    last_thought: str = ""
    last_tool: str = ""
    step: int = 0
    paused: bool = False
    error: Optional[str] = None


class AgentMonitor:
    """
    Central event bus for all agent activity.

    Agents call emit() to broadcast events. The UI layer subscribes
    via subscribe() and receives events on a background thread.
    The user can call pause_agent() / resume_agent() / stop_all()
    at any time to intervene.

    Usage::

        monitor = AgentMonitor(max_history=500)
        monitor.subscribe(lambda e: print(e.display()))

        monitor.emit("agent-1", EventKind.THOUGHT, "Planning next step...")
        monitor.emit("agent-1", EventKind.TOOL_CALL, "search", data={"query": "RAG"})

        monitor.pause_agent("agent-1")
        monitor.resume_agent("agent-1")
        monitor.stop_all()
    """

    def __init__(self, max_history: int = 1000):
        self._history: deque[MonitorEvent] = deque(maxlen=max_history)
        self._snapshots: dict[str, AgentSnapshot] = {}
        self._subscribers: list[Callable[[MonitorEvent], None]] = []
        self._stop_requested: bool = False
        self._lock = threading.Lock()
        self._step_counter: dict[str, int] = {}

    def emit(self, agent_id: str, kind: EventKind, message: str, data: Any = None) -> None:
        with self._lock:
            step = self._step_counter.get(agent_id, 0) + 1
            self._step_counter[agent_id] = step
            event = MonitorEvent(agent_id=agent_id, kind=kind, message=message,
                                 data=data, step=step)
            self._history.append(event)
            self._update_snapshot(agent_id, event)
        for sub in self._subscribers:
            try:
                sub(event)
            except Exception as e:
                logger.debug("Monitor subscriber error: %s", e)

    def _update_snapshot(self, agent_id: str, event: MonitorEvent) -> None:
        snap = self._snapshots.setdefault(agent_id, AgentSnapshot(agent_id=agent_id))
        snap.step = event.step
        if event.kind == EventKind.THOUGHT:
            snap.status = "thinking"
            snap.last_thought = event.message[:120]
        elif event.kind == EventKind.TOOL_CALL:
            snap.status = "tool_call"
            snap.last_tool = event.message
        elif event.kind == EventKind.ERROR:
            snap.status = "error"
            snap.error = event.message
        elif event.kind == EventKind.STATUS:
            snap.status = event.message
        elif event.kind == EventKind.USER_PAUSE:
            snap.paused = True
            snap.status = "paused"

    def subscribe(self, callback: Callable[[MonitorEvent], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[MonitorEvent], None]) -> None:
        self._subscribers.remove(callback)

    def pause_agent(self, agent_id: str) -> None:
        self.emit(agent_id, EventKind.USER_PAUSE, "User requested pause")
        logger.info("Agent %s paused by user", agent_id)

    def resume_agent(self, agent_id: str) -> None:
        with self._lock:
            if agent_id in self._snapshots:
                self._snapshots[agent_id].paused = False
                self._snapshots[agent_id].status = "thinking"
        self.emit(agent_id, EventKind.STATUS, "resumed")

    def stop_all(self) -> None:
        self._stop_requested = True
        for agent_id in list(self._snapshots.keys()):
            self.emit(agent_id, EventKind.STATUS, "stopped")
        logger.warning("All agents stopped by user")

    def is_paused(self, agent_id: str) -> bool:
        return self._snapshots.get(agent_id, AgentSnapshot(agent_id)).paused

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def snapshot(self, agent_id: str) -> AgentSnapshot:
        return self._snapshots.get(agent_id, AgentSnapshot(agent_id=agent_id))

    def all_snapshots(self) -> list[AgentSnapshot]:
        return list(self._snapshots.values())

    def recent(self, n: int = 50, agent_id: Optional[str] = None) -> list[MonitorEvent]:
        events = list(self._history)
        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        return events[-n:]

    def dashboard(self) -> str:
        lines = ["=== ShadowRealm Agent Monitor ==="]
        for snap in self.all_snapshots():
            status_icon = {
                "thinking": "🔵", "tool_call": "🟡", "paused": "⏸️",
                "error": "🔴", "done": "✅", "idle": "⚪"
            }.get(snap.status, "❓")
            lines.append(
                f"  {status_icon} {snap.agent_id:<20} [{snap.status:<10}] "
                f"step={snap.step}  task={snap.current_task[:40]}"
            )
            if snap.last_thought:
                lines.append(f"      💭 {snap.last_thought[:80]}")
            if snap.error:
                lines.append(f"      ❗ {snap.error[:80]}")
        return "\n".join(lines)
