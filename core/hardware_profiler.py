"""
C112 — Hardware Profiler
Detects CPU, RAM, GPU, disk, and OS info to produce a HardwareProfile
used by the tier selector and setup wizard.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class HardwareProfile:
    os_name: str = ""
    os_version: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_gb: float = 0.0
    disk_free_gb: float = 0.0
    gpu_name: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    apple_silicon: bool = False
    cuda_available: bool = False
    metal_available: bool = False
    tier: str = "minimal"
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        gpu_str = f"{self.gpu_name} ({self.gpu_vram_gb:.1f}GB VRAM)" if self.gpu_name else "None"
        return (
            f"OS: {self.os_name} {self.os_version}\n"
            f"CPU: {self.cpu_cores}c/{self.cpu_threads}t  RAM: {self.ram_gb:.1f}GB  "
            f"Disk free: {self.disk_free_gb:.1f}GB\n"
            f"GPU: {gpu_str}  CUDA: {self.cuda_available}  Metal: {self.metal_available}\n"
            f"Tier: {self.tier.upper()}"
        )


class HardwareProfiler:
    """
    Collect system hardware info and assign a capability tier.

    Tiers
    -----
    minimal    : Raspberry Pi / very old hardware (<2GB RAM)
    basic      : Low-end laptop (2-8GB RAM, no GPU)
    standard   : Mid-range laptop / desktop (8-16GB RAM)
    advanced   : High-end laptop / gaming PC / Mac Studio (16-64GB, GPU)
    enterprise : Multi-GPU workstation / server farm (64GB+, multiple GPUs)
    """

    def profile(self) -> HardwareProfile:
        p = HardwareProfile()
        self._detect_os(p)
        self._detect_cpu_ram(p)
        self._detect_disk(p)
        self._detect_gpu(p)
        self._assign_tier(p)
        return p

    def _detect_os(self, p: HardwareProfile) -> None:
        p.os_name = platform.system()
        p.os_version = platform.version()
        machine = platform.machine().lower()
        if p.os_name == "Darwin" and ("arm" in machine or "apple" in machine):
            p.apple_silicon = True

    def _detect_cpu_ram(self, p: HardwareProfile) -> None:
        try:
            import psutil
            p.cpu_cores = psutil.cpu_count(logical=False) or 1
            p.cpu_threads = psutil.cpu_count(logical=True) or 1
            p.ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        except ImportError:
            p.cpu_cores = os.cpu_count() or 1
            p.cpu_threads = p.cpu_cores
            p.warnings.append("psutil not installed; RAM detection limited")

    def _detect_disk(self, p: HardwareProfile) -> None:
        try:
            usage = shutil.disk_usage(os.path.expanduser("~"))
            p.disk_free_gb = usage.free / (1024 ** 3)
        except Exception:
            p.disk_free_gb = 0.0

    def _detect_gpu(self, p: HardwareProfile) -> None:
        try:
            import torch
            if torch.cuda.is_available():
                p.cuda_available = True
                p.gpu_name = torch.cuda.get_device_name(0)
                p.gpu_vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        except ImportError:
            pass
        if p.apple_silicon:
            try:
                import torch
                if torch.backends.mps.is_available():
                    p.metal_available = True
                    p.gpu_name = "Apple Silicon GPU (MPS)"
                    p.gpu_vram_gb = p.ram_gb
            except Exception:
                pass

    def _assign_tier(self, p: HardwareProfile) -> None:
        has_gpu = p.cuda_available or p.metal_available
        if p.ram_gb < 2:
            p.tier = "minimal"
        elif p.ram_gb < 8 and not has_gpu:
            p.tier = "basic"
        elif p.ram_gb < 16 and not has_gpu:
            p.tier = "standard"
        elif p.ram_gb < 64 or (has_gpu and (p.gpu_vram_gb or 0) < 16):
            p.tier = "advanced"
        else:
            p.tier = "enterprise"
