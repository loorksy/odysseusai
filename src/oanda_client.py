"""Minimal OANDA practice REST client used by the forex datafeed and APIs."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import requests


class OandaConfigError(RuntimeError):
    pass


@dataclass
class _CacheEntry:
    expires_at: float
    value: Any


class OandaClient:
    PRACTICE_URL = "https://api-fxpractice.oanda.com"
    LIVE_URL = "https://api-fxtrade.oanda.com"

    def __init__(self, token: Optional[str] = None, account_id: Optional[str] = None, env: Optional[str] = None):
        self.token = token or os.getenv("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        self.env = (env or os.getenv("OANDA_ENV", "practice")).lower()
        self.base_url = self.PRACTICE_URL if self.env != "live" else self.LIVE_URL
        self.timeout = float(os.getenv("OANDA_TIMEOUT", "15"))
        self._cache: Dict[str, _CacheEntry] = {}
        self._last_request_at = 0.0
        self._min_interval = float(os.getenv("OANDA_MIN_REQUEST_INTERVAL", "0.12"))

    @property
    def configured(self) -> bool:
        return bool(self.token and self.account_id)

    def _require_config(self) -> None:
        if not self.configured:
            raise OandaConfigError("OANDA_API_TOKEN and OANDA_ACCOUNT_ID must be configured")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept-Datetime-Format": "RFC3339"}

    def _get_cached(self, key: str):
        entry = self._cache.get(key)
        if entry and entry.expires_at > time.time():
            return entry.value
        self._cache.pop(key, None)
        return None

    def _set_cached(self, key: str, value: Any, ttl: float) -> Any:
        self._cache[key] = _CacheEntry(time.time() + ttl, value)
        return value

    def _request(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._require_config()
        elapsed = time.time() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        url = f"{self.base_url}{path}"
        response = requests.get(url, headers=self._headers(), params=params or {}, timeout=self.timeout)
        self._last_request_at = time.time()
        response.raise_for_status()
        return response.json()

    def list_instruments(self) -> list[dict[str, Any]]:
        key = "instruments"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        data = self._request(f"/v3/accounts/{self.account_id}/instruments")
        instruments = [i for i in data.get("instruments", []) if i.get("type") == "CURRENCY"]
        return self._set_cached(key, instruments, ttl=3600)

    def get_candles(self, instrument: str, granularity: str, count: Optional[int] = None, from_time: Optional[int] = None, to_time: Optional[int] = None) -> list[dict[str, Any]]:
        params: Dict[str, Any] = {"price": "M", "granularity": granularity}
        if count is not None:
            params["count"] = max(1, min(int(count), 5000))
        if from_time is not None:
            params["from"] = int(from_time)
        if to_time is not None:
            params["to"] = int(to_time)
        key = f"candles:{instrument}:{granularity}:{params}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        data = self._request(f"/v3/instruments/{instrument}/candles", params=params)
        return self._set_cached(key, data.get("candles", []), ttl=10)

    def get_pricing(self, instruments: Iterable[str]) -> list[dict[str, Any]]:
        names = sorted({i for i in instruments if i})
        if not names:
            return []
        key = f"pricing:{','.join(names)}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        data = self._request(f"/v3/accounts/{self.account_id}/pricing", params={"instruments": ",".join(names)})
        return self._set_cached(key, data.get("prices", []), ttl=2)


def get_oanda_client() -> OandaClient:
    return OandaClient()
