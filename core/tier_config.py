"""
C113 — Tier Configuration
Maps hardware tiers to LLM model choices, enabled features,
resource limits, and excluded capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.hardware_profiler import HardwareProfile


@dataclass
class TierConfig:
    tier: str
    llm_model: str
    embedding_model: str
    max_context_tokens: int
    max_agents: int
    max_ram_gb: float
    features: list[str] = field(default_factory=list)
    excluded_features: list[str] = field(default_factory=list)
    physics_model: Optional[str] = None
    chemistry_model: Optional[str] = None
    vision_model: Optional[str] = None
    notes: str = ""


TIER_PROFILES: dict[str, TierConfig] = {
    "minimal": TierConfig(
        tier="minimal",
        llm_model="tinyllama-1.1b",
        embedding_model="all-MiniLM-L6-v2",
        max_context_tokens=512,
        max_agents=1,
        max_ram_gb=1.5,
        features=["text_chat", "basic_memory"],
        excluded_features=[
            "remote_desktop", "computer_control", "vision",
            "physics_model", "chemistry_model", "multi_agent",
            "streaming_output", "vector_store",
        ],
        notes="Raspberry Pi / micro-controller. Single tiny LLM only.",
    ),
    "basic": TierConfig(
        tier="basic",
        llm_model="phi-2",
        embedding_model="all-MiniLM-L6-v2",
        max_context_tokens=2048,
        max_agents=2,
        max_ram_gb=6.0,
        features=["text_chat", "basic_memory", "vector_store", "tool_use"],
        excluded_features=[
            "remote_desktop", "computer_control", "vision",
            "physics_model", "chemistry_model",
        ],
        notes="Low-end laptop. Small model, no multimodal.",
    ),
    "standard": TierConfig(
        tier="standard",
        llm_model="llama3-8b",
        embedding_model="bge-base-en",
        max_context_tokens=8192,
        max_agents=4,
        max_ram_gb=12.0,
        features=[
            "text_chat", "basic_memory", "vector_store", "tool_use",
            "multi_agent", "streaming_output",
        ],
        excluded_features=["remote_desktop", "computer_control", "physics_model"],
        vision_model="clip-vit-base",
        notes="Mid-range laptop / desktop. 8B model, vision support.",
    ),
    "advanced": TierConfig(
        tier="advanced",
        llm_model="llama3-70b",
        embedding_model="bge-large-en",
        max_context_tokens=32768,
        max_agents=8,
        max_ram_gb=48.0,
        features=[
            "text_chat", "basic_memory", "vector_store", "tool_use",
            "multi_agent", "streaming_output", "vision",
            "computer_control", "remote_desktop",
        ],
        excluded_features=[],
        physics_model="mace-mp-0",
        chemistry_model="chemberta-77m",
        vision_model="llava-13b",
        notes="High-end laptop / gaming PC / Apple Silicon. Full feature set.",
    ),
    "enterprise": TierConfig(
        tier="enterprise",
        llm_model="llama3-405b",
        embedding_model="text-embedding-3-large",
        max_context_tokens=128000,
        max_agents=32,
        max_ram_gb=256.0,
        features=[
            "text_chat", "basic_memory", "vector_store", "tool_use",
            "multi_agent", "streaming_output", "vision",
            "computer_control", "remote_desktop",
            "distributed_agents", "model_parallel",
        ],
        excluded_features=[],
        physics_model="mace-mp-0b",
        chemistry_model="molbert-large",
        vision_model="llava-34b",
        notes="Multi-GPU workstation / server cluster. No restrictions.",
    ),
}


def resolve_tier_config(profile: HardwareProfile) -> TierConfig:
    cfg = TIER_PROFILES[profile.tier]
    if profile.ram_gb > 0:
        cfg.max_ram_gb = min(cfg.max_ram_gb, profile.ram_gb * 0.75)
    return cfg


def feature_enabled(cfg: TierConfig, feature: str) -> bool:
    return feature in cfg.features and feature not in cfg.excluded_features
