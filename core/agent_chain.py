"""
C111 — Agent Chain
Composable chain of agents where each agent's output is passed to the next.
Supports branching, map-reduce, and conditional routing between agents.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.agent_base import AgentRun, BaseAgent

logger = logging.getLogger(__name__)


@dataclass
class ChainStep:
    name: str
    agent: BaseAgent
    transform_input: Optional[Callable[[Any], str]] = None   # maps prev output -> str input
    transform_output: Optional[Callable[[AgentRun], Any]] = None  # maps AgentRun -> next input
    condition: Optional[Callable[[Any], bool]] = None  # skip if returns False
    on_error: str = "raise"  # 'raise' | 'skip' | 'stop'


@dataclass
class ChainResult:
    steps: list[tuple[str, AgentRun]] = field(default_factory=list)
    final_output: Any = None
    success: bool = True

    def get(self, step_name: str) -> Optional[AgentRun]:
        for name, run in self.steps:
            if name == step_name:
                return run
        return None

    def failed_steps(self) -> list[str]:
        return [name for name, run in self.steps if not run.success]


class AgentChain:
    """
    Sequential chain of agents.

    Usage::

        chain = (
            AgentChain("rag-pipeline")
            .then(retriever_agent, name="retrieve")
            .then(summarizer_agent, name="summarize")
            .then(critic_agent, name="critique",
                  condition=lambda out: len(out) > 100)
        )
        result = chain.run("What is RAG?")
    """

    def __init__(self, name: str = "chain"):
        self.name = name
        self._steps: list[ChainStep] = []

    def then(
        self,
        agent: BaseAgent,
        name: Optional[str] = None,
        transform_input: Optional[Callable[[Any], str]] = None,
        transform_output: Optional[Callable[[AgentRun], Any]] = None,
        condition: Optional[Callable[[Any], bool]] = None,
        on_error: str = "raise",
    ) -> "AgentChain":
        step_name = name or agent.config.name
        self._steps.append(ChainStep(
            name=step_name,
            agent=agent,
            transform_input=transform_input,
            transform_output=transform_output,
            condition=condition,
            on_error=on_error,
        ))
        return self

    def run(self, initial_input: Any) -> ChainResult:
        result = ChainResult()
        current: Any = initial_input
        for step in self._steps:
            if step.condition and not step.condition(current):
                logger.debug("[%s] Skipping step '%s'", self.name, step.name)
                continue
            agent_input = step.transform_input(current) if step.transform_input else str(current)
            try:
                run = step.agent.run(agent_input)
                result.steps.append((step.name, run))
                if not run.success:
                    result.success = False
                    logger.warning("[%s] Step '%s' failed: %s", self.name, step.name, run.error)
                    if step.on_error == "raise":
                        raise RuntimeError(f"Chain step '{step.name}' failed: {run.error}")
                    elif step.on_error == "stop":
                        break
                    continue
                current = step.transform_output(run) if step.transform_output else run.output
            except Exception as e:
                result.success = False
                logger.error("[%s] Step '%s' raised: %s", self.name, step.name, e)
                if step.on_error == "raise":
                    raise
                elif step.on_error == "stop":
                    break
        result.final_output = current
        return result

    def map_reduce(
        self,
        inputs: list[Any],
        map_agent: BaseAgent,
        reduce_agent: BaseAgent,
        reduce_transform: Optional[Callable[[list[AgentRun]], str]] = None,
    ) -> ChainResult:
        """
        Fan-out inputs to map_agent, then feed all outputs to reduce_agent.
        """
        map_runs: list[AgentRun] = []
        for inp in inputs:
            run = map_agent.run(str(inp))
            map_runs.append(run)
        if reduce_transform:
            reduce_input = reduce_transform(map_runs)
        else:
            reduce_input = "\n\n".join(
                r.output for r in map_runs if r.success and r.output
            )
        reduce_run = reduce_agent.run(reduce_input)
        chain_result = ChainResult(
            steps=[(f"map_{i}", r) for i, r in enumerate(map_runs)] + [("reduce", reduce_run)],
            final_output=reduce_run.output,
            success=all(r.success for r in map_runs) and reduce_run.success,
        )
        return chain_result

    def __len__(self) -> int:
        return len(self._steps)

    def __repr__(self) -> str:
        steps = " -> ".join(s.name for s in self._steps)
        return f"AgentChain({self.name!r}: {steps})"
