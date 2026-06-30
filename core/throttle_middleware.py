"""ThrottleMiddleware — Flask/Quart WSGI/ASGI throttle layer (C45).

Integrates RateLimiter and QuotaManager into the HTTP request path.
Registers as a Flask before_request / after_request hook pair (or can
be used as a standalone decorator on individual routes).

Behaviour:
  1. Identify owner from JWT sub claim, API-key header, or IP fallback
  2. Check RateLimiter for short-horizon limits (RPM / burst)
  3. Check QuotaManager for long-horizon limits (daily tokens, etc.)
  4. If either blocks: return 429 JSON response with Retry-After header
  5. On pass: attach rate/quota context to Flask g for downstream use
  6. Emit TelemetryCollector event for every throttle decision

Config is loaded from a ThrottleConfig dataclass (or dict).

Public API:
  mw = ThrottleMiddleware(app=None, rate_limiter=None, quota_manager=None)
  mw.init_app(flask_app)             # Flask application-factory pattern
  mw.exempt(route_or_fn)            # decorator: skip throttle for route
  @mw.throttle(resource, rpm, burst) # decorator: per-route override
  mw.identify_owner(request) -> str  # override to customise owner detection
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)

_DEFAULT_RESOURCE = "http_request"
_DEFAULT_RPM      = 120
_DEFAULT_BURST    = 20
_OWNER_HEADER     = "X-Owner-Id"
_API_KEY_HEADER   = "X-Api-Key"


class ThrottleMiddleware:
    """Flask/Quart throttle layer backed by RateLimiter + QuotaManager."""

    def __init__(
        self,
        app=None,
        rate_limiter=None,
        quota_manager=None,
        telemetry=None,
    ):
        self._rl        = rate_limiter
        self._qm        = quota_manager
        self._tel       = telemetry
        self._exempt:   Set[str] = set()     # exempt function names
        self._overrides: Dict[str, Dict] = {} # fn_name -> {resource, rpm, burst}
        if app is not None:
            self.init_app(app)

    # ------------------------------------------------------------------
    # Flask integration
    # ------------------------------------------------------------------

    def init_app(self, app) -> None:
        """Register before/after hooks on a Flask app."""
        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.extensions["throttle_middleware"] = self
        logger.info("ThrottleMiddleware: registered on Flask app")

    def _before_request(self):
        try:
            from flask import request, g, jsonify, abort
        except ImportError:
            return None

        fn_name = request.endpoint or ""
        if fn_name in self._exempt:
            return None

        owner    = self.identify_owner(request)
        override = self._overrides.get(fn_name, {})
        resource = override.get("resource", _DEFAULT_RESOURCE)

        # Rate check
        if self._rl:
            result = self._rl.consume(owner, resource)
            if not result.allowed:
                self._emit("rate_limited", owner, resource)
                from flask import jsonify
                resp = jsonify({
                    "error":       "rate_limit_exceeded",
                    "retry_after": result.retry_after_s,
                    "resource":    resource,
                })
                resp.status_code = 429
                resp.headers["Retry-After"] = str(int(result.retry_after_s) + 1)
                return resp

        # Quota check
        if self._qm:
            qresult = self._qm.consume(owner, resource)
            if not qresult.allowed:
                self._emit("quota_exceeded", owner, resource)
                from flask import jsonify
                resp = jsonify({
                    "error":      "quota_exceeded",
                    "resource":   resource,
                    "resets_at":  qresult.resets_at,
                })
                resp.status_code = 429
                return resp

        # Attach to g for downstream use
        try:
            g.throttle_owner    = owner
            g.throttle_resource = resource
        except RuntimeError:
            pass
        return None

    def _after_request(self, response):
        """Inject rate-limit headers into every response."""
        try:
            from flask import g
            owner    = getattr(g, "throttle_owner", None)
            resource = getattr(g, "throttle_resource", _DEFAULT_RESOURCE)
            if owner and self._rl:
                result = self._rl.check(owner, resource)
                response.headers["X-RateLimit-Remaining"] = str(int(result.remaining))
                response.headers["X-RateLimit-Reset"]     = str(int(result.reset_in_s))
        except Exception:
            pass
        return response

    # ------------------------------------------------------------------
    # Decorators
    # ------------------------------------------------------------------

    def exempt(self, fn: Callable) -> Callable:
        """Decorator: skip throttle for this route."""
        self._exempt.add(fn.__name__)
        return fn

    def throttle(
        self,
        resource: str = _DEFAULT_RESOURCE,
        rpm:   float = _DEFAULT_RPM,
        burst: float = _DEFAULT_BURST,
    ) -> Callable:
        """Decorator: apply per-route rate-limit override."""
        def decorator(fn: Callable) -> Callable:
            self._overrides[fn.__name__] = {
                "resource": resource, "rpm": rpm, "burst": burst
            }
            if self._rl:
                self._rl.add_rule(resource, rpm=rpm, burst=burst)
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    # ------------------------------------------------------------------
    # Owner identification
    # ------------------------------------------------------------------

    def identify_owner(self, request) -> str:
        """Extract owner identifier from request. Override for custom logic."""
        # 1. Explicit header
        owner = request.headers.get(_OWNER_HEADER, "")
        if owner:
            return owner
        # 2. API key header — use as owner proxy
        api_key = request.headers.get(_API_KEY_HEADER, "")
        if api_key:
            return f"key:{api_key[:8]}"
        # 3. JWT sub from Authorization Bearer
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            try:
                import base64, json as _json
                token   = auth.split(" ", 1)[1]
                payload = token.split(".")[1]
                padded  = payload + "=" * (-len(payload) % 4)
                claims  = _json.loads(base64.urlsafe_b64decode(padded))
                sub     = claims.get("sub", "")
                if sub:
                    return sub
            except Exception:
                pass
        # 4. IP fallback
        return request.remote_addr or "anonymous"

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------

    def _emit(self, kind: str, owner: str, resource: str) -> None:
        if self._tel:
            try:
                self._tel.emit(kind, {"resource": resource}, owner=owner)
            except Exception:
                pass
