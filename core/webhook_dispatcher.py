"""WebhookDispatcher — Reliable outbound webhook delivery (C68).

Sends signed HTTP POST payloads to registered subscriber URLs.
Supports:
  - HMAC-SHA256 request signing (X-ShadowRealm-Signature header)
  - Per-subscriber retry queue with exponential backoff
  - Delivery receipts: success / failure / pending
  - Event filtering: subscribers declare which event types they want
  - Async delivery via background thread pool
  - Dead-letter store for permanently failed deliveries

Public API:
  wd = WebhookDispatcher(http_client, signing_secret)
  wd.subscribe(name, url, *, events, secret)
  wd.unsubscribe(name)
  wd.dispatch(event_type, payload)  -> list[DeliveryReceipt]
  wd.receipts(name, n)  -> list[DeliveryReceipt]
  wd.dead_letters()     -> list[DeliveryReceipt]
  wd.subscribers()      -> list[dict]
"""
from __future__ import annotations
import hashlib, hmac, json, logging, threading, time, uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_RETRIES  = 5
_BASE_BACKOFF = 2.0
_MAX_BACKOFF  = 300.0


@dataclass
class DeliveryReceipt:
    receipt_id:  str
    subscriber:  str
    event_type:  str
    url:         str
    status:      str      # pending | delivered | failed | dead
    status_code: int = 0
    attempts:    int = 0
    created_at:  float = field(default_factory=time.time)
    delivered_at: Optional[float] = None
    error:       str = ""


@dataclass
class _Subscriber:
    name:    str
    url:     str
    events:  List[str]   # empty = all events
    secret:  str
    active:  bool = True
    history: List[DeliveryReceipt] = field(default_factory=list)


class WebhookDispatcher:
    """HMAC-signed webhook delivery with per-subscriber retry."""

    def __init__(self, http_client=None, signing_secret: str = ""):
        self._http    = http_client
        self._secret  = signing_secret
        self._subs:   Dict[str, _Subscriber] = {}
        self._dead:   List[DeliveryReceipt] = []
        self._lock    = threading.Lock()

    def subscribe(
        self, name: str, url: str,
        *, events: Optional[List[str]] = None, secret: str = "",
    ) -> None:
        with self._lock:
            self._subs[name] = _Subscriber(
                name=name, url=url,
                events=events or [],
                secret=secret or self._secret,
            )

    def unsubscribe(self, name: str) -> bool:
        with self._lock:
            sub = self._subs.get(name)
            if sub: sub.active = False
        return sub is not None

    def dispatch(self, event_type: str, payload: Any) -> List[DeliveryReceipt]:
        with self._lock:
            subs = [s for s in self._subs.values()
                    if s.active and (not s.events or event_type in s.events)]
        receipts = []
        for sub in subs:
            receipt = DeliveryReceipt(
                receipt_id=uuid.uuid4().hex,
                subscriber=sub.name, event_type=event_type,
                url=sub.url, status="pending",
            )
            receipts.append(receipt)
            t = threading.Thread(
                target=self._deliver,
                args=(sub, event_type, payload, receipt),
                daemon=True,
            )
            t.start()
        return receipts

    def receipts(self, name: str, n: int = 50) -> List[DeliveryReceipt]:
        with self._lock:
            sub = self._subs.get(name)
        return sub.history[-n:] if sub else []

    def dead_letters(self) -> List[DeliveryReceipt]:
        return list(self._dead)

    def subscribers(self) -> List[Dict]:
        with self._lock:
            return [
                {"name": s.name, "url": s.url,
                 "events": s.events, "active": s.active}
                for s in self._subs.values()
            ]

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def _deliver(
        self, sub: _Subscriber, event_type: str,
        payload: Any, receipt: DeliveryReceipt,
    ) -> None:
        body = json.dumps({"event": event_type, "data": payload,
                           "id": receipt.receipt_id}).encode()
        sig  = hmac.new(sub.secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type":           "application/json",
            "X-ShadowRealm-Event":    event_type,
            "X-ShadowRealm-Signature": f"sha256={sig}",
        }
        backoff = _BASE_BACKOFF
        for attempt in range(1, _MAX_RETRIES + 1):
            receipt.attempts = attempt
            try:
                if self._http:
                    resp = self._http.post(sub.url, data=body, headers=headers)
                    code = resp.status
                else:
                    import urllib.request
                    req = urllib.request.Request(sub.url, data=body, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=10) as r:
                        code = r.status
                if 200 <= code < 300:
                    receipt.status       = "delivered"
                    receipt.status_code  = code
                    receipt.delivered_at = time.time()
                    sub.history.append(receipt)
                    return
                receipt.status_code = code
            except Exception as e:
                receipt.error = str(e)
            if attempt < _MAX_RETRIES:
                time.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
        receipt.status = "dead"
        sub.history.append(receipt)
        with self._lock:
            self._dead.append(receipt)
        logger.warning(f"WebhookDispatcher: '{sub.name}' delivery dead after {_MAX_RETRIES} attempts")
