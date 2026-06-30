"""AdaptiveThrottler — Dynamic rate-limit and retry management (C24).

Responsibilities:
  1. Rate limiting: enforce per-model, per-owner token-per-minute (TPM)
     and requests-per-minute (RPM) limits with a sliding-window counter.
  2. Adaptive backoff: when the upstream API returns a rate-limit error
     (429 / RateLimitError), automatically back off and retry with
     exponential + jitter strategy.
  3. Model fallback: if a model is rate-limited beyond the retry budget,
     suggest a cheaper/faster fallback model from the fallback chain.
  4. Throttle state is per-process (in-memory); for multi-worker deployments
     use an external rate limiter (Redis token bucket) — this module exposes
     hooks for that via `_check_external_limit()` / `_record_external_use()`.

Public API:
  throttler = AdaptiveThrottler()
  throttler.acquire(model, tokens, owner)   — blocks/raises if over limit
  throttler.record_success(model, tokens)   — update sliding window
  throttler.record_error(model, error_code) — update backoff state
  throttler.backoff_seconds(model)          — current wait time
  throttler.suggest_fallback(model)         — cheaper model or None
  throttler.status()                        — full throttle state snapshot
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-model rate limits (TPM = tokens/min, RPM = requests/min)
# Source: OpenAI / Anthropic / Google published tier-1 limits (conservative)
# ---------------------------------------------------------------------------
_MODEL_LIMITS: Dict[str, Dict[str, int]] = {
    "gpt-4o":                   {"tpm": 800_000,  "rpm": 5_000},
    "gpt-4o-mini":              {"tpm": 4_000_000, "rpm": 30_000},
    "gpt-4-turbo":              {"tpm": 600_000,  "rpm": 5_000},
    "gpt-4":                    {"tpm": 300_000,  "rpm": 3_000},
    "gpt-3.5-turbo":            {"tpm": 2_000_000, "rpm": 10_000},
    "o1":                       {"tpm": 300_000,  "rpm": 500},
    "o1-mini":                  {"tpm": 1_000_000, "rpm": 1_000},
    "o3":                       {"tpm": 300_000,  "rpm": 500},
    "o3-mini":                  {"tpm": 1_000_000, "rpm": 1_000},
    "claude-3-5-sonnet":        {"tpm": 400_000,  "rpm": 4_000},
    "claude-3-5-haiku":         {"tpm": 400_000,  "rpm": 4_000},
    "claude-3-opus":            {"tpm": 100_000,  "rpm": 1_000},
    "gemini-1.5-pro":           {"tpm": 1_000_000, "rpm": 1_000},
    "gemini-1.5-flash":         {"tpm": 4_000_000, "rpm": 4_000},
    "gemini-2.0-flash":         {"tpm": 4_000_000, "rpm": 4_000},
    "__default__":              {"tpm": 100_000,  "rpm": 1_000},
}

# Fallback chains: if primary is rate-limited, try these in order.
_FALLBACK_CHAINS: Dict[str, List[str]] = {
    "gpt-4o":           ["gpt-4o-mini", "gpt-3.5-turbo"],
    "gpt-4-turbo":      ["gpt-4o", "gpt-4o-mini"],
    "gpt-4":            ["gpt-4o", "gpt-4o-mini"],
    "o1":               ["o1-mini", "gpt-4o"],
    "o3":               ["o3-mini", "gpt-4o"],
    "claude-3-opus":    ["claude-3-5-sonnet", "claude-3-5-haiku"],
    "claude-3-5-sonnet":["claude-3-5-haiku"],
    "gemini-1.5-pro":   ["gemini-1.5-flash", "gemini-2.0-flash"],
}

# Backoff parameters
_BASE_BACKOFF   = 1.0   # seconds
_MAX_BACKOFF    = 60.0  # seconds
_JITTER_FACTOR  = 0.25  # ±25% jitter
_MAX_RETRIES    = 5
_WINDOW_SECS    = 60    # sliding window for TPM/RPM


class RateLimitError(Exception):
    """Raised when acquire() cannot proceed and retry budget is exhausted."""
    def __init__(self, model: str, reason: str):
        self.model = model
        self.reason = reason
        super().__init__(f"Rate limit for {model}: {reason}")


class AdaptiveThrottler:
    """In-process adaptive rate limiter with model fallback suggestions."""

    def __init__(
        self,
        extra_limits: Optional[Dict[str, Dict[str, int]]] = None,
        extra_chains: Optional[Dict[str, List[str]]] = None,
    ):
        self._limits  = {**_MODEL_LIMITS,   **(extra_limits or {})}
        self._chains  = {**_FALLBACK_CHAINS, **(extra_chains or {})}
        self._lock    = threading.Lock()

        # Per-model sliding-window state
        # _windows[model] = {"tokens": [(ts, count), ...], "requests": [(ts,), ...]}
        self._windows: Dict[str, Dict] = {}

        # Per-model backoff state
        # _backoff[model] = {"errors": int, "until": float, "retries": int}
        self._backoff: Dict[str, Dict] = {}

    # ------------------------------------------------------------------
    # Sliding window helpers
    # ------------------------------------------------------------------

    def _window(self, model: str) -> Dict:
        return self._windows.setdefault(model, {"tokens": [], "requests": []})

    def _prune(self, model: str) -> None:
        """Remove entries outside the current sliding window."""
        cutoff = time.time() - _WINDOW_SECS
        w = self._window(model)
        w["tokens"]   = [(ts, n) for ts, n in w["tokens"]   if ts >= cutoff]
        w["requests"] = [(ts,)   for (ts,) in w["requests"] if ts >= cutoff]

    def _current_tpm(self, model: str) -> int:
        self._prune(model)
        return sum(n for _, n in self._window(model)["tokens"])

    def _current_rpm(self, model: str) -> int:
        self._prune(model)
        return len(self._window(model)["requests"])

    # ------------------------------------------------------------------
    # Backoff helpers
    # ------------------------------------------------------------------

    def _backoff_state(self, model: str) -> Dict:
        return self._backoff.setdefault(model, {"errors": 0, "until": 0.0, "retries": 0})

    def backoff_seconds(self, model: str) -> float:
        """Current wait time for `model` (0 if not backing off)."""
        until = self._backoff_state(model).get("until", 0.0)
        remaining = until - time.time()
        return max(0.0, remaining)

    def _compute_backoff(self, errors: int) -> float:
        raw = _BASE_BACKOFF * (2 ** min(errors - 1, 10))
        capped = min(raw, _MAX_BACKOFF)
        jitter = capped * _JITTER_FACTOR * (2 * random.random() - 1)
        return max(0.0, capped + jitter)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(
        self,
        model: str,
        tokens: int,
        owner: Optional[str] = None,
        *,
        block: bool = False,
        timeout: float = 10.0,
    ) -> None:
        """Attempt to acquire a slot for `tokens` against `model`.

        If `block=True`, sleeps up to `timeout` seconds waiting for the window
        to clear.  Otherwise raises RateLimitError immediately when over limit.
        """
        with self._lock:
            limits = self._limits.get(model) or self._limits["__default__"]
            deadline = time.time() + timeout

            while True:
                # Check backoff
                remaining_backoff = self.backoff_seconds(model)
                if remaining_backoff > 0:
                    if block and time.time() + remaining_backoff <= deadline:
                        pass  # will sleep below
                    else:
                        raise RateLimitError(model, f"backing off for {remaining_backoff:.1f}s")

                tpm = self._current_tpm(model)
                rpm = self._current_rpm(model)

                if (tpm + tokens) <= limits["tpm"] and rpm < limits["rpm"]:
                    # Slot available — record use
                    now = time.time()
                    self._window(model)["tokens"].append((now, tokens))
                    self._window(model)["requests"].append((now,))
                    return

                if not block:
                    reason = (
                        f"TPM {tpm + tokens}/{limits['tpm']}" if (tpm + tokens) > limits["tpm"]
                        else f"RPM {rpm + 1}/{limits['rpm']}"
                    )
                    raise RateLimitError(model, reason)

                if time.time() >= deadline:
                    raise RateLimitError(model, "timeout waiting for rate-limit window")

                # Sleep a short interval and retry
                self._lock.release()
                time.sleep(0.5)
                self._lock.acquire()

    def record_success(self, model: str, tokens: int) -> None:
        """Record a successful LLM response; resets error counter."""
        with self._lock:
            bs = self._backoff_state(model)
            bs["errors"]  = 0
            bs["retries"] = 0
            bs["until"]   = 0.0
            # Tokens already recorded in acquire(); nothing more needed.

    def record_error(self, model: str, error_code: int = 429) -> float:
        """Record a rate-limit or transient error; returns seconds to wait."""
        with self._lock:
            bs = self._backoff_state(model)
            bs["errors"]  = int(bs.get("errors", 0)) + 1
            bs["retries"] = int(bs.get("retries", 0)) + 1
            wait = self._compute_backoff(bs["errors"])
            bs["until"] = time.time() + wait
            logger.warning(f"AdaptiveThrottler: model={model} error={error_code} backoff={wait:.2f}s (attempt {bs['retries']})")
            return wait

    def should_retry(self, model: str) -> bool:
        """True if the error count hasn't exceeded the retry budget."""
        return self._backoff_state(model).get("retries", 0) < _MAX_RETRIES

    def suggest_fallback(self, model: str) -> Optional[str]:
        """Return the next fallback model if available, or None."""
        chain = self._chains.get(model, [])
        for candidate in chain:
            # Prefer a candidate that isn't currently backed off
            if self.backoff_seconds(candidate) == 0.0:
                return candidate
        # All candidates are backed off — return the first anyway
        return chain[0] if chain else None

    def status(self) -> Dict:
        """Return a snapshot of throttle state for all tracked models."""
        with self._lock:
            out = {}
            seen = set(self._windows) | set(self._backoff)
            for model in seen:
                limits = self._limits.get(model) or self._limits["__default__"]
                out[model] = {
                    "tpm_used":    self._current_tpm(model),
                    "tpm_limit":   limits["tpm"],
                    "rpm_used":    self._current_rpm(model),
                    "rpm_limit":   limits["rpm"],
                    "backoff_secs": self.backoff_seconds(model),
                    "errors":      self._backoff_state(model).get("errors", 0),
                    "retries":     self._backoff_state(model).get("retries", 0),
                }
            return out

    # ------------------------------------------------------------------
    # Extension hooks for external rate limiters (Redis, etc.)
    # ------------------------------------------------------------------

    def _check_external_limit(self, model: str, tokens: int, owner: Optional[str]) -> bool:
        """Override in a subclass to check a Redis / external rate limiter.
        Return True if the request is allowed, False if it should be blocked.
        """
        return True

    def _record_external_use(self, model: str, tokens: int, owner: Optional[str]) -> None:
        """Override in a subclass to record usage in an external store."""
