"""NotificationDispatcher — Multi-channel notification fanout (C79).

Routes notifications to one or more delivery channels based on
user preferences and notification type. Channels are pluggable.

Built-in channel adapters:
  - in_app   : writes to InAppMessageQueue
  - email    : delegates to EmailComposer
  - webhook  : delegates to WebhookDispatcher
  - log      : structured log entry (always-on fallback)

Features:
  - Per-user channel preferences (from UserPreferenceStore)
  - Per-notification-type routing rules
  - Priority levels: CRITICAL(0) / HIGH(1) / NORMAL(2) / LOW(3)
  - Deduplication window: suppress repeat notifications within N seconds
  - Delivery receipts per channel
  - Async fanout via TaskQueue or daemon threads

Public API:
  nd = NotificationDispatcher()
  nd.register_channel(name, adapter_fn)
  nd.set_routing_rule(event_type, channels)
  receipt_list = nd.notify(user_id, event_type, payload,
                           priority=NORMAL, dedupe_key=None, ttl_s=60)
  nd.receipts(user_id, n=50)  -> list[NotificationReceipt]
"""
from __future__ import annotations
import logging, threading, time, uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CRITICAL, HIGH, NORMAL, LOW = 0, 1, 2, 3
_DEFAULT_CHANNELS  = ["in_app", "log"]
_DEDUPE_WINDOW_S   = 60.0


@dataclass
class NotificationReceipt:
    receipt_id:  str
    user_id:     str
    event_type:  str
    channel:     str
    status:      str        # pending | delivered | failed | suppressed
    created_at:  float = field(default_factory=time.time)
    error:       str = ""


class NotificationDispatcher:
    """Multi-channel notification fanout with per-user routing and deduplication."""

    def __init__(self, task_queue=None, preference_store=None):
        self._tq    = task_queue
        self._prefs = preference_store
        self._channels: Dict[str, Callable] = {"log": self._log_adapter}
        self._rules:    Dict[str, List[str]] = {}
        self._dedupe:   Dict[str, float]     = {}
        self._receipts: Dict[str, List[NotificationReceipt]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_channel(self, name: str, adapter_fn: Callable) -> None:
        """Register a delivery channel. adapter_fn(user_id, payload) -> None."""
        self._channels[name] = adapter_fn
        logger.debug(f"NotificationDispatcher: registered channel '{name}'")

    def set_routing_rule(self, event_type: str, channels: List[str]) -> None:
        """Map an event type to a list of delivery channels."""
        self._rules[event_type] = channels

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    def notify(
        self,
        user_id:    str,
        event_type: str,
        payload:    Any,
        *,
        priority:   int            = NORMAL,
        dedupe_key: Optional[str]  = None,
        ttl_s:      float          = _DEDUPE_WINDOW_S,
    ) -> List[NotificationReceipt]:
        """Fan-out a notification to all resolved channels."""
        if dedupe_key:
            with self._lock:
                if time.time() - self._dedupe.get(dedupe_key, 0) < ttl_s:
                    r = self._make_receipt(user_id, event_type, "suppressed", "dedupe")
                    self._receipts.setdefault(user_id, []).append(r)
                    return [r]
                self._dedupe[dedupe_key] = time.time()

        channels = self._resolve_channels(user_id, event_type)
        receipts: List[NotificationReceipt] = []

        for channel in channels:
            r = NotificationReceipt(
                receipt_id=uuid.uuid4().hex,
                user_id=user_id, event_type=event_type,
                channel=channel, status="pending",
            )
            receipts.append(r)
            args = (channel, user_id, event_type, payload, r)
            if self._tq:
                self._tq.enqueue(self._deliver, args, priority=priority)
            else:
                threading.Thread(target=self._deliver, args=args, daemon=True).start()

        with self._lock:
            self._receipts.setdefault(user_id, []).extend(receipts)
        return receipts

    def receipts(self, user_id: str, n: int = 50) -> List[NotificationReceipt]:
        """Return last N receipts for a user."""
        with self._lock:
            return list(self._receipts.get(user_id, []))[-n:]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _deliver(
        self, channel: str, user_id: str,
        event_type: str, payload: Any,
        receipt: NotificationReceipt,
    ) -> None:
        adapter = self._channels.get(channel)
        if not adapter:
            receipt.status = "failed"
            receipt.error  = f"unknown channel: {channel}"
            logger.warning(f"NotificationDispatcher: unknown channel '{channel}'")
            return
        try:
            adapter(user_id, {"event": event_type, "data": payload})
            receipt.status = "delivered"
        except Exception as exc:
            receipt.status = "failed"
            receipt.error  = str(exc)
            logger.warning(f"NotificationDispatcher: channel '{channel}' error: {exc}")

    def _resolve_channels(self, user_id: str, event_type: str) -> List[str]:
        if event_type in self._rules:
            return self._rules[event_type]
        if self._prefs:
            try:
                prefs = self._prefs.get(user_id, "notifications.channels", None)
                if prefs:
                    return prefs
            except Exception:
                pass
        return _DEFAULT_CHANNELS

    @staticmethod
    def _log_adapter(user_id: str, payload: Dict) -> None:
        logger.info(
            f"[notification] user={user_id} "
            f"event={payload.get('event')} data={payload.get('data')}"
        )

    @staticmethod
    def _make_receipt(
        user_id: str, event_type: str, status: str, channel: str
    ) -> NotificationReceipt:
        return NotificationReceipt(
            receipt_id=uuid.uuid4().hex,
            user_id=user_id, event_type=event_type,
            channel=channel, status=status,
        )
