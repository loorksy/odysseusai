import asyncio

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools import pipeline_tools as pt
from pathlib import Path

def test_pipeline_registered():
    assert "pipeline" in TOOL_HANDLERS, "pipeline missing from TOOL_HANDLERS"

def test_pipeline_not_routed_via_dispatch_ai_tool():
    source = (Path(__file__).resolve().parent.parent / "src" / "tool_execution.py").read_text(encoding="utf-8")
    marker = "from src.ai_interaction import dispatch_ai_tool"
    idx = source.index(marker)
    branch_head = source.rfind("elif tool in (", 0, idx)
    legacy_tuple = source[branch_head:idx]
    
    assert "pipeline" not in legacy_tuple, "pipeline still routed via dispatch_ai_tool"

def test_pipeline_handler_threads_ctx(monkeypatch):
    # PipelineTool.execute() must unpack session_id + owner from ctx into
    # do_pipeline - guards the ctx adapter (would catch a mis-keyed ctx.get).
    seen = {}
    async def spy(content, session_id=None, owner=None):
        seen.update(content=content, session_id=session_id, owner=owner)
        return {"results": "ok"}
    monkeypatch.setattr(pt, "do_pipeline", spy)
    res = asyncio.run(pt.PipelineTool().execute("q", {"owner": "alice", "session_id": "s1"}))
    assert res == {"results": "ok"}
    assert seen == {"content": "q", "session_id": "s1", "owner": "alice"} 