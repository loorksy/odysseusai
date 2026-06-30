"""
C116 — Setup Wizard
Interactive CLI setup that profiles hardware, selects a tier,
explains what's enabled/excluded, and writes a config file.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from core.hardware_profiler import HardwareProfiler, HardwareProfile
from core.tier_config import resolve_tier_config, TierConfig, feature_enabled

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.path.expanduser("~/.shadowrealm/config.json"))


class SetupWizard:
    """
    Runs on first launch (or --setup flag).

    Steps
    -----
    1. Print welcome banner
    2. Detect hardware -> HardwareProfile
    3. Resolve TierConfig -> show enabled / excluded features
    4. Let user confirm or manually override tier
    5. Write ~/.shadowrealm/config.json
    6. Print next-steps summary

    Usage::

        wizard = SetupWizard()
        wizard.run()
    """

    def __init__(self, interactive: bool = True, force_tier: Optional[str] = None):
        self.interactive = interactive
        self.force_tier = force_tier
        self.profiler = HardwareProfiler()

    def run(self) -> dict:
        self._banner()
        print("\n🔍  Scanning hardware...")
        profile = self.profiler.profile()
        print(profile.summary())

        if profile.warnings:
            for w in profile.warnings:
                print(f"  ⚠️  {w}")

        if self.force_tier:
            profile.tier = self.force_tier
            print(f"\n  Tier overridden to: {self.force_tier.upper()}")

        cfg = resolve_tier_config(profile)
        self._print_tier_summary(cfg)

        if self.interactive:
            override = input("\nAccept this tier? [Y/n/tier-name]: ").strip().lower()
            if override and override not in ("y", "yes", ""):
                from core.tier_config import TIER_PROFILES
                if override in TIER_PROFILES:
                    profile.tier = override
                    cfg = resolve_tier_config(profile)
                    self._print_tier_summary(cfg)
                else:
                    print(f"  Unknown tier '{override}', keeping {cfg.tier}")

        config = self._build_config(profile, cfg)
        self._write_config(config)
        self._print_next_steps(cfg)
        return config

    def _banner(self) -> None:
        print("""
╔══════════════════════════════════════════╗
║        ShadowRealm  —  Setup Wizard      ║
╚══════════════════════════════════════════╝
        """)

    def _print_tier_summary(self, cfg: TierConfig) -> None:
        print(f"\n{'='*44}")
        print(f"  Tier       : {cfg.tier.upper()}")
        print(f"  LLM Model  : {cfg.llm_model}")
        print(f"  Embeddings : {cfg.embedding_model}")
        print(f"  Context    : {cfg.max_context_tokens:,} tokens")
        print(f"  Max Agents : {cfg.max_agents}")
        print(f"  Max RAM    : {cfg.max_ram_gb:.1f} GB")
        print(f"{'='*44}")
        print("  ✅ ENABLED features:")
        for f in cfg.features:
            print(f"      • {f}")
        if cfg.excluded_features:
            print("  ❌ EXCLUDED features (hardware limit):")
            for f in cfg.excluded_features:
                print(f"      • {f}")
        if cfg.physics_model:
            print(f"  🔬 Physics model   : {cfg.physics_model}")
        if cfg.chemistry_model:
            print(f"  ⚗️  Chemistry model : {cfg.chemistry_model}")
        if cfg.vision_model:
            print(f"  👁️  Vision model    : {cfg.vision_model}")
        if cfg.notes:
            print(f"  📝 {cfg.notes}")
        print(f"{'='*44}")

    def _print_next_steps(self, cfg: TierConfig) -> None:
        print("\n🎉  Setup complete!")
        print("    Config saved to ~/.shadowrealm/config.json")
        print("    Checkpoints will be saved to ~/.shadowrealm/checkpoints/")
        print("    Run `python -m shadowrealm` to start.")
        if cfg.tier in ("minimal", "basic"):
            print("    💡 Tip: upgrade RAM or add a GPU to unlock more features.")

    def _build_config(self, profile: HardwareProfile, cfg: TierConfig) -> dict:
        return {
            "version": "2.0",
            "tier": cfg.tier,
            "llm_model": cfg.llm_model,
            "embedding_model": cfg.embedding_model,
            "max_context_tokens": cfg.max_context_tokens,
            "max_agents": cfg.max_agents,
            "max_ram_gb": cfg.max_ram_gb,
            "features": cfg.features,
            "excluded_features": cfg.excluded_features,
            "physics_model": cfg.physics_model,
            "chemistry_model": cfg.chemistry_model,
            "vision_model": cfg.vision_model,
            "hardware": {
                "os": profile.os_name,
                "cpu_cores": profile.cpu_cores,
                "ram_gb": round(profile.ram_gb, 2),
                "gpu": profile.gpu_name,
                "cuda": profile.cuda_available,
                "metal": profile.metal_available,
            },
        }

    def _write_config(self, config: dict) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2))
        logger.info("Config written to %s", CONFIG_PATH)
