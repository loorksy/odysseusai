"""TradingView UDF-compatible endpoints backed by OANDA candles."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from src.oanda_client import OandaConfigError, get_oanda_client

RESOLUTION_TO_OANDA = {
    "1": "M1", "5": "M5", "15": "M15", "30": "M30",
    "60": "H1", "120": "H2", "240": "H4", "1D": "D", "D": "D", "1W": "W", "W": "W",
}
SUPPORTED_RESOLUTIONS = ["1", "5", "15", "30", "60", "120", "240", "1D", "1W"]


def _tv_symbol(symbol: str) -> str:
    return (symbol or "EUR_USD").upper().replace("/", "_").replace(":", "_")


def _parse_oanda_time(value: str) -> int:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return int(datetime.fromisoformat(value).timestamp())


def _symbol_info(name: str, display: str | None = None) -> dict[str, Any]:
    symbol = _tv_symbol(name)
    return {
        "name": symbol,
        "ticker": symbol,
        "description": display or symbol.replace("_", "/"),
        "type": "forex",
        "session": "24x7",
        "exchange": "OANDA",
        "listed_exchange": "OANDA",
        "timezone": "Etc/UTC",
        "minmov": 1,
        "pricescale": 100000,
        "has_intraday": True,
        "has_daily": True,
        "has_weekly_and_monthly": True,
        "supported_resolutions": SUPPORTED_RESOLUTIONS,
        "volume_precision": 0,
        "data_status": "streaming",
    }


def setup_datafeed_routes() -> APIRouter:
    router = APIRouter(prefix="/api/udf", tags=["forex-datafeed"])

    @router.get("/config")
    async def config():
        return {"supports_search": True, "supports_group_request": False, "supports_marks": False, "supports_timescale_marks": False, "supports_time": True, "supported_resolutions": SUPPORTED_RESOLUTIONS, "exchanges": [{"value": "OANDA", "name": "OANDA", "desc": "OANDA Forex"}], "symbols_types": [{"name": "forex", "value": "forex"}]}

    @router.get("/time")
    async def server_time():
        return int(time.time())

    @router.get("/symbols")
    async def symbols(symbol: str = Query(...)):
        wanted = _tv_symbol(symbol)
        try:
            for inst in get_oanda_client().list_instruments():
                if inst.get("name") == wanted:
                    return _symbol_info(wanted, inst.get("displayName"))
        except OandaConfigError:
            pass
        return _symbol_info(wanted)

    @router.get("/search")
    async def search(query: str = "", limit: int = 30):
        q = _tv_symbol(query)
        try:
            instruments = get_oanda_client().list_instruments()
        except OandaConfigError:
            instruments = [{"name": s, "displayName": s.replace("_", "/")} for s in ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD"]]
        results = []
        for inst in instruments:
            name = inst.get("name", "")
            display = inst.get("displayName") or name.replace("_", "/")
            if not q or q in name or query.upper() in display.upper():
                results.append({"symbol": name, "full_name": f"OANDA:{name}", "description": display, "exchange": "OANDA", "ticker": name, "type": "forex"})
            if len(results) >= limit:
                break
        return results

    @router.get("/history")
    async def history(symbol: str, resolution: str, from_: int = Query(..., alias="from"), to: int = Query(...)):
        granularity = RESOLUTION_TO_OANDA.get(resolution, RESOLUTION_TO_OANDA.get(resolution.upper()))
        if not granularity:
            return {"s": "error", "errmsg": f"Unsupported resolution: {resolution}"}
        candles = get_oanda_client().get_candles(_tv_symbol(symbol), granularity, from_time=from_, to_time=to)
        rows = [c for c in candles if c.get("complete", True) and c.get("mid")]
        if not rows:
            return {"s": "no_data", "nextTime": from_}
        return {"s": "ok", "t": [_parse_oanda_time(c["time"]) for c in rows], "o": [float(c["mid"]["o"]) for c in rows], "h": [float(c["mid"]["h"]) for c in rows], "l": [float(c["mid"]["l"]) for c in rows], "c": [float(c["mid"]["c"]) for c in rows], "v": [int(c.get("volume", 0)) for c in rows]}

    return router
