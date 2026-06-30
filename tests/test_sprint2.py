# tests/test_sprint2.py
# Sprint 2 unit tests — covers TokenCounter, ContextProfile selection,
# ToolSelector scoring, and the compaction threshold logic.
#
# Run: pytest tests/test_sprint2.py -v

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# TokenCounter
# ---------------------------------------------------------------------------

class TestTokenCounter:
    def setup_method(self):
        from core.token_counter import TokenCounter
        self.counter = TokenCounter()   # fresh instance per test

    def test_record_and_snapshot(self):
        self.counter.record("sess1", 100, 50, model="gpt-4o", source="chat")
        snap = self.counter.snapshot("sess1")
        assert snap is not None
        assert snap.tokens_in == 100
        assert snap.tokens_out == 50
        assert snap.total == 150

    def test_multiple_records_accumulate(self):
        for _ in range(5):
            self.counter.record("sess1", 100, 40)
        snap = self.counter.snapshot("sess1")
        assert snap.tokens_in == 500
        assert snap.tokens_out == 200

    def test_unknown_session_returns_none(self):
        assert self.counter.snapshot("ghost") is None

    def test_totals_aggregate_across_sessions(self):
        self.counter.record("a", 300, 100)
        self.counter.record("b", 200, 50)
        t = self.counter.totals()
        assert t["tokens_in"] == 500
        assert t["tokens_out"] == 150
        assert t["total"] == 650
        assert t["sessions"] == 2

    def test_by_source_breakdown(self):
        self.counter.record("s", 100, 50, source="chat")
        self.counter.record("s", 200, 80, source="skill:web")
        snap = self.counter.snapshot("s")
        bs = snap.by_source()
        assert bs["chat"] == 150
        assert bs["skill:web"] == 280

    def test_reset_session(self):
        self.counter.record("r", 100, 50)
        self.counter.reset_session("r")
        assert self.counter.snapshot("r") is None

    def test_negative_tokens_clamped_to_zero(self):
        self.counter.record("neg", -50, -20)
        snap = self.counter.snapshot("neg")
        assert snap.total == 0


# ---------------------------------------------------------------------------
# ContextProfile selection
# ---------------------------------------------------------------------------

class TestContextProfiles:
    def test_known_large_model_gets_large_profile(self):
        from core.context_profiles import get_profile
        p = get_profile("gpt-4o")
        assert p.name == "large"
        assert p.window_tokens == 128_000

    def test_known_small_model_gets_small_profile(self):
        from core.context_profiles import get_profile
        p = get_profile("phi3")
        assert p.name == "small"

    def test_unknown_model_fallback(self):
        from core.context_profiles import get_profile
        p = get_profile("totally-unknown-model-xyz")
        assert p.name == "small"  # conservative fallback to 8192 window

    def test_compaction_limit_is_fraction_of_window(self):
        from core.context_profiles import LARGE
        assert LARGE.compaction_limit == int(128_000 * 0.80)

    def test_profile_for_tokens(self):
        from core.context_profiles import get_profile_for_tokens, MEDIUM
        p = get_profile_for_tokens(32_000)
        assert p.name == MEDIUM.name

    def test_all_profiles_returns_dicts(self):
        from core.context_profiles import all_profiles
        profiles = all_profiles()
        assert len(profiles) == 3
        assert all(isinstance(p, dict) for p in profiles)
        names = {p["name"] for p in profiles}
        assert names == {"small", "medium", "large"}


# ---------------------------------------------------------------------------
# ToolSelector
# ---------------------------------------------------------------------------

class TestToolSelector:
    def _make_tools(self):
        from core.tool_selector import ToolDescriptor
        return [
            ToolDescriptor(name="web_search", description="Search the web for information", tags=["search", "web"]),
            ToolDescriptor(name="memory_read", description="Read from memory store", tags=["memory"], always_include=True),
            ToolDescriptor(name="run_shell", description="Execute a shell command", tags=["shell", "bash"]),
            ToolDescriptor(name="git_commit", description="Commit changes to git repository", tags=["git"]),
            ToolDescriptor(name="read_file", description="Read a file from the filesystem", tags=["file"]),
        ]

    def test_always_include_pinned(self):
        from core.tool_selector import ToolSelector
        from core.context_profiles import MEDIUM
        selector = ToolSelector(self._make_tools(), profile=MEDIUM)
        selected = selector.select("search for python docs")
        names = [t.name for t in selected]
        assert "memory_read" in names

    def test_task_keyword_scoring(self):
        from core.tool_selector import ToolSelector
        from core.context_profiles import MEDIUM
        selector = ToolSelector(self._make_tools(), profile=MEDIUM)
        selected = selector.select("search the web for information")
        names = [t.name for t in selected]
        assert "web_search" in names

    def test_slot_limit_respected(self):
        from core.tool_selector import ToolSelector, ToolDescriptor
        from core.context_profiles import SMALL   # max_tool_slots=5
        tools = [ToolDescriptor(name=f"tool_{i}", description=f"Tool {i}") for i in range(20)]
        selector = ToolSelector(tools, profile=SMALL)
        selected = selector.select("anything")
        assert len(selected) <= SMALL.max_tool_slots

    def test_force_include_overrides(self):
        from core.tool_selector import ToolSelector
        from core.context_profiles import SMALL  # only 5 slots
        selector = ToolSelector(self._make_tools(), profile=SMALL)
        selected = selector.select("unrelated task", force_include=["run_shell"])
        names = [t.name for t in selected]
        assert "run_shell" in names

    def test_build_registry(self):
        from core.tool_selector import build_registry
        raw = [
            {"name": "search", "description": "Search the web for answers"},
            {"name": "memory_write", "description": "Write to memory"},
        ]
        reg = build_registry(raw, always_include=["memory_write"])
        assert len(reg) == 2
        mw = next(t for t in reg if t.name == "memory_write")
        assert mw.always_include is True


# ---------------------------------------------------------------------------
# Compaction threshold logic (unit — no HTTP layer)
# ---------------------------------------------------------------------------

class TestCompactionThreshold:
    """Tests the compaction trigger decision in isolation."""

    def test_under_threshold_no_compaction(self):
        from core.context_profiles import LARGE
        from core.token_counter import TokenCounter
        counter = TokenCounter()
        counter.record("s", 10_000, 5_000)  # 15k — well under 102k limit
        snap = counter.snapshot("s")
        assert snap.total < LARGE.compaction_limit

    def test_over_threshold_triggers(self):
        from core.context_profiles import LARGE
        from core.token_counter import TokenCounter
        counter = TokenCounter()
        # Push over 80% of 128k = 102 400
        counter.record("s", 60_000, 50_000)  # 110k > 102.4k
        snap = counter.snapshot("s")
        assert snap.total >= LARGE.compaction_limit

    def test_summary_message_contains_compacted_marker(self):
        from core.compaction_middleware import _build_summary_message
        from core.context_profiles import MEDIUM
        messages = [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I am fine, thanks!"},
            {"role": "user", "content": "Tell me about Python."},
        ]
        msg = _build_summary_message(messages, MEDIUM)
        assert msg["role"] == "system"
        assert "[CONTEXT COMPACTED]" in msg["content"]

    def test_summary_includes_recent_turns(self):
        from core.compaction_middleware import _build_summary_message
        from core.context_profiles import MEDIUM
        messages = [
            {"role": "user", "content": "Tell me about the Eiffel Tower."},
            {"role": "assistant", "content": "It is in Paris."},
        ]
        msg = _build_summary_message(messages, MEDIUM)
        # Should contain a snippet of the conversation
        assert "Eiffel" in msg["content"] or "Paris" in msg["content"]
