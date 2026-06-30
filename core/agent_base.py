"""
C92 — Agent Base
Abstract base class for all ShadowRealm agents.
Provides lifecycle hooks, config validation, and shared utilities.
"""
from __future__ import annotations

import abc
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from core.llm_client import LLMClient, LLMMessage
from core.memory_manager import MemoryManager
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    name: str = "agent"
    system_prompt: str = ""
    model: str = "gpt-4o"
    provider: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    max_iterations: int = 20
    memory_strategy: str = "sliding_window"
    memory_max_tokens: int = 8192
    timeout: float = 120.0
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentRun:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    iterations: int = 0
    input: str = ""
    output: str = ""
    success: bool = False
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def finish(self, output: str, success: bool = True, error: Optional[str] = None) -> None:
        self.finished_at = time.time()
        self.output = output
        self.success = success
        self.error = error

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at


class AgentError(Exception):
    pass


class MaxIterationsError(AgentError):
    pass


class BaseAgent(abc.ABC):
    """
    Abstract base for all agents.
    Subclasses must implement `_run_iteration`.
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        llm: Optional[LLMClient] = None,
        memory: Optional[MemoryManager] = None,
        tools: Optional[ToolRegistry] = None,
    ):
        self.config = config or AgentConfig()
        self.llm = llm or LLMClient(
            model=self.config.model,
            provider=self.config.provider,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        self.memory = memory or MemoryManager(
            max_tokens=self.config.memory_max_tokens,
            strategy=self.config.memory_strategy,
            system_prompt=self.config.system_prompt,
        )
        self.tools = tools or ToolRegistry()
        self._current_run: Optional[AgentRun] = None

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def run(self, user_input: str, **kwargs) -> AgentRun:
        run = AgentRun(agent_name=self.config.name, input=user_input)
        self._current_run = run
        self.on_run_start(run)
        try:
            self.memory.add_user(user_input)
            output = self._execute_loop(run, **kwargs)
            run.finish(output=output, success=True)
        except MaxIterationsError as e:
            run.finish(output="", success=False, error=str(e))
            self.on_max_iterations(run)
        except Exception as e:
            run.finish(output="", success=False, error=str(e))
            logger.exception("Agent '%s' run failed: %s", self.config.name, e)
            self.on_error(run, e)
        finally:
            self._current_run = None
            self.on_run_end(run)
        return run

    async def arun(self, user_input: str, **kwargs) -> AgentRun:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.run(user_input, **kwargs))

    def reset(self) -> None:
        """Clear memory and reset state."""
        self.memory.clear(keep_system=True)

    # ------------------------------------------------------------------ #
    #  Core loop                                                           #
    # ------------------------------------------------------------------ #

    def _execute_loop(self, run: AgentRun, **kwargs) -> str:
        for i in range(self.config.max_iterations):
            run.iterations = i + 1
            result = self._run_iteration(run, **kwargs)
            if result is not None:
                return result
        raise MaxIterationsError(
            f"Agent '{self.config.name}' reached max iterations ({self.config.max_iterations})"
        )

    @abc.abstractmethod
    def _run_iteration(self, run: AgentRun, **kwargs) -> Optional[str]:
        """
        Execute one iteration of the agent loop.
        Return a final answer string to stop, or None to continue.
        """

    # ------------------------------------------------------------------ #
    #  Lifecycle hooks (override as needed)                               #
    # ------------------------------------------------------------------ #

    def on_run_start(self, run: AgentRun) -> None:
        logger.debug("[%s] Run started: %s", self.config.name, run.run_id)

    def on_run_end(self, run: AgentRun) -> None:
        logger.debug(
            "[%s] Run finished in %.2fs, %d iterations",
            self.config.name, run.elapsed, run.iterations,
        )

    def on_max_iterations(self, run: AgentRun) -> None:
        logger.warning("[%s] Max iterations reached", self.config.name)

    def on_error(self, run: AgentRun, error: Exception) -> None:
        logger.error("[%s] Error: %s", self.config.name, error)

    def on_tool_call(self, name: str, arguments: dict) -> None:
        logger.debug("[%s] Tool call: %s(%s)", self.config.name, name, arguments)

    def on_tool_result(self, name: str, result: Any) -> None:
        logger.debug("[%s] Tool result from %s: %s", self.config.name, name, str(result)[:200])

    # ------------------------------------------------------------------ #
    #  Utilities                                                           #
    # ------------------------------------------------------------------ #

    def _chat(self, tools: Optional[list[dict]] = None):
        """Call the LLM with current memory."""
        return self.llm.complete(self.memory.get_messages(), tools=tools)

    def _tool_schemas(self) -> list[dict]:
        if self.config.provider == "anthropic":
            return self.tools.as_anthropic_tools()
        return self.tools.as_openai_tools()

    @property
    def name(self) -> str:
        return self.config.name
