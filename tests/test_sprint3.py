# tests/test_sprint3.py
"""Sprint 3 tests (C25) for SkillRegistry, AgentHarness, and TrainingInterface."""

import pytest
from core.skill_registry import SkillRegistry
from core.agent_harness import AgentHarness
from core.training_interface import TrainingInterface
from services.memory.skills import SkillsManager
from core.token_counter import TokenCounter

class DummySkillsManager:
    def __init__(self):
        self.skills = [
            {
                "name": "test_skill",
                "description": "a test skill description",
                "category": "testing",
                "owner": "alice",
                "markdown": "## Active Skill: test_skill\n\nFull instructions go here."
            }
        ]

    def index_for(self, owner=None, active_toolsets=None, platform=None):
        return self.skills

    def read_skill_md(self, name, owner=None):
        for s in self.skills:
            if s["name"] == name:
                return s["markdown"]
        return None

    def record_use(self, name, owner=None):
        pass

    def load(self, owner=None):
        return self.skills

def test_skill_registry_progressive_disclosure():
    sm = DummySkillsManager()
    reg = SkillRegistry(sm)

    # Test compact index
    idx = reg.compact_index(owner="alice")
    assert len(idx) == 1
    assert idx[0]["name"] == "test_skill"
    assert idx[0]["description"] == "a test skill description"
    # Ensure full instructions are NOT present in index
    assert "markdown" not in idx[0]
    assert "Full instructions" not in idx[0]["description"]

    # Test selection / on-demand load
    full_md = reg.select("test_skill", owner="alice")
    assert "Full instructions go here." in full_md


def test_agent_harness_routing_and_tokens():
    sm = DummySkillsManager()
    harness = AgentHarness(sm, owner="alice")
    harness.begin_session("test_session_123")

    # Test status
    status = harness.status()
    assert status["session_id"] == "test_session_123"
    assert status["owner"] == "alice"
    assert status["tokens_used"] == 0

    # Test record_turn
    harness.record_turn(150, source="test_chat")
    assert harness.tokens_used == 150
    assert TokenCounter.get().totals()["tokens_in"] >= 150


def test_training_interface_trace_and_crystallization():
    sm = DummySkillsManager()
    harness = AgentHarness(sm, owner="alice")
    ti = TrainingInterface(harness)

    ti.start(session_id="session_train", goal="teach agent to print hello")
    assert ti.is_active

    # Capture turn
    ti.capture_turn("hello", "world", tool_calls=[])
    assert ti.turn_count == 1

    # Stop and crystallize
    payload = ti.crystallize()
    assert payload["goal"] == "teach agent to print hello"
    assert "world" in payload["trace"]
    assert "teach agent to print hello" in payload["trace"]
    assert "crystallizing a workflow" in payload["system_prompt"]

    ti.stop()
    assert not ti.is_active
