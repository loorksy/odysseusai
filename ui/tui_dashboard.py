"""
C117 — TUI Dashboard
Terminal UI that renders a live agent monitor dashboard.
Refreshes every N seconds, shows agent status, recent events,
checkpoint log, and provides keyboard controls for pause/resume/stop.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

from core.agent_monitor import AgentMonitor, EventKind, MonitorEvent
from core.checkpoint_manager import CheckpointManager


class TUIDashboard:
    """
    Lightweight TUI dashboard using only stdlib (no curses dependency).
    Clears the terminal and redraws every `refresh_interval` seconds.

    Controls (keyboard, non-blocking)
    ----------------------------------
    p  : pause all agents
    r  : resume all agents
    s  : stop all agents
    q  : quit dashboard (agents keep running)
    c  : print checkpoint list

    Usage::

        monitor = AgentMonitor()
        mgr = CheckpointManager(session_id="run-001")
        dash = TUIDashboard(monitor, mgr, refresh_interval=2.0)
        dash.start()   # non-blocking background thread
        # ... agents run ...
        dash.stop()
    """

    CLEAR = "\033[2J\033[H"
    BOLD  = "\033[1m"
    RESET = "\033[0m"
    RED   = "\033[31m"
    GRN   = "\033[32m"
    YLW   = "\033[33m"
    CYN   = "\033[36m"

    def __init__(
        self,
        monitor: AgentMonitor,
        checkpoint_mgr: Optional[CheckpointManager] = None,
        refresh_interval: float = 2.0,
        max_events: int = 15,
    ):
        self.monitor = monitor
        self.checkpoint_mgr = checkpoint_mgr
        self.refresh_interval = refresh_interval
        self.max_events = max_events
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._event_buffer: list[MonitorEvent] = []
        monitor.subscribe(self._on_event)

    def _on_event(self, event: MonitorEvent) -> None:
        self._event_buffer.append(event)
        if len(self._event_buffer) > 200:
            self._event_buffer = self._event_buffer[-200:]

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while self._running:
            self._render()
            time.sleep(self.refresh_interval)

    def _render(self) -> None:
        lines = []
        lines.append(self.BOLD + "=" * 60 + self.RESET)
        lines.append(self.BOLD + "  ShadowRealm  |  Agent Dashboard  |  " +
                     time.strftime("%H:%M:%S") + self.RESET)
        lines.append("=" * 60)

        # Agent status table
        lines.append(self.CYN + "  AGENTS" + self.RESET)
        for snap in self.monitor.all_snapshots():
            icon = {"thinking": "🔵", "tool_call": "🟡", "paused": "⏸️",
                    "error": "🔴", "done": "✅", "idle": "⚫"}.get(snap.status, "❓")
            color = self.RED if snap.status == "error" else (
                    self.YLW if snap.status == "paused" else self.GRN)
            lines.append(
                f"  {icon} {color}{snap.agent_id:<18}{self.RESET} "
                f"[{snap.status:<10}] step={snap.step}"
            )
            if snap.last_thought:
                lines.append(f"       💭 {snap.last_thought[:70]}")
            if snap.error:
                lines.append(self.RED + f"       ❗ {snap.error[:70]}" + self.RESET)

        # Recent events
        lines.append("")
        lines.append(self.CYN + "  RECENT EVENTS" + self.RESET)
        recent = self._event_buffer[-self.max_events:]
        for e in recent:
            kind_color = {
                EventKind.ERROR:   self.RED,
                EventKind.WARNING: self.YLW,
                EventKind.THOUGHT: self.GRN,
            }.get(e.kind, "")
            lines.append(
                f"  {kind_color}[{e.agent_id:<12}] "
                f"{e.kind.value.upper():<12} {e.message[:50]}{self.RESET}"
            )

        # Checkpoint summary
        if self.checkpoint_mgr:
            lines.append("")
            lines.append(self.CYN + "  CHECKPOINTS" + self.RESET)
            cps = self.checkpoint_mgr.list()[-5:]
            if cps:
                for cp in cps:
                    lines.append(f"  {'[auto]' if cp.auto else '[manual]'} {cp.summary()[:60]}")
            else:
                lines.append("  (none yet)")

        lines.append("")
        lines.append("  Controls: [p]ause  [r]esume  [s]top all  [q]uit  [c]heckpoints")
        lines.append("=" * 60)

        sys.stdout.write(self.CLEAR + "\n".join(lines) + "\n")
        sys.stdout.flush()

    def handle_key(self, key: str) -> None:
        """Process a single keypress from the caller's input loop."""
        key = key.strip().lower()
        if key == "p":
            for snap in self.monitor.all_snapshots():
                self.monitor.pause_agent(snap.agent_id)
        elif key == "r":
            for snap in self.monitor.all_snapshots():
                self.monitor.resume_agent(snap.agent_id)
        elif key == "s":
            self.monitor.stop_all()
        elif key == "q":
            self.stop()
        elif key == "c" and self.checkpoint_mgr:
            for cp in self.checkpoint_mgr.list():
                print(cp.summary())
