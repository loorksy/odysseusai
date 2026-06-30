"""
C109 — Retry Engine
Configurable retry logic with exponential backoff, jitter, per-exception
filtering, and async support. Usable as a decorator or direct call wrapper.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type

logger = logging.getLogger(__name__)


@dataclass
class RetryStats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    total_wait: float = 0.0
    last_error: Optional[str] = None


class RetryExhausted(Exception):
    """Raised when all retry attempts are exhausted."""
    def __init__(self, message: str, last_exception: Optional[Exception] = None):
        super().__init__(message)
        self.last_exception = last_exception


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple = field(default_factory=lambda: (Exception,))
    non_retryable_exceptions: tuple = field(default_factory=tuple)
    on_retry: Optional[Callable[[int, Exception, float], None]] = None  # (attempt, exc, wait)

    def delay_for(self, attempt: int) -> float:
        delay = min(self.base_delay * (self.backoff_factor ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay

    def is_retryable(self, exc: Exception) -> bool:
        if self.non_retryable_exceptions and isinstance(exc, self.non_retryable_exceptions):
            return False
        return isinstance(exc, self.retryable_exceptions)


class RetryEngine:
    """
    Flexible retry engine.

    Usage::

        policy = RetryPolicy(max_attempts=5, base_delay=0.5)
        engine = RetryEngine(policy)

        result = engine.run(my_fn, arg1, kwarg=val)
        result = await engine.arun(my_async_fn, arg1)

        @engine.decorator
        def flaky_call(): ...
    """

    def __init__(self, policy: Optional[RetryPolicy] = None):
        self.policy = policy or RetryPolicy()
        self.stats = RetryStats()

    def run(self, fn: Callable, *args, **kwargs) -> Any:
        policy = self.policy
        last_exc: Optional[Exception] = None
        for attempt in range(1, policy.max_attempts + 1):
            self.stats.attempts += 1
            try:
                result = fn(*args, **kwargs)
                self.stats.successes += 1
                return result
            except Exception as exc:
                last_exc = exc
                self.stats.failures += 1
                self.stats.last_error = str(exc)
                if not policy.is_retryable(exc):
                    logger.debug("Non-retryable exception: %s", exc)
                    raise
                if attempt == policy.max_attempts:
                    break
                wait = policy.delay_for(attempt)
                self.stats.total_wait += wait
                logger.warning(
                    "Retry %d/%d after %.2fs (error: %s)",
                    attempt, policy.max_attempts, wait, exc,
                )
                if policy.on_retry:
                    policy.on_retry(attempt, exc, wait)
                time.sleep(wait)
        raise RetryExhausted(
            f"All {policy.max_attempts} attempts failed",
            last_exception=last_exc,
        ) from last_exc

    async def arun(self, fn: Callable, *args, **kwargs) -> Any:
        policy = self.policy
        last_exc: Optional[Exception] = None
        for attempt in range(1, policy.max_attempts + 1):
            self.stats.attempts += 1
            try:
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(fn, *args, **kwargs)
                self.stats.successes += 1
                return result
            except Exception as exc:
                last_exc = exc
                self.stats.failures += 1
                self.stats.last_error = str(exc)
                if not policy.is_retryable(exc):
                    raise
                if attempt == policy.max_attempts:
                    break
                wait = policy.delay_for(attempt)
                self.stats.total_wait += wait
                logger.warning("Async retry %d/%d after %.2fs: %s", attempt, policy.max_attempts, wait, exc)
                if policy.on_retry:
                    policy.on_retry(attempt, exc, wait)
                await asyncio.sleep(wait)
        raise RetryExhausted(
            f"All {policy.max_attempts} async attempts failed",
            last_exception=last_exc,
        ) from last_exc

    def decorator(self, fn: Callable) -> Callable:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                return await self.arun(fn, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                return self.run(fn, *args, **kwargs)
            return sync_wrapper

    def reset_stats(self) -> None:
        self.stats = RetryStats()


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable: tuple = (Exception,),
    non_retryable: tuple = (),
):
    """Convenience decorator factory."""
    policy = RetryPolicy(
        max_attempts=max_attempts,
        base_delay=base_delay,
        backoff_factor=backoff_factor,
        jitter=jitter,
        retryable_exceptions=retryable,
        non_retryable_exceptions=non_retryable,
    )
    engine = RetryEngine(policy)
    return engine.decorator
