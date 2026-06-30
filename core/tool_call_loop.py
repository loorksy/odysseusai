"""
C93 — Tool-Call Loop
ReAct-style loop that drives tool use until the LLM emits a final answer.
Works with any BaseAgent subclass.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from core.agent_base import AgentRun, BaseAgent, MaxIterationsError
from core.llm_client import LLMMessage, LLMResponse
from core.llm_response_parser import LLMResponseParser
from core.tool_registry import ToolNotFoundError, ToolValidationError

logger = logging.getLogger(__name__)

FINAL_ANSWER_TAG = "FINAL_ANSWER"


class ToolCallLoop:
    """
    Encapsulates the ReAct tool-call loop.
    Can be used standalone or embedded inside a BaseAgent subclass.

    Usage inside an agent::

        class MyAgent(BaseAgent):
            def __init__(self, ...):
                super().__init__(...)
                self._loop = ToolCallLoop(self)

            def _run_iteration(self, run, **kwargs):
                return self._loop.step(run)
    """

    def __init__(
        self,
        agent: BaseAgent,
        parser: Optional[LLMResponseParser] = None,
        final_answer_tag: str = FINAL_ANSWER_TAG,
        max_tool_errors: int = 3,
    ):
        self.agent = agent
        self.parser = parser or LLMResponseParser()
        self.final_answer_tag = final_answer_tag
        self.max_tool_errors = max_tool_errors
        self._tool_error_count = 0

    # ------------------------------------------------------------------ #
    #  Main step                                                           #
    # ------------------------------------------------------------------ #

    def step(self, run: AgentRun) -> Optional[str]:
        """
        One iteration of the loop.
        Returns a final answer string to stop, or None to continue.
        """
        tools = self.agent._tool_schemas() if len(self.agent.tools) > 0 else None
        response: LLMResponse = self.agent._chat(tools=tools)

        # --- native tool calls (OpenAI / Anthropic function calling) ---
        if response.tool_calls:
            self._handle_native_tool_calls(response)
            return None

        content = response.content or ""

        # --- text-embedded FINAL_ANSWER tag ---
        final = self._extract_final_answer(content)
        if final is not None:
            self.agent.memory.add_assistant(content)
            return final

        # --- text-embedded tool calls (<tool_call> XML) ---
        tool_calls = self.parser.extract_tool_calls(content, provider="text")
        if tool_calls:
            self.agent.memory.add_assistant(content)
            for tc in tool_calls:
                self._execute_tool(tc.name, tc.arguments, call_id=None)
            return None

        # --- no tool calls and no final tag → treat as final answer ---
        self.agent.memory.add_assistant(content)
        return content

    # ------------------------------------------------------------------ #
    #  Tool execution                                                      #
    # ------------------------------------------------------------------ #

    def _handle_native_tool_calls(self, response: LLMResponse) -> None:
        # Record assistant message with tool calls
        self.agent.memory.add(
            LLMMessage(role="assistant", content=response.content or "")
        )
        for tc in response.tool_calls:
            self.agent.on_tool_call(tc["name"], tc.get("arguments", {}))
            args = tc.get("arguments", {})
            if isinstance(args, str):
                r = self.parser._parse_json_with_repair(args)
                args = r.data if r.success else {}
            self._execute_tool(tc["name"], args, call_id=tc.get("id"))

    def _execute_tool(self, name: str, arguments: dict, call_id: Optional[str]) -> None:
        try:
            result = self.agent.tools.dispatch(name, arguments)
            self.agent.on_tool_result(name, result)
            result_str = self._serialize_result(result)
            self._tool_error_count = 0
        except (ToolNotFoundError, ToolValidationError) as e:
            result_str = f"[Tool error] {e}"
            self._tool_error_count += 1
            logger.warning("Tool error (%s): %s", name, e)
        except Exception as e:
            result_str = f"[Tool error] Unexpected error in '{name}': {e}"
            self._tool_error_count += 1
            logger.exception("Unexpected tool error (%s)", name)

        if self._tool_error_count >= self.max_tool_errors:
            raise MaxIterationsError(
                f"Too many consecutive tool errors ({self._tool_error_count})"
            )

        self.agent.memory.add_tool_result(
            tool_call_id=call_id or f"call_{name}",
            name=name,
            content=result_str,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _extract_final_answer(self, text: str) -> Optional[str]:
        result = self.parser.extract_xml_tag(text, self.final_answer_tag)
        if result.success:
            return result.data
        tag_upper = self.final_answer_tag.upper()
        pattern = rf"{re.escape(tag_upper)}:\s*(.+)"
        import re
        m = re.search(pattern, text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _serialize_result(result: Any) -> str:
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            return str(result)


class ToolAgent(BaseAgent):
    """
    Ready-to-use agent that runs the ToolCallLoop.
    Instantiate with a config, optional LLM/memory/registry overrides.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loop = ToolCallLoop(self)

    def _run_iteration(self, run: AgentRun, **kwargs) -> Optional[str]:
        return self._loop.step(run)
