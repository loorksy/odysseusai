"""
core/llm_client.py
C88 — Unified LLM Client

Provides a single interface for all supported LLM backends:
  - OpenAI (GPT-4o, GPT-4o-mini, o1, o3, etc.)
  - Anthropic (Claude 3.5 Sonnet, Claude 3 Haiku, etc.)
  - Google Gemini (gemini-1.5-pro, gemini-flash, etc.)
  - Ollama (local models: llama3, mistral, phi3, etc.)

Features:
  - Streaming + non-streaming completions
  - Tool/function-calling (passes schemas from C89 ToolRegistry)
  - Structured JSON output mode
  - Retry with exponential backoff (delegates to C14 RetryPolicy)
  - Per-provider token counting
  - Zero external dependencies at import time — providers loaded lazily

Architecture Invariant #1: core/ = stdlib only at module level.
Provider SDKs (openai, anthropic, google-generativeai) are imported
only inside provider-specific methods, so this module remains importable
in environments where those SDKs are not installed.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Constants
# ---------------------------------------------------------------------------

class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


DEFAULT_MODELS: Dict[str, str] = {
    LLMProvider.OPENAI: "gpt-4o",
    LLMProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
    LLMProvider.GEMINI: "gemini-1.5-pro",
    LLMProvider.OLLAMA: "llama3",
}

MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.0  # seconds


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in a conversation."""
    role: str          # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: Optional[str] = None   # populated for role="tool" responses
    tool_calls: Optional[List[Dict]] = None  # populated on assistant tool-use turns

    def to_dict(self) -> Dict:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d


@dataclass
class LLMResponse:
    """Normalised response from any LLM backend."""
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    tool_calls: Optional[List[Dict]] = None
    raw: Optional[Any] = None           # original provider response object
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMConfig:
    """Runtime configuration for a single LLM call."""
    provider: str = LLMProvider.OPENAI
    model: Optional[str] = None          # defaults to DEFAULT_MODELS[provider]
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    stream: bool = False
    tools: Optional[List[Dict]] = None   # OpenAI-spec tool schemas from C89
    tool_choice: Union[str, Dict] = "auto"
    response_format: Optional[str] = None  # "json_object" | None
    system_prompt: Optional[str] = None
    timeout: float = 60.0
    api_key: Optional[str] = None        # overrides env var
    base_url: Optional[str] = None       # for Ollama or OpenAI-compatible endpoints
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def resolved_model(self) -> str:
        return self.model or DEFAULT_MODELS.get(self.provider, "gpt-4o")


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Unified LLM client. Single entry point for all LLM calls across the system.

    Usage:
        client = LLMClient()

        # Simple completion
        response = client.complete(
            messages=[Message(role="user", content="Hello!")],
            config=LLMConfig(provider="openai", model="gpt-4o"),
        )
        print(response.content)

        # Streaming
        for chunk in client.stream(
            messages=[Message(role="user", content="Tell me a story")],
            config=LLMConfig(provider="anthropic", stream=True),
        ):
            print(chunk, end="", flush=True)

        # With tools (C89 ToolRegistry integration)
        from core.tool_registry import get_registry
        schemas = get_registry().get_schemas()
        response = client.complete(
            messages=[Message(role="user", content="Search for latest AI news")],
            config=LLMConfig(provider="openai", tools=schemas),
        )
    """

    def __init__(self, default_config: Optional[LLMConfig] = None):
        self._default_config = default_config or LLMConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: List[Message],
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        """
        Blocking completion. Returns a normalised LLMResponse.
        Retries transient errors up to MAX_RETRIES times.
        """
        cfg = config or self._default_config
        return self._call_with_retry(messages, cfg)

    def stream(
        self,
        messages: List[Message],
        config: Optional[LLMConfig] = None,
    ) -> Iterator[str]:
        """
        Streaming completion. Yields text chunks as they arrive.
        config.stream is forced True.
        """
        cfg = config or self._default_config
        cfg.stream = True
        yield from self._stream_dispatch(messages, cfg)

    def complete_json(
        self,
        messages: List[Message],
        config: Optional[LLMConfig] = None,
    ) -> Dict:
        """
        Request structured JSON output.
        Sets response_format="json_object" and parses the result.
        Raises ValueError if the model returns non-JSON.
        """
        cfg = config or self._default_config
        cfg.response_format = "json_object"
        response = self.complete(messages, cfg)
        try:
            return json.loads(response.content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"[LLMClient] Model returned non-JSON: {response.content[:200]}"
            ) from e

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    def _call_with_retry(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self._dispatch(messages, config)
            except _RETRYABLE_ERRORS as e:
                last_exc = e
                delay = BASE_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"[LLMClient] Attempt {attempt}/{MAX_RETRIES} failed: {e}. "
                    f"Retrying in {delay:.1f}s…"
                )
                time.sleep(delay)
            except Exception:
                raise
        raise RuntimeError(
            f"[LLMClient] All {MAX_RETRIES} attempts failed."
        ) from last_exc

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        provider = config.provider
        if provider == LLMProvider.OPENAI:
            return self._call_openai(messages, config)
        if provider == LLMProvider.ANTHROPIC:
            return self._call_anthropic(messages, config)
        if provider == LLMProvider.GEMINI:
            return self._call_gemini(messages, config)
        if provider == LLMProvider.OLLAMA:
            return self._call_ollama(messages, config)
        raise ValueError(f"[LLMClient] Unknown provider: '{provider}'")

    def _stream_dispatch(
        self, messages: List[Message], config: LLMConfig
    ) -> Iterator[str]:
        provider = config.provider
        if provider == LLMProvider.OPENAI:
            yield from self._stream_openai(messages, config)
        elif provider == LLMProvider.ANTHROPIC:
            yield from self._stream_anthropic(messages, config)
        elif provider == LLMProvider.GEMINI:
            yield from self._stream_gemini(messages, config)
        elif provider == LLMProvider.OLLAMA:
            yield from self._stream_ollama(messages, config)
        else:
            raise ValueError(f"[LLMClient] Unknown provider: '{provider}'")

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    def _call_openai(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        try:
            import openai  # lazy import
        except ImportError as e:
            raise ImportError("openai SDK not installed. Run: pip install openai") from e

        client = openai.OpenAI(
            api_key=config.api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=config.base_url,
            timeout=config.timeout,
        )

        kwargs: Dict[str, Any] = {
            "model": config.resolved_model,
            "messages": self._openai_messages(messages, config),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "top_p": config.top_p,
        }
        if config.tools:
            kwargs["tools"] = config.tools
            kwargs["tool_choice"] = config.tool_choice
        if config.response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        kwargs.update(config.extra)

        t0 = time.perf_counter()
        resp = client.chat.completions.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000

        choice = resp.choices[0]
        msg = choice.message
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        return LLMResponse(
            content=msg.content or "",
            model=resp.model,
            provider=LLMProvider.OPENAI,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
            raw=resp,
            latency_ms=latency,
        )

    def _stream_openai(
        self, messages: List[Message], config: LLMConfig
    ) -> Iterator[str]:
        try:
            import openai
        except ImportError as e:
            raise ImportError("openai SDK not installed.") from e

        client = openai.OpenAI(
            api_key=config.api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=config.base_url,
            timeout=config.timeout,
        )
        stream = client.chat.completions.create(
            model=config.resolved_model,
            messages=self._openai_messages(messages, config),
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def _openai_messages(
        self, messages: List[Message], config: LLMConfig
    ) -> List[Dict]:
        result = []
        if config.system_prompt:
            result.append({"role": "system", "content": config.system_prompt})
        result.extend(m.to_dict() for m in messages)
        return result

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    def _call_anthropic(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        try:
            import anthropic  # lazy import
        except ImportError as e:
            raise ImportError(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from e

        client = anthropic.Anthropic(
            api_key=config.api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=config.timeout,
        )

        # Anthropic separates system from messages
        system = config.system_prompt or ""
        ant_messages = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]

        kwargs: Dict[str, Any] = {
            "model": config.resolved_model,
            "max_tokens": config.max_tokens,
            "messages": ant_messages,
        }
        if system:
            kwargs["system"] = system
        if config.tools:
            # Convert OpenAI tool schema to Anthropic format
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in config.tools
            ]
        kwargs.update(config.extra)

        t0 = time.perf_counter()
        resp = client.messages.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000

        content_text = ""
        tool_calls = None
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input),
                    },
                })

        return LLMResponse(
            content=content_text,
            model=resp.model,
            provider=LLMProvider.ANTHROPIC,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            finish_reason=resp.stop_reason or "stop",
            tool_calls=tool_calls,
            raw=resp,
            latency_ms=latency,
        )

    def _stream_anthropic(
        self, messages: List[Message], config: LLMConfig
    ) -> Iterator[str]:
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("anthropic SDK not installed.") from e

        client = anthropic.Anthropic(
            api_key=config.api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )
        system = config.system_prompt or ""
        ant_messages = [
            {"role": m.role, "content": m.content}
            for m in messages if m.role != "system"
        ]
        with client.messages.stream(
            model=config.resolved_model,
            max_tokens=config.max_tokens,
            system=system,
            messages=ant_messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    # ------------------------------------------------------------------
    # Google Gemini
    # ------------------------------------------------------------------

    def _call_gemini(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        try:
            import google.generativeai as genai  # lazy import
        except ImportError as e:
            raise ImportError(
                "google-generativeai SDK not installed. "
                "Run: pip install google-generativeai"
            ) from e

        genai.configure(api_key=config.api_key or os.environ.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name=config.resolved_model,
            system_instruction=config.system_prompt,
        )

        # Convert to Gemini content format
        history = []
        last_user_msg = ""
        for m in messages:
            if m.role == "system":
                continue
            gemini_role = "model" if m.role == "assistant" else "user"
            if m == messages[-1] and m.role == "user":
                last_user_msg = m.content
            else:
                history.append({"role": gemini_role, "parts": [m.content]})

        gen_config = genai.types.GenerationConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
            top_p=config.top_p,
        )

        t0 = time.perf_counter()
        chat = model.start_chat(history=history)
        resp = chat.send_message(last_user_msg or messages[-1].content, generation_config=gen_config)
        latency = (time.perf_counter() - t0) * 1000

        return LLMResponse(
            content=resp.text,
            model=config.resolved_model,
            provider=LLMProvider.GEMINI,
            input_tokens=getattr(resp.usage_metadata, "prompt_token_count", 0),
            output_tokens=getattr(resp.usage_metadata, "candidates_token_count", 0),
            finish_reason="stop",
            raw=resp,
            latency_ms=latency,
        )

    def _stream_gemini(
        self, messages: List[Message], config: LLMConfig
    ) -> Iterator[str]:
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise ImportError("google-generativeai SDK not installed.") from e

        genai.configure(api_key=config.api_key or os.environ.get("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name=config.resolved_model,
            system_instruction=config.system_prompt,
        )
        gen_config = genai.types.GenerationConfig(
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
        )
        content = messages[-1].content
        for chunk in model.generate_content(content, generation_config=gen_config, stream=True):
            if chunk.text:
                yield chunk.text

    # ------------------------------------------------------------------
    # Ollama (local)
    # ------------------------------------------------------------------

    def _call_ollama(self, messages: List[Message], config: LLMConfig) -> LLMResponse:
        """
        Calls a local Ollama instance via its OpenAI-compatible REST API.
        Default base URL: http://localhost:11434/v1
        No SDK required — uses urllib from stdlib.
        """
        import urllib.request  # stdlib
        import urllib.error

        base_url = config.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434/v1"
        )
        url = f"{base_url.rstrip('/')}/chat/completions"

        payload = {
            "model": config.resolved_model,
            "messages": self._openai_messages(messages, config),
            "temperature": config.temperature,
            "stream": False,
        }
        if config.tools:
            payload["tools"] = config.tools

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=config.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"[LLMClient] Ollama unreachable at {base_url}. "
                "Is Ollama running? Try: ollama serve"
            ) from e
        latency = (time.perf_counter() - t0) * 1000

        choice = raw["choices"][0]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls")

        return LLMResponse(
            content=msg.get("content") or "",
            model=raw.get("model", config.resolved_model),
            provider=LLMProvider.OLLAMA,
            input_tokens=raw.get("usage", {}).get("prompt_tokens", 0),
            output_tokens=raw.get("usage", {}).get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=tool_calls,
            raw=raw,
            latency_ms=latency,
        )

    def _stream_ollama(
        self, messages: List[Message], config: LLMConfig
    ) -> Iterator[str]:
        import urllib.request
        import urllib.error

        base_url = config.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434/v1"
        )
        url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": config.resolved_model,
            "messages": self._openai_messages(messages, config),
            "temperature": config.temperature,
            "stream": True,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.timeout) as resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError):
                            continue
        except urllib.error.URLError as e:
            raise ConnectionError(f"[LLMClient] Ollama stream failed: {e}") from e


# ---------------------------------------------------------------------------
# Retryable error base — populated after lazy imports succeed
# Using BaseException subclass so it's always valid at module level
# ---------------------------------------------------------------------------
_RETRYABLE_ERRORS = (ConnectionError, TimeoutError, OSError)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_client: Optional[LLMClient] = None


def get_client(config: Optional[LLMConfig] = None) -> LLMClient:
    """Return the module-level shared LLMClient (lazy-init singleton)."""
    global _client
    if _client is None:
        _client = LLMClient(default_config=config)
        logger.debug("[LLMClient] Global client initialised.")
    return _client


def complete(
    messages: List[Message],
    config: Optional[LLMConfig] = None,
) -> LLMResponse:
    """Module-level shorthand: get_client().complete(...)"""
    return get_client().complete(messages, config)


def stream(
    messages: List[Message],
    config: Optional[LLMConfig] = None,
) -> Iterator[str]:
    """Module-level shorthand: get_client().stream(...)"""
    yield from get_client().stream(messages, config)
