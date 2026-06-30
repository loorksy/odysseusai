"""StreamingResponseHandler — Unified LLM stream consumer (C37).

Wraps any streaming LLM response (OpenAI, Anthropic, or generic iterator)
into a consistent async generator interface:
  async for event in handler.stream(raw_stream): yield StreamEvent

Each StreamEvent carries:
  kind  — "token" | "tool_call" | "done" | "error"
  delta — incremental text (for token events)
  data  — structured payload (for tool_call / done / error)

The handler also:
  - Detects and buffers tool-call fragments across chunks (OpenAI style)
  - Accumulates the full response text for post-processing
  - Fires optional callbacks: on_token, on_tool_call, on_done, on_error
  - Respects a max_tokens_per_stream safety cap
  - Integrates with SSEEventBus by emitting to a bus if provided

Provider adapters:
  StreamingResponseHandler.from_openai(stream, ...)   → handler
  StreamingResponseHandler.from_anthropic(stream, ...) → handler
  StreamingResponseHandler.from_iterator(iter, ...)   → handler  (tests/mock)

Public API:
  handler = StreamingResponseHandler(bus=None, callbacks=None, max_tokens=4096)
  async for event in handler.stream_openai(raw):   yield StreamEvent
  async for event in handler.stream_anthropic(raw): yield StreamEvent
  async for event in handler.stream_iter(it):       yield StreamEvent
  handler.full_text()  → str   (available after stream exhausted)
  handler.tool_calls() → list[dict]
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TOKENS = 8_192


@dataclass
class StreamEvent:
    kind:  str        # "token" | "tool_call" | "done" | "error"
    delta: str = ""   # incremental text
    data:  Any = None # structured payload


@dataclass
class StreamCallbacks:
    on_token:     Optional[Callable[[str], None]] = None
    on_tool_call: Optional[Callable[[Dict], None]] = None
    on_done:      Optional[Callable[[str], None]] = None
    on_error:     Optional[Callable[[Exception], None]] = None


class StreamingResponseHandler:
    """Consumes LLM streaming responses and emits structured StreamEvents."""

    def __init__(
        self,
        bus=None,                             # SSEEventBus | None
        callbacks: Optional[StreamCallbacks] = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
    ):
        self._bus        = bus
        self._cb         = callbacks or StreamCallbacks()
        self._max_tokens = max_tokens
        self._session_id = session_id
        self._owner      = owner
        self._tokens:     List[str]   = []
        self._tool_calls: List[Dict]  = []
        self._token_count = 0

    # ------------------------------------------------------------------
    # OpenAI stream adapter
    # ------------------------------------------------------------------

    async def stream_openai(self, raw) -> AsyncIterator[StreamEvent]:
        """Consume an openai.AsyncStream or openai.Stream of ChatCompletionChunk."""
        pending_calls: Dict[int, Dict] = {}   # index → partial tool-call dict
        try:
            async for chunk in self._to_async(raw):
                for choice in getattr(chunk, "choices", []):
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue

                    # Token delta
                    content = getattr(delta, "content", None)
                    if content:
                        event = StreamEvent(kind="token", delta=content)
                        yield event
                        self._on_token(content)

                    # Tool-call fragments
                    tc_list = getattr(delta, "tool_calls", None) or []
                    for tc in tc_list:
                        idx = tc.index if hasattr(tc, "index") else 0
                        if idx not in pending_calls:
                            pending_calls[idx] = {
                                "id":       getattr(tc, "id", ""),
                                "name":     "",
                                "arguments": "",
                            }
                        fn = getattr(tc, "function", None)
                        if fn:
                            if getattr(fn, "name", ""):
                                pending_calls[idx]["name"] += fn.name
                            if getattr(fn, "arguments", ""):
                                pending_calls[idx]["arguments"] += fn.arguments

                    finish = getattr(choice, "finish_reason", None)
                    if finish in ("tool_calls", "stop"):
                        for tc_dict in pending_calls.values():
                            event = StreamEvent(kind="tool_call", data=tc_dict)
                            yield event
                            self._on_tool_call(tc_dict)
                        pending_calls.clear()

                    if self._token_count >= self._max_tokens:
                        logger.warning("StreamingResponseHandler: max_tokens cap reached")
                        break

        except Exception as e:
            yield StreamEvent(kind="error", data=e)
            self._fire(self._cb.on_error, e)
            return

        full = "".join(self._tokens)
        yield StreamEvent(kind="done", data=full)
        self._fire(self._cb.on_done, full)
        if self._bus:
            await self._bus.publish("done", {"text": full}, session_id=self._session_id)

    # ------------------------------------------------------------------
    # Anthropic stream adapter
    # ------------------------------------------------------------------

    async def stream_anthropic(self, raw) -> AsyncIterator[StreamEvent]:
        """Consume an anthropic MessageStream."""
        try:
            async for event in self._to_async(raw):
                etype = getattr(event, "type", "")
                if etype == "content_block_delta":
                    delta_obj = getattr(event, "delta", None)
                    text = getattr(delta_obj, "text", "") if delta_obj else ""
                    if text:
                        ev = StreamEvent(kind="token", delta=text)
                        yield ev
                        self._on_token(text)
                elif etype == "message_delta":
                    stop = getattr(getattr(event, "delta", None), "stop_reason", None)
                    if stop == "tool_use":
                        pass  # tool use handled via content_block_start
                elif etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if block and getattr(block, "type", "") == "tool_use":
                        tc = {
                            "id":        getattr(block, "id", ""),
                            "name":      getattr(block, "name", ""),
                            "arguments": "",
                        }
                        ev = StreamEvent(kind="tool_call", data=tc)
                        yield ev
                        self._on_tool_call(tc)
                elif etype == "message_stop":
                    break

                if self._token_count >= self._max_tokens:
                    break
        except Exception as e:
            yield StreamEvent(kind="error", data=e)
            self._fire(self._cb.on_error, e)
            return

        full = "".join(self._tokens)
        yield StreamEvent(kind="done", data=full)
        self._fire(self._cb.on_done, full)
        if self._bus:
            await self._bus.publish("done", {"text": full}, session_id=self._session_id)

    # ------------------------------------------------------------------
    # Generic iterator adapter (tests / mock providers)
    # ------------------------------------------------------------------

    async def stream_iter(self, it) -> AsyncIterator[StreamEvent]:
        """Consume any iterable/async-iterable of str tokens."""
        try:
            async for token in self._to_async(it):
                if isinstance(token, str):
                    ev = StreamEvent(kind="token", delta=token)
                    yield ev
                    self._on_token(token)
                    if self._token_count >= self._max_tokens:
                        break
        except Exception as e:
            yield StreamEvent(kind="error", data=e)
            self._fire(self._cb.on_error, e)
            return
        full = "".join(self._tokens)
        yield StreamEvent(kind="done", data=full)
        self._fire(self._cb.on_done, full)

    # ------------------------------------------------------------------
    # Post-stream accessors
    # ------------------------------------------------------------------

    def full_text(self) -> str:
        return "".join(self._tokens)

    def tool_calls(self) -> List[Dict]:
        return list(self._tool_calls)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_token(self, text: str) -> None:
        self._tokens.append(text)
        self._token_count += len(text.split())
        self._fire(self._cb.on_token, text)
        if self._bus:
            asyncio.ensure_future(
                self._bus.publish("token", {"delta": text}, session_id=self._session_id)
            )

    def _on_tool_call(self, tc: Dict) -> None:
        self._tool_calls.append(tc)
        self._fire(self._cb.on_tool_call, tc)

    @staticmethod
    def _fire(fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception as e:
                logger.debug(f"StreamingResponseHandler callback error: {e}")

    @staticmethod
    async def _to_async(it):
        """Wrap a sync iterable as an async one if needed."""
        if hasattr(it, "__aiter__"):
            async for item in it:
                yield item
        else:
            for item in it:
                yield item
                await asyncio.sleep(0)
