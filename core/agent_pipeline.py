"""
C95 — Agent Pipeline
Chains multiple agents or callables in sequence, with optional
branching, parallel execution, and result passing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    step_name: str
    output: Any
    success: bool
    error: Optional[str] = None
    elapsed: float = 0.0


@dataclass
class PipelineResult:
    steps: list[StepResult] = field(default_factory=list)
    final_output: Any = None
    success: bool = True

    def last(self) -> Optional[StepResult]:
        return self.steps[-1] if self.steps else None

    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if not s.success]


StepCallable = Callable[[Any], Any]


@dataclass
class PipelineStep:
    name: str
    fn: StepCallable
    condition: Optional[Callable[[Any], bool]] = None
    on_error: str = "raise"  # 'raise' | 'skip' | 'stop'


class AgentPipeline:
    """
    Sequential pipeline of steps. Each step receives the output
    of the previous step as its input.
    """

    def __init__(self, name: str = "pipeline"):
        self.name = name
        self._steps: list[PipelineStep] = []

    def step(
        self,
        name: str,
        fn: StepCallable,
        condition: Optional[Callable[[Any], bool]] = None,
        on_error: str = "raise",
    ) -> "AgentPipeline":
        self._steps.append(PipelineStep(name=name, fn=fn, condition=condition, on_error=on_error))
        return self

    def parallel(
        self,
        name: str,
        fns: list[tuple[str, StepCallable]],
        merge: Optional[Callable[[list[Any]], Any]] = None,
        on_error: str = "skip",
    ) -> "AgentPipeline":
        _merge = merge or (lambda results: results)

        async def _run_parallel(input_data: Any) -> Any:
            tasks = [asyncio.to_thread(fn, input_data) for _, fn in fns]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            valid = []
            for (step_name, _), outcome in zip(fns, outcomes):
                if isinstance(outcome, Exception):
                    logger.warning("Parallel step '%s' failed: %s", step_name, outcome)
                    if on_error == "raise":
                        raise outcome
                else:
                    valid.append(outcome)
            return _merge(valid)

        def _sync_parallel(input_data: Any) -> Any:
            return asyncio.run(_run_parallel(input_data))

        self._steps.append(PipelineStep(name=name, fn=_sync_parallel, on_error=on_error))
        return self

    def run(self, initial_input: Any = None) -> PipelineResult:
        result = PipelineResult()
        current = initial_input
        for step in self._steps:
            if step.condition and not step.condition(current):
                logger.debug("[%s] Skipping step '%s' (condition=False)", self.name, step.name)
                continue
            start = time.time()
            try:
                output = step.fn(current)
                if hasattr(output, "output"):
                    output = output.output
                elapsed = time.time() - start
                step_result = StepResult(step_name=step.name, output=output, success=True, elapsed=elapsed)
                result.steps.append(step_result)
                current = output
            except Exception as e:
                elapsed = time.time() - start
                step_result = StepResult(step_name=step.name, output=None, success=False, error=str(e), elapsed=elapsed)
                result.steps.append(step_result)
                result.success = False
                logger.error("[%s] Step '%s' failed: %s", self.name, step.name, e)
                if step.on_error == "raise":
                    raise
                elif step.on_error == "stop":
                    break
        result.final_output = current
        return result

    async def arun(self, initial_input: Any = None) -> PipelineResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.run(initial_input))
