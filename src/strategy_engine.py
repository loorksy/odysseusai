"""Forex strategy catalog loader and recommendation engine."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src.trading_analysis import summarize

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "strategies_catalog.json"


def load_catalog() -> list[dict[str, Any]]:
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def get_strategy(strategy_id_or_name: str) -> Optional[dict[str, Any]]:
    wanted = str(strategy_id_or_name).lower()
    for strategy in load_catalog():
        if wanted in {str(strategy.get("id", "")).lower(), str(strategy.get("name_en", "")).lower(), str(strategy.get("name_ar", "")).lower()}:
            return strategy
    return None


def recommend_from_candles(candles: list[dict[str, Any]], strategy_id: str | None = None) -> dict[str, Any]:
    analysis = summarize(candles)
    close = float(analysis.get("close") or 0)
    atr = float(analysis.get("atr") or (close * 0.001 if close else 0))
    signal = analysis["signal"]
    if signal == "BUY":
        sl, tp = close - (1.5 * atr), close + (2.5 * atr)
    elif signal == "SELL":
        sl, tp = close + (1.5 * atr), close - (2.5 * atr)
    else:
        sl = tp = None
    strategy = get_strategy(strategy_id) if strategy_id else None
    return {**analysis, "entry": round(close, 6) if close and signal != "HOLD" else None, "stop_loss": round(sl, 6) if sl else None, "take_profit": round(tp, 6) if tp else None, "strategy": strategy or "aggregate_default", "disclaimer": "Educational paper-trading recommendation only; no live order execution."}
