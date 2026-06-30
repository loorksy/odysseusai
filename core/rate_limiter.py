"""
C106 — Rate Limiter
Token-bucket and sliding-window rate limiters for controlling
LLM API call frequency, tool use, and agent throughput.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Token Bucket                                                         #
# ------------------------------------------------------------------ #

class TokenBucket:
    """
    Classic token-bucket algorithm.
    Allows bursting up to `capacity` tokens, refilling at `rate` per second.
    """

    def __init__(self, rate: float, capacity: float):
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_and_consume(self, tokens: float = 1.0, timeout: Optional[float] = None) -> bool:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self.consume(tokens):
                return True
            if deadline and time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    async def async_consume(self, tokens: float = 1.0, timeout: Optional[float] = None) -> bool:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self.consume(tokens):
                return True
            if deadline and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.01)

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


# ------------------------------------------------------------------ #
#  Sliding Window                                                       #
# ------------------------------------------------------------------ #

class SlidingWindowLimiter:
    """
    Sliding-window rate limiter.
    Allows at most `max_calls` requests within any `window_seconds` period.
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.pop(0)

    def is_allowed(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(now)
                return True
            return False

    def wait_until_allowed(self, timeout: Optional[float] = None) -> bool:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self.is_allowed():
                return True
            if deadline and time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    async def async_is_allowed(self) -> bool:
        return self.is_allowed()

    @property
    def remaining(self) -> int:
        with self._lock:
            self._purge(time.monotonic())
            return max(0, self.max_calls - len(self._timestamps))

    @property
    def retry_after(self) -> float:
        """Seconds until at least one slot opens."""
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            if len(self._timestamps) < self.max_calls:
                return 0.0
            oldest = self._timestamps[0]
            return max(0.0, oldest + self.window_seconds - now)


# ------------------------------------------------------------------ #
#  Composite limiter                                                    #
# ------------------------------------------------------------------ #

class RateLimiter:
    """
    Combines a token bucket and a sliding window.
    A call is allowed only if BOTH permit it.

    Usage::

        limiter = RateLimiter(rpm=60, burst=10)
        if limiter.is_allowed():
            call_api()
        else:
            time.sleep(limiter.retry_after)
    """

    def __init__(
        self,
        rpm: int = 60,
        burst: Optional[int] = None,
        window_seconds: float = 60.0,
    ):
        rate = rpm / window_seconds
        capacity = burst or max(1, rpm // 6)
        self._bucket = TokenBucket(rate=rate, capacity=capacity)
        self._window = SlidingWindowLimiter(max_calls=rpm, window_seconds=window_seconds)

    def is_allowed(self) -> bool:
        return self._window.is_allowed() and self._bucket.consume()

    def wait_until_allowed(self, timeout: Optional[float] = None) -> bool:
        return self._window.wait_until_allowed(timeout=timeout) and self._bucket.wait_and_consume(timeout=timeout)

    async def async_is_allowed(self) -> bool:
        return self._window.is_allowed() and await self._bucket.async_consume()

    @property
    def retry_after(self) -> float:
        return self._window.retry_after

    @property
    def remaining(self) -> int:
        return min(self._window.remaining, int(self._bucket.available))
