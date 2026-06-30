"""
C96 · WorkflowMiddleware
=======================
Composable middleware chain for WorkflowAPI — zero external dependencies.

Each middleware is a callable with signature::

    def mw(handler: HandlerFn, request: Request) -> Response: ...

The chain is built once and called for every incoming HTTP request before
the route dispatcher runs.  Middlewares are executed in registration order;
the innermost callable is the actual route handler.

Built-in middlewares
--------------------
AuthMiddleware
    API-key authentication via the ``X-API-Key`` header.
    Keys are checked against a set of allowed strings.
    Returns 401 if the header is missing or the key is unknown.
    Paths in ``skip_paths`` (e.g. ``/health``) bypass auth entirely.

RateLimitMiddleware
    Token-bucket rate limiter keyed by remote IP address.
    Configurable ``requests_per_minute`` and optional ``burst`` allowance.
    Returns 429 Too Many Requests with a ``Retry-After`` header when the
    bucket is empty.  Buckets are reaped after ``ttl_seconds`` of inactivity
    to prevent unbounded memory growth.

CORSMiddleware
    Adds ``Access-Control-*`` response headers and handles preflight
    ``OPTIONS`` requests (returns 204 No Content).
    ``allowed_origins`` may contain exact origins or ``"*"``.

LoggingMiddleware
    Structured request / response logging via the standard ``logging`` module.
    Logs method, path, status code, and elapsed time in milliseconds.
    Sensitive headers (``Authorization``, ``X-API-Key``) are redacted.

Usage
-----
    from core.workflow_middleware import (
        MiddlewareChain,
        AuthMiddleware,
        RateLimitMiddleware,
        CORSMiddleware,
        LoggingMiddleware,
    )
    from core.workflow_api import WorkflowAPI

    chain = (
        MiddlewareChain()
        .use(LoggingMiddleware())
        .use(CORSMiddleware(allowed_origins=["https://app.example.com"]))
        .use(RateLimitMiddleware(requests_per_minute=60, burst=10))
        .use(AuthMiddleware(api_keys={"secret-key-1", "secret-key-2"},
                            skip_paths={"/health"}))
    )

    api = WorkflowAPI(engine, store, scheduler, middleware=chain)
    api.start()

Standing up WorkflowAPI with middleware
---------------------------------------
WorkflowAPI accepts an optional ``middleware: MiddlewareChain`` kwarg
(added in C96).  When present, every request passes through the chain
before reaching the router.

Custom middleware
-----------------
Implement the ``Middleware`` protocol::

    class MyMiddleware:
        def __call__(self, handler: HandlerFn, request: Request) -> Response:
            # pre-processing …
            response = handler(request)
            # post-processing …
            return response

    chain.use(MyMiddleware())
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / Response value objects
# (thin wrappers; the actual HTTP I/O stays in workflow_api._Handler)
# ---------------------------------------------------------------------------

@dataclass
class Request:
    method:  str
    path:    str
    headers: Dict[str, str]
    body:    bytes
    remote:  str  # client IP
    query:   Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class Response:
    status:  int
    headers: Dict[str, str] = field(default_factory=dict)
    body:    bytes = b""

    @classmethod
    def json(cls, status: int, data: Any) -> "Response":
        import json
        body = json.dumps(data, default=str).encode()
        return cls(
            status=status,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            body=body,
        )

    @classmethod
    def error(cls, status: int, message: str) -> "Response":
        return cls.json(status, {"error": message})


# Handler type: receives a Request, returns a Response
HandlerFn = Callable[[Request], Response]

# Middleware callable type
MiddlewareFn = Callable[[HandlerFn, Request], Response]


# ---------------------------------------------------------------------------
# MiddlewareChain
# ---------------------------------------------------------------------------

class MiddlewareChain:
    """
    Ordered list of middlewares.  Call ``apply(handler)`` to wrap a base
    handler with all registered middlewares (outermost = last registered).

    Execution order example for chain.use(A).use(B).use(C):
        A → B → C → handler → C → B → A
    """

    def __init__(self) -> None:
        self._middlewares: List[MiddlewareFn] = []

    def use(self, middleware: MiddlewareFn) -> "MiddlewareChain":
        """Register a middleware.  Returns self for fluent chaining."""
        self._middlewares.append(middleware)
        return self

    def apply(self, handler: HandlerFn) -> HandlerFn:
        """Wrap *handler* with all middlewares and return the outermost callable."""
        fn = handler
        for mw in reversed(self._middlewares):
            fn = _make_wrapper(mw, fn)
        return fn

    def __len__(self) -> int:
        return len(self._middlewares)


def _make_wrapper(mw: MiddlewareFn, inner: HandlerFn) -> HandlerFn:
    """Capture mw + inner in a closure to avoid late-binding issues."""
    def wrapper(request: Request) -> Response:
        return mw(inner, request)
    return wrapper


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------

class AuthMiddleware:
    """
    Enforce API-key authentication via ``X-API-Key`` request header.

    Parameters
    ----------
    api_keys   : set of valid key strings
    skip_paths : paths that bypass auth (default: {"/health"})
    header     : header name to inspect (default: ``X-API-Key``)
    """

    def __init__(
        self,
        api_keys:   Set[str],
        skip_paths: Optional[Set[str]] = None,
        header:     str = "X-API-Key",
    ) -> None:
        self._keys       = frozenset(api_keys)
        self._skip       = frozenset(skip_paths or {"/health"})
        self._header     = header

    def __call__(self, handler: HandlerFn, request: Request) -> Response:
        if request.path in self._skip:
            return handler(request)
        key = request.headers.get(self._header, "")
        if not key:
            return Response.error(401, "Missing API key")
        if key not in self._keys:
            return Response.error(401, "Invalid API key")
        return handler(request)


# ---------------------------------------------------------------------------
# RateLimitMiddleware  (token-bucket per IP)
# ---------------------------------------------------------------------------

@dataclass
class _Bucket:
    tokens:     float
    last_refill: float  # monotonic seconds


class RateLimitMiddleware:
    """
    Per-IP token-bucket rate limiter.

    Parameters
    ----------
    requests_per_minute : steady-state rate (tokens refilled per second = rpm/60)
    burst               : maximum burst size (default = requests_per_minute)
    ttl_seconds         : idle TTL before bucket is reaped (default 300)
    skip_paths          : paths exempt from rate limiting (default: {"/health"})
    """

    def __init__(
        self,
        requests_per_minute: float = 60.0,
        burst:               Optional[float] = None,
        ttl_seconds:         float = 300.0,
        skip_paths:          Optional[Set[str]] = None,
    ) -> None:
        self._rate      = requests_per_minute / 60.0  # tokens / second
        self._capacity  = burst if burst is not None else requests_per_minute
        self._ttl       = ttl_seconds
        self._skip      = frozenset(skip_paths or {"/health"})
        self._buckets:  Dict[str, _Bucket] = {}
        self._lock      = threading.Lock()

    def __call__(self, handler: HandlerFn, request: Request) -> Response:
        if request.path in self._skip:
            return handler(request)
        ip = request.remote
        allowed, wait = self._consume(ip)
        if not allowed:
            resp = Response.error(429, "Rate limit exceeded")
            resp.headers["Retry-After"] = str(int(wait) + 1)
            return resp
        return handler(request)

    def _consume(self, ip: str) -> tuple[bool, float]:
        """Attempt to consume one token.  Returns (allowed, seconds_to_wait)."""
        now = time.monotonic()
        with self._lock:
            self._reap(now)
            bucket = self._buckets.get(ip)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, last_refill=now)
                self._buckets[ip] = bucket

            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            wait = (1.0 - bucket.tokens) / self._rate
            return False, wait

    def _reap(self, now: float) -> None:
        """Remove buckets that have been idle longer than ttl_seconds."""
        stale = [
            ip for ip, b in self._buckets.items()
            if now - b.last_refill > self._ttl
        ]
        for ip in stale:
            del self._buckets[ip]


# ---------------------------------------------------------------------------
# CORSMiddleware
# ---------------------------------------------------------------------------

class CORSMiddleware:
    """
    Cross-Origin Resource Sharing headers.

    Parameters
    ----------
    allowed_origins : list of allowed origins, or ["*"] for open CORS
    allowed_methods : HTTP methods to advertise (default: common REST set)
    allowed_headers : request headers to allow (default includes X-API-Key)
    max_age         : preflight cache duration in seconds (default 600)
    """

    _DEFAULT_METHODS = "GET, POST, PUT, DELETE, OPTIONS"
    _DEFAULT_HEADERS = "Content-Type, X-API-Key, Authorization"

    def __init__(
        self,
        allowed_origins: Optional[List[str]] = None,
        allowed_methods: str = _DEFAULT_METHODS,
        allowed_headers: str = _DEFAULT_HEADERS,
        max_age:         int = 600,
    ) -> None:
        self._origins = list(allowed_origins or ["*"])
        self._methods = allowed_methods
        self._headers = allowed_headers
        self._max_age = str(max_age)

    def __call__(self, handler: HandlerFn, request: Request) -> Response:
        origin = request.headers.get("Origin", "")
        
        # Preflight
        if request.method == "OPTIONS":
            resp = Response(status=204)
            self._add_cors(resp, origin)
            resp.headers["Access-Control-Max-Age"] = self._max_age
            return resp

        resp = handler(request)
        self._add_cors(resp, origin)
        return resp

    def _add_cors(self, resp: Response, origin: str) -> None:
        allowed = self._resolve_origin(origin)
        resp.headers["Access-Control-Allow-Origin"]  = allowed
        resp.headers["Access-Control-Allow-Methods"] = self._methods
        resp.headers["Access-Control-Allow-Headers"] = self._headers
        if allowed != "*":
            resp.headers["Vary"] = "Origin"

    def _resolve_origin(self, origin: str) -> str:
        if "*" in self._origins:
            return "*"
        if origin in self._origins:
            return origin
        return self._origins[0] if self._origins else "*"


# ---------------------------------------------------------------------------
# LoggingMiddleware
# ---------------------------------------------------------------------------

_REDACT_HEADERS: FrozenSet[str] = frozenset({"authorization", "x-api-key", "cookie"})


class LoggingMiddleware:
    """
    Structured request/response logger.

    Logs at INFO level on completion::

        GET /workflows/abc123 -> 200 (4.21 ms) [192.168.1.10]

    Sensitive headers are redacted in DEBUG output.

    Parameters
    ----------
    logger      : logging.Logger to use (default: module logger)
    log_headers : emit request headers at DEBUG level (default False)
    log_body    : emit request body at DEBUG level (default False)
    """

    def __init__(
        self,
        logger:      Optional[logging.Logger] = None,
        log_headers: bool = False,
        log_body:    bool = False,
    ) -> None:
        self._log         = logger or log
        self._log_headers = log_headers
        self._log_body    = log_body

    def __call__(self, handler: HandlerFn, request: Request) -> Response:
        start = time.perf_counter()

        if self._log_headers and self._log.isEnabledFor(logging.DEBUG):
            redacted = {
                k: ("<redacted>" if k.lower() in _REDACT_HEADERS else v)
                for k, v in request.headers.items()
            }
            self._log.debug("REQ headers: %s", redacted)

        if self._log_body and request.body and self._log.isEnabledFor(logging.DEBUG):
            self._log.debug("REQ body: %s", request.body[:512])

        try:
            response = handler(request)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log.error(
                "%s %s -> ERROR (%.2f ms) [%s]: %s",
                request.method, request.path, elapsed_ms, request.remote, exc,
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._log.info(
            "%s %s -> %d (%.2f ms) [%s]",
            request.method, request.path, response.status, elapsed_ms, request.remote,
        )
        return response
