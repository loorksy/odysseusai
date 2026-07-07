"""Authenticated forex-analysis and paper-trading APIs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.auth_helpers import effective_user, require_user
from src.oanda_client import OandaConfigError, get_oanda_client
from src.strategy_engine import load_catalog, recommend_from_candles
from src.forex_news import get_pair_news
from routes.datafeed_routes import RESOLUTION_TO_OANDA

STORE = Path("data/paper_trades.json")


def _load_store() -> dict[str, Any]:
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_store(data: dict[str, Any]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(STORE)


def _user_key(request: Request) -> str:
    require_user(request)
    return effective_user(request) or "default"


def setup_trading_routes() -> APIRouter:
    router = APIRouter(prefix="/api/forex", tags=["forex"])

    @router.get("/instruments")
    async def instruments(request: Request):
        _user_key(request)
        try:
            return {"instruments": get_oanda_client().list_instruments()}
        except OandaConfigError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get("/strategies")
    async def strategies(request: Request, q: str = "", limit: int = Query(50, ge=1, le=1000)):
        _user_key(request)
        rows = load_catalog()
        if q:
            needle = q.lower()
            rows = [s for s in rows if needle in json.dumps(s, ensure_ascii=False).lower()]
        return {"count": len(rows), "strategies": rows[:limit]}

    @router.get("/analyze")
    async def analyze(request: Request, pair: str = "EUR_USD", timeframe: str = "H1"):
        _user_key(request)
        granularity = RESOLUTION_TO_OANDA.get(timeframe, timeframe)
        try:
            candles = get_oanda_client().get_candles(pair.upper().replace("/", "_"), granularity, count=300)
        except OandaConfigError as exc:
            raise HTTPException(503, str(exc)) from exc
        return recommend_from_candles(candles)

    @router.get("/recommend")
    async def recommend(request: Request, pair: str = "EUR_USD", timeframe: str = "H1", strategy_id: str | None = None):
        _user_key(request)
        granularity = RESOLUTION_TO_OANDA.get(timeframe, timeframe)
        try:
            candles = get_oanda_client().get_candles(pair.upper().replace("/", "_"), granularity, count=300)
        except OandaConfigError as exc:
            raise HTTPException(503, str(exc)) from exc
        return recommend_from_candles(candles, strategy_id=strategy_id)

    @router.get("/news")
    async def news(request: Request, pair: str = "EUR_USD"):
        _user_key(request)
        try:
            return get_pair_news(pair.upper().replace("/", "_"))
        except Exception as exc:
            raise HTTPException(503, f"News lookup failed: {exc}") from exc

    @router.get("/trades")
    async def get_trades(request: Request):
        user = _user_key(request)
        return {"trades": _load_store().get(user, [])}

    @router.get("/events")
    async def get_events(request: Request):
        user = _user_key(request)
        return {"events": _load_store().get("_events", {}).get(user, [])}

    @router.post("/events")
    async def log_event(request: Request, body: dict[str, Any]):
        user = _user_key(request)
        data = _load_store()
        event = {**body, "id": f"ev-{int(datetime.now(timezone.utc).timestamp()*1000)}", "user_id": user, "created_at": datetime.now(timezone.utc).isoformat()}
        data.setdefault("_events", {}).setdefault(user, []).append(event)
        _save_store(data)
        return event

    @router.post("/trades")
    async def log_trade(request: Request, body: dict[str, Any]):
        user = _user_key(request)
        data = _load_store()
        trade = {**body, "id": f"pt-{int(datetime.now(timezone.utc).timestamp()*1000)}", "user_id": user, "opened_at": datetime.now(timezone.utc).isoformat(), "status": body.get("status", "open")}
        data.setdefault(user, []).append(trade)
        _save_store(data)
        return trade

    @router.get("/selected-pair")
    async def get_selected_pair(request: Request):
        user = _user_key(request)
        settings = _load_store().get("_settings", {})
        return {"selected_pair": settings.get(user, {}).get("selected_pair", "EUR_USD")}

    @router.post("/selected-pair")
    async def selected_pair(request: Request, body: dict[str, Any]):
        user = _user_key(request)
        data = _load_store()
        settings = data.setdefault("_settings", {})
        settings[user] = {**settings.get(user, {}), "selected_pair": str(body.get("pair", "EUR_USD")).upper().replace("/", "_")}
        _save_store(data)
        return {"ok": True, "selected_pair": settings[user]["selected_pair"]}

    return router
