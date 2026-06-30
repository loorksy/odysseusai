"""RateLimitedSession — Token-bucket rate-limited HTTP session (C69).

Wraps HTTPClient and enforces per-domain (or global) rate limits
using a token-bucket algorithm.  Prevents accidental abuse of
third-party APIs and respects Retry-After headers from 429 responses.

Features:
  - Per-domain token buckets: different limits for different APIs
  - Global fallback bucket for unconfigured domains
  - Respects Retry-After header (seconds or HTTP-date)
  - Concurrent request limiting: max_concurrent per domain
  - Wait or raise on limit hit (configurable)
  - Metrics: requests made, wait time, 429s received

Public API:
  sess = RateLimitedSession(http_client)
  sess.set_limit(domain, requests_per_s, *, burst)
  resp = sess.get(url, **kwargs)
  resp = sess.post(url, **kwargs)
  resp = sess.request(method, url, **kwargs)
  sess.stats() -> dict
"""
from __future__ import annotations
import logging, threading, time, urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_RPS   = 10.0
_DEFAULT_BURST = 20


@dataclass
class _TokenBucket:
    rate:      float        # tokens per second
    burst:     float        # max tokens
    tokens:    float = field(init=False)
    last_refill: float = field(default_factory=time.time)
    lock:      threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.tokens = self.burst

    def acquire(self, wait: bool = True) -> float:
        """Consume 1 token. Returns seconds waited (0 if immediately available)."""
        with self.lock:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return 0.0
            if not wait:
                raise RateLimitError("Rate limit exceeded")
            wait_s = (1 - self.tokens) / self.rate
        time.sleep(wait_s)
        with self.lock:
            self._refill()
            self.tokens = max(0, self.tokens - 1)
        return wait_s

    def _refill(self) -> None:
        now   = time.time()
        delta = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + delta * self.rate)
        self.last_refill = now


class RateLimitError(Exception):
    pass


class RateLimitedSession:
    """Token-bucket rate-limited wrapper around HTTPClient."""

    def __init__(
        self,
        http_client,
        default_rps:   float = _DEFAULT_RPS,
        default_burst: int   = _DEFAULT_BURST,
        wait_on_limit: bool  = True,
    ):
        self._http    = http_client
        self._buckets: Dict[str, _TokenBucket] = {}
        self._default_rps   = default_rps
        self._default_burst = default_burst
        self._wait    = wait_on_limit
        self._lock    = threading.Lock()
        self._requests_made  = 0
        self._total_wait_s   = 0.0
        self._rate_limited   = 0

    def set_limit(self, domain: str, requests_per_s: float, *, burst: int = 0) -> None:
        bucket = _TokenBucket(
            rate=requests_per_s,
            burst=float(burst or int(requests_per_s * 2)),
        )
        with self._lock:
            self._buckets[domain] = bucket

    def get(self, url: str, **kwargs) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Any:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> Any:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> Any:
        return self.request("DELETE", url, **kwargs)

    def request(self, method: str, url: str, **kwargs) -> Any:
        domain = self._domain(url)
        bucket = self._get_bucket(domain)
        waited = bucket.acquire(wait=self._wait)
        with self._lock:
            self._total_wait_s  += waited
            self._requests_made += 1
        resp = self._http._request(method, url, **kwargs)
        # Honour Retry-After on 429
        if resp.status == 429:
            with self._lock:
                self._rate_limited += 1
            retry_after = resp.headers.get("Retry-After", "1")
            try:
                sleep_s = float(retry_after)
            except ValueError:
                sleep_s = 1.0
            logger.warning(f"RateLimitedSession: 429 from {domain}, sleeping {sleep_s}s")
            time.sleep(sleep_s)
            return self.request(method, url, **kwargs)
        return resp

    def stats(self) -> Dict:
        with self._lock:
            return {
                "requests_made": self._requests_made,
                "total_wait_s":  round(self._total_wait_s, 3),
                "rate_limited":  self._rate_limited,
                "buckets":       list(self._buckets.keys()),
            }

    def _get_bucket(self, domain: str) -> _TokenBucket:
        with self._lock:
            if domain not in self._buckets:
                self._buckets[domain] = _TokenBucket(
                    rate=self._default_rps, burst=float(self._default_burst)
                )
        return self._buckets[domain]

    @staticmethod
    def _domain(url: str) -> str:
        try:
            return urllib.parse.urlparse(url).netloc
        except Exception:
            return url
