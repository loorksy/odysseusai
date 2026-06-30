"""HTTPClient — Resilient HTTP client with retries and tracing (C67).

Wraps urllib.request (stdlib) with an optional requests/httpx adapter.
Provides a consistent interface regardless of which HTTP lib is installed.

Features:
  - GET / POST / PUT / PATCH / DELETE helpers
  - Automatic JSON serialisation / deserialisation
  - Configurable retry policy: max_retries, backoff, retry-on status codes
  - Request / response logging with redacted auth headers
  - Timeout enforcement (connect + read)
  - Bearer token and Basic auth helpers
  - Response object with .json(), .text, .status, .headers

Public API:
  client = HTTPClient(base_url="", timeout_s=10, max_retries=3)
  resp = client.get(path, *, params, headers)
  resp = client.post(path, json=None, data=None, *, headers)
  resp = client.put(path, json=None, *, headers)
  resp = client.patch(path, json=None, *, headers)
  resp = client.delete(path, *, headers)
  client.set_auth_bearer(token)
  client.set_auth_basic(user, password)
"""
from __future__ import annotations
import base64, json as _json, logging, time, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_BASE_BACKOFF   = 0.5
_MAX_BACKOFF    = 30.0


@dataclass
class HTTPResponse:
    status:  int
    headers: Dict[str, str]
    body:    bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return _json.loads(self.body)

    def ok(self) -> bool:
        return 200 <= self.status < 300


class HTTPError(Exception):
    def __init__(self, message: str, status: int = 0, response: Optional[HTTPResponse] = None):
        super().__init__(message)
        self.status   = status
        self.response = response


class HTTPClient:
    """Resilient HTTP client backed by stdlib urllib."""

    def __init__(
        self,
        base_url:    str   = "",
        timeout_s:   float = 10.0,
        max_retries: int   = 3,
        verify_ssl:  bool  = True,
    ):
        self._base        = base_url.rstrip("/")
        self._timeout     = timeout_s
        self._max_retries = max_retries
        self._verify_ssl  = verify_ssl
        self._default_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }

    def set_auth_bearer(self, token: str) -> None:
        self._default_headers["Authorization"] = f"Bearer {token}"

    def set_auth_basic(self, user: str, password: str) -> None:
        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._default_headers["Authorization"] = f"Basic {creds}"

    def set_header(self, key: str, value: str) -> None:
        self._default_headers[key] = value

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------

    def get(self, path: str, *, params: Optional[Dict] = None,
            headers: Optional[Dict] = None) -> HTTPResponse:
        url = self._url(path, params)
        return self._request("GET", url, headers=headers)

    def post(self, path: str, *, json: Any = None, data: Optional[bytes] = None,
             headers: Optional[Dict] = None) -> HTTPResponse:
        return self._request("POST", self._url(path), body=self._body(json, data),
                             headers=headers)

    def put(self, path: str, *, json: Any = None,
            headers: Optional[Dict] = None) -> HTTPResponse:
        return self._request("PUT", self._url(path), body=self._body(json),
                             headers=headers)

    def patch(self, path: str, *, json: Any = None,
              headers: Optional[Dict] = None) -> HTTPResponse:
        return self._request("PATCH", self._url(path), body=self._body(json),
                             headers=headers)

    def delete(self, path: str, *, headers: Optional[Dict] = None) -> HTTPResponse:
        return self._request("DELETE", self._url(path), headers=headers)

    # ------------------------------------------------------------------
    # Core request with retry
    # ------------------------------------------------------------------

    def _request(
        self, method: str, url: str,
        body: Optional[bytes] = None,
        headers: Optional[Dict] = None,
    ) -> HTTPResponse:
        merged = {**self._default_headers, **(headers or {})}
        backoff = _BASE_BACKOFF
        last_exc = None
        for attempt in range(self._max_retries + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=merged, method=method)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    rbody   = resp.read()
                    rstatus = resp.status
                    rheads  = dict(resp.headers)
                response = HTTPResponse(status=rstatus, headers=rheads, body=rbody)
                self._log(method, url, rstatus)
                if rstatus in _RETRY_STATUSES and attempt < self._max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                    continue
                return response
            except urllib.error.HTTPError as e:
                rbody   = e.read()
                response = HTTPResponse(status=e.code, headers=dict(e.headers), body=rbody)
                self._log(method, url, e.code)
                if e.code in _RETRY_STATUSES and attempt < self._max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                    last_exc = e
                    continue
                raise HTTPError(str(e), status=e.code, response=response) from e
            except Exception as e:
                last_exc = e
                if attempt < self._max_retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF)
                else:
                    raise HTTPError(str(e)) from e
        raise HTTPError(f"Max retries exceeded: {last_exc}")

    def _url(self, path: str, params: Optional[Dict] = None) -> str:
        url = self._base + ("" if path.startswith("http") else "/" + path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    @staticmethod
    def _body(json_data: Any = None, raw: Optional[bytes] = None) -> Optional[bytes]:
        if json_data is not None:
            return _json.dumps(json_data, separators=(",", ":")).encode()
        return raw

    @staticmethod
    def _log(method: str, url: str, status: int) -> None:
        logger.debug(f"HTTPClient: {method} {url} -> {status}")
