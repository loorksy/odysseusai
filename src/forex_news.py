"""Forex news lookup through the configured SearXNG endpoint."""
from __future__ import annotations

import os
from typing import Any

import requests

POSITIVE = {"rise", "rises", "gain", "gains", "bullish", "strong", "beats", "hawkish", "surge", "rally"}
NEGATIVE = {"fall", "falls", "loss", "losses", "bearish", "weak", "misses", "dovish", "drop", "slump"}


def _query_for_pair(pair: str) -> str:
    return f"{pair.replace('_', ' ')} forex news"


def _sentiment(text: str) -> str:
    words = {w.strip(".,:;!?()[]{}\"'").lower() for w in text.split()}
    pos = len(words & POSITIVE)
    neg = len(words & NEGATIVE)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def get_pair_news(pair: str, limit: int = 8) -> dict[str, Any]:
    base = os.getenv("SEARXNG_URL") or os.getenv("SEARX_URL") or os.getenv("SEARXNG_BASE_URL")
    query = _query_for_pair(pair)
    if not base:
        return {"pair": pair, "query": query, "sentiment": "neutral", "items": [], "summary": "SearXNG endpoint is not configured."}
    url = base.rstrip("/") + "/search"
    response = requests.get(url, params={"q": query, "format": "json", "language": "en"}, timeout=float(os.getenv("SEARXNG_TIMEOUT", "10")))
    response.raise_for_status()
    data = response.json()
    items = []
    combined = []
    for result in data.get("results", [])[:limit]:
        title = result.get("title") or "Untitled"
        content = result.get("content") or result.get("snippet") or ""
        items.append({"title": title, "url": result.get("url"), "content": content, "sentiment": _sentiment(f"{title} {content}")})
        combined.append(f"{title} {content}")
    overall = _sentiment(" ".join(combined))
    return {"pair": pair, "query": query, "sentiment": overall, "items": items, "summary": f"Found {len(items)} news items for {pair}; simple sentiment is {overall}."}
