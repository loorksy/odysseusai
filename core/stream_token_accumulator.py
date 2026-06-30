"""StreamTokenAccumulator — Stateful token buffer for streaming LLM output (C39).

Collects token deltas from StreamingResponseHandler events and exposes:
  - Rolling full-text assembly
  - Partial-sentence detection (for mid-stream read-ahead)
  - Structured-output extraction once a JSON fence closes
  - Per-turn statistics: token count, first-token latency, total duration
  - Snapshot API for mid-stream UI polling

The accumulator is intentionally synchronous and lightweight; it is driven
by the caller inside the streaming loop:

  acc = StreamTokenAccumulator()
  async for event in handler.stream_openai(raw):
      if event.kind == "token":
          acc.push(event.delta)
      elif event.kind == "done":
          acc.finish()
  stats = acc.stats()
  full  = acc.text()

Public API:
  acc = StreamTokenAccumulator(owner=None, session_id=None)
  acc.push(delta)          — add a token delta
  acc.finish()             — mark stream as complete
  acc.text()               — full assembled text so far
  acc.partial_sentence()   — last incomplete sentence (for streaming UI)
  acc.json_payload()       — parsed JSON if output contains a closed ``` block
  acc.stats()              — StreamStats dataclass
  acc.snapshot()           — dict suitable for JSON-serialisation
  acc.reset()              — clear for reuse
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_SENTENCE_END = re.compile(r'(?<=[.!?])\s')
_JSON_FENCE   = re.compile(r'```(?:json)?\s*([\s\S]*?)```', re.IGNORECASE)


@dataclass
class StreamStats:
    token_count:       int
    char_count:        int
    first_token_ms:    float   # latency to first token
    total_ms:          float   # wall-clock duration
    tokens_per_second: float
    finished:          bool
    session_id:        Optional[str]
    owner:             Optional[str]


class StreamTokenAccumulator:
    """Collects streaming token deltas and provides text/stats/snapshot access."""

    def __init__(
        self,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        self._owner      = owner
        self._session_id = session_id
        self._chunks:    List[str] = []
        self._char_count  = 0
        self._token_count = 0
        self._start_ts    = time.time()
        self._first_ts:   Optional[float] = None
        self._finish_ts:  Optional[float] = None
        self._finished    = False

    # ------------------------------------------------------------------
    # Core push / finish
    # ------------------------------------------------------------------

    def push(self, delta: str) -> None:
        """Append a token delta."""
        if not delta:
            return
        if self._first_ts is None:
            self._first_ts = time.time()
        self._chunks.append(delta)
        self._char_count  += len(delta)
        self._token_count += max(1, len(delta.split()))

    def finish(self) -> None:
        """Mark stream complete."""
        self._finish_ts = time.time()
        self._finished  = True

    def reset(self) -> None:
        self._chunks       = []
        self._char_count   = 0
        self._token_count  = 0
        self._start_ts     = time.time()
        self._first_ts     = None
        self._finish_ts    = None
        self._finished     = False

    # ------------------------------------------------------------------
    # Text accessors
    # ------------------------------------------------------------------

    def text(self) -> str:
        return "".join(self._chunks)

    def partial_sentence(self) -> str:
        """Return the last incomplete sentence fragment."""
        full = self.text()
        if not full:
            return ""
        parts = _SENTENCE_END.split(full)
        last = parts[-1].strip()
        # If full text ends on a sentence boundary, nothing is partial
        return last if not full.rstrip().endswith(tuple(".!?")) else ""

    def json_payload(self) -> Optional[Any]:
        """Extract and parse JSON from the first closed ``` block, if present."""
        full = self.text()
        m = _JSON_FENCE.search(full)
        if not m:
            return None
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Stats & snapshot
    # ------------------------------------------------------------------

    def stats(self) -> StreamStats:
        now = self._finish_ts or time.time()
        first_ms = ((self._first_ts or now) - self._start_ts) * 1000
        total_ms = (now - self._start_ts) * 1000
        tps = self._token_count / max((now - self._start_ts), 0.001)
        return StreamStats(
            token_count=self._token_count,
            char_count=self._char_count,
            first_token_ms=round(first_ms, 2),
            total_ms=round(total_ms, 2),
            tokens_per_second=round(tps, 2),
            finished=self._finished,
            session_id=self._session_id,
            owner=self._owner,
        )

    def snapshot(self) -> Dict:
        s = self.stats()
        return {
            "text":             self.text(),
            "partial_sentence": self.partial_sentence(),
            "token_count":      s.token_count,
            "char_count":       s.char_count,
            "first_token_ms":   s.first_token_ms,
            "total_ms":         s.total_ms,
            "tokens_per_second": s.tokens_per_second,
            "finished":         s.finished,
            "session_id":       self._session_id,
            "owner":            self._owner,
        }
