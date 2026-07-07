"""Technical-analysis helpers for forex recommendations.

The project declares pandas for richer future analysis, but this module keeps a
small dependency-light implementation so forex APIs can still run in minimal
installations.
"""
from __future__ import annotations

from typing import Any, Iterable


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append((value * alpha) + (out[-1] * (1 - alpha)))
    return out


def candles_to_rows(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for candle in candles:
        mid = candle.get("mid") or candle
        if not mid:
            continue
        rows.append({"time": candle.get("time"), "open": float(mid.get("o", mid.get("open", 0))), "high": float(mid.get("h", mid.get("high", 0))), "low": float(mid.get("l", mid.get("low", 0))), "close": float(mid.get("c", mid.get("close", 0))), "volume": int(candle.get("volume", 0))})
    return rows


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    window = deltas[-period:]
    gains = [max(d, 0) for d in window]
    losses = [abs(min(d, 0)) for d in window]
    avg_gain = _mean(gains) or 0
    avg_loss = _mean(losses) or 0
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float:
    if not rows:
        return 0.0
    true_ranges = []
    for idx, row in enumerate(rows):
        prev_close = rows[idx - 1]["close"] if idx else row["close"]
        true_ranges.append(max(row["high"] - row["low"], abs(row["high"] - prev_close), abs(row["low"] - prev_close)))
    return _mean(true_ranges[-period:]) or 0.0


def summarize(candles: list[dict[str, Any]]) -> dict[str, Any]:
    rows = candles_to_rows(candles)
    if not rows:
        return {"signal": "HOLD", "confidence": 0, "reason": "No candle data available."}
    closes = [r["close"] for r in rows]
    close = closes[-1]
    sma20 = _mean(closes[-20:]) or close
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [(a - b) for a, b in zip(ema12[-len(ema26):], ema26)] if ema12 and ema26 else [0.0]
    macd_signal = _ema(macd_line, 9)[-1] if macd_line else 0.0
    macd = macd_line[-1] if macd_line else 0.0
    rsi = _rsi(closes)
    atr = _atr(rows) or (close * 0.001)
    bullish = close > sma20 and macd > macd_signal
    bearish = close < sma20 and macd < macd_signal
    if bullish and rsi < 72:
        signal, confidence = "BUY", 68
    elif bearish and rsi > 28:
        signal, confidence = "SELL", 68
    else:
        signal, confidence = "HOLD", 45
    recent = rows[-50:]
    return {"signal": signal, "confidence": confidence, "close": close, "rsi": round(rsi, 2), "atr": round(atr, 6), "support": round(min(r["low"] for r in recent), 6), "resistance": round(max(r["high"] for r in recent), 6), "reason": f"Trend from SMA20/MACD with RSI={rsi:.1f}."}
