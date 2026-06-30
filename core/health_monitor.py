"""
C120 — Health Monitor
Periodically checks CPU, RAM, disk, and GPU usage and emits warnings
or throttle signals when resources are near limits. Integrates with
AgentMonitor so health events appear in the live dashboard.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.agent_monitor import AgentMonitor, EventKind

logger = logging.getLogger(__name__)

HEALTH_AGENT_ID = "system:health"


@dataclass
class HealthSnapshot:
    cpu_pct: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_pct: float = 0.0
    disk_free_gb: float = 0.0
    gpu_vram_used_gb: Optional[float] = None
    gpu_vram_total_gb: Optional[float] = None
    gpu_pct: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        gpu_str = ""
        if self.gpu_vram_total_gb:
            gpu_str = (f"  GPU VRAM: {self.gpu_vram_used_gb:.1f}/"
                       f"{self.gpu_vram_total_gb:.1f}GB ({self.gpu_pct:.0f}%)")
        return (
            f"CPU: {self.cpu_pct:.0f}%  "
            f"RAM: {self.ram_used_gb:.1f}/{self.ram_total_gb:.1f}GB ({self.ram_pct:.0f}%)"
            f"  Disk free: {self.disk_free_gb:.1f}GB{gpu_str}"
        )


class HealthMonitor:
    """
    Background thread that samples system resources and emits
    WARNING events into the AgentMonitor when thresholds are exceeded.

    Usage::

        hm = HealthMonitor(monitor, interval=10.0,
                           ram_warn_pct=80, cpu_warn_pct=90)
        hm.start()
        # ...agents run...
        hm.stop()
        snap = hm.latest()
    """

    def __init__(
        self,
        monitor: AgentMonitor,
        interval: float = 10.0,
        ram_warn_pct: float = 80.0,
        cpu_warn_pct: float = 90.0,
        disk_warn_gb: float = 2.0,
        gpu_warn_pct: float = 90.0,
    ):
        self.monitor = monitor
        self.interval = interval
        self.ram_warn_pct = ram_warn_pct
        self.cpu_warn_pct = cpu_warn_pct
        self.disk_warn_gb = disk_warn_gb
        self.gpu_warn_pct = gpu_warn_pct
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[HealthSnapshot] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="health-monitor")
        self._thread.start()
        logger.info("Health monitor started (interval=%.1fs)", self.interval)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def latest(self) -> Optional[HealthSnapshot]:
        return self._latest

    def _loop(self) -> None:
        while self._running:
            snap = self._sample()
            self._latest = snap
            self._check_and_emit(snap)
            time.sleep(self.interval)

    def _sample(self) -> HealthSnapshot:
        snap = HealthSnapshot()
        try:
            import psutil
            snap.cpu_pct = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            snap.ram_used_gb = mem.used / (1024 ** 3)
            snap.ram_total_gb = mem.total / (1024 ** 3)
            snap.ram_pct = mem.percent
            snap.disk_free_gb = psutil.disk_usage(".").free / (1024 ** 3)
        except ImportError:
            logger.debug("psutil not available; health checks limited")
        # GPU
        try:
            import torch
            if torch.cuda.is_available():
                used = torch.cuda.memory_allocated(0) / (1024 ** 3)
                total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
                snap.gpu_vram_used_gb = used
                snap.gpu_vram_total_gb = total
                snap.gpu_pct = (used / total * 100) if total else 0.0
        except Exception:
            pass
        return snap

    def _check_and_emit(self, snap: HealthSnapshot) -> None:
        if snap.ram_pct and snap.ram_pct >= self.ram_warn_pct:
            msg = f"High RAM usage: {snap.ram_pct:.0f}% ({snap.ram_used_gb:.1f}GB used)"
            snap.warnings.append(msg)
            self.monitor.emit(HEALTH_AGENT_ID, EventKind.WARNING, msg)
        if snap.cpu_pct and snap.cpu_pct >= self.cpu_warn_pct:
            msg = f"High CPU usage: {snap.cpu_pct:.0f}%"
            snap.warnings.append(msg)
            self.monitor.emit(HEALTH_AGENT_ID, EventKind.WARNING, msg)
        if snap.disk_free_gb and snap.disk_free_gb <= self.disk_warn_gb:
            msg = f"Low disk space: {snap.disk_free_gb:.1f}GB free"
            snap.warnings.append(msg)
            self.monitor.emit(HEALTH_AGENT_ID, EventKind.WARNING, msg)
        if snap.gpu_pct and snap.gpu_pct >= self.gpu_warn_pct:
            msg = f"High GPU VRAM: {snap.gpu_pct:.0f}% ({snap.gpu_vram_used_gb:.1f}GB used)"
            snap.warnings.append(msg)
            self.monitor.emit(HEALTH_AGENT_ID, EventKind.WARNING, msg)
        if not snap.warnings:
            self.monitor.emit(
                HEALTH_AGENT_ID, EventKind.STATUS, snap.summary()
            )
