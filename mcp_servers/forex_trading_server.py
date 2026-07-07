"""MCP server exposing forex analysis, recommendations, news, chart draw commands and paper trades."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.forex_news import get_pair_news
from src.oanda_client import get_oanda_client
from src.strategy_engine import load_catalog, recommend_from_candles

server = Server("forex_trading")
STATE_FILE = Path(os.getenv("FOREX_MCP_STATE_FILE", "data/forex_mcp_state.json"))


def _text(value) -> list[TextContent]:
    return [TextContent(type="text", text=value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))]


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"selected_pair": "EUR_USD", "trades": []}


def _save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pair(value: str | None) -> str:
    return (value or _load_state().get("selected_pair") or "EUR_USD").upper().replace("/", "_")


@server.list_tools()
async def list_tools() -> list[Tool]:
    schema_pair_tf = {"type": "object", "properties": {"pair": {"type": "string"}, "timeframe": {"type": "string", "default": "H1"}}}
    return [
        Tool(name="get_selected_pair", description="Return the selected forex pair.", inputSchema={"type": "object", "properties": {}}),
        Tool(name="set_selected_pair", description="Set the selected forex pair.", inputSchema={"type": "object", "properties": {"pair": {"type": "string"}}, "required": ["pair"]}),
        Tool(name="get_candles", description="Fetch OANDA candles for a forex pair/timeframe.", inputSchema=schema_pair_tf),
        Tool(name="analyze_pair", description="Analyze a forex pair and return BUY/SELL/HOLD context.", inputSchema=schema_pair_tf),
        Tool(name="recommend", description="Return an educational paper-trading recommendation.", inputSchema=schema_pair_tf),
        Tool(name="run_strategy", description="Run a catalog strategy by id/name for the pair/timeframe.", inputSchema={"type": "object", "properties": {"strategy_id": {"type": "string"}, "pair": {"type": "string"}, "timeframe": {"type": "string", "default": "H1"}}}),
        Tool(name="get_news", description="Fetch SearXNG forex news and simple sentiment for a pair.", inputSchema={"type": "object", "properties": {"pair": {"type": "string"}}}),
        Tool(name="draw_on_chart", description="Return JSON drawing commands for the browser TradingView chart.", inputSchema={"type": "object", "properties": {"shapes": {"type": "array"}}}),
        Tool(name="log_paper_trade", description="Append a local paper trade record.", inputSchema={"type": "object", "properties": {"trade": {"type": "object"}}, "required": ["trade"]}),
        Tool(name="get_trade_log", description="Return local paper trade records.", inputSchema={"type": "object", "properties": {}}),
        Tool(name="list_strategies", description="Return strategy catalog metadata.", inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    arguments = arguments or {}
    state = _load_state()
    if name == "get_selected_pair":
        return _text({"selected_pair": _pair(None)})
    if name == "set_selected_pair":
        state["selected_pair"] = _pair(arguments.get("pair"))
        _save_state(state)
        return _text({"ok": True, "selected_pair": state["selected_pair"]})
    if name in {"get_candles", "analyze_pair", "recommend", "run_strategy"}:
        pair = _pair(arguments.get("pair"))
        timeframe = arguments.get("timeframe") or "H1"
        candles = get_oanda_client().get_candles(pair, timeframe, count=300)
        if name == "get_candles":
            return _text({"pair": pair, "timeframe": timeframe, "candles": candles})
        return _text(recommend_from_candles(candles, strategy_id=arguments.get("strategy_id")))
    if name == "get_news":
        return _text(get_pair_news(_pair(arguments.get("pair"))))
    if name == "draw_on_chart":
        return _text({"draw_commands": arguments.get("shapes") or []})
    if name == "log_paper_trade":
        trade = dict(arguments.get("trade") or {})
        state.setdefault("trades", []).append(trade)
        _save_state(state)
        return _text({"ok": True, "trade": trade})
    if name == "get_trade_log":
        return _text({"trades": state.get("trades", [])})
    if name == "list_strategies":
        limit = int(arguments.get("limit") or 20)
        return _text({"strategies": load_catalog()[:limit]})
    return _text(f"Unknown tool: {name}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
