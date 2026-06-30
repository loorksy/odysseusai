"""
C92 · WorkflowExecutor
======================
DAG execution engine for WorkflowDefinition (C91).

Design principles
-----------------
* Async-first — all execution paths are coroutines.
* Context-passing — a mutable ExecutionContext travels through the graph,
  carrying the shared variable namespace and step results.
* Pluggable dispatch — ActionNode execution is delegated to a registered
  ActionDispatcher callable; the executor itself stays pure.
* Structured concurrency — ParallelNode uses asyncio.gather with
  independent sub-contexts; results merged into parent context on join.
* Loop guard — LoopNode enforces max_iter and honours while-conditions
  evaluated against the live context.
* Audit hook — every step emits a StepRecord (start / end / error) so
  AuditLogger (C71) can consume them without coupling.
* stdlib only — no external deps.

Usage
-----
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType
    from core.workflow_executor import WorkflowExecutor, ExecutionContext

    async def my_dispatcher(node, ctx):
        # call actual tools / LLM / etc.
        return {"status": "ok", "data": "result"}

    wf  = WorkflowBuilder("demo").trigger(TriggerType.MANUAL)\
                                  .action("greet", ActionType.TOOL_CALL, tool="greeter")\
                                  .build()

    ctx = ExecutionContext(trigger_payload={"user": "Shadow"})
    ex  = WorkflowExecutor(dispatcher=my_dispatcher)
    result = await ex.execute(wf, ctx)
    print(result.status, result.variables)
"""

from __future__ import annotations

import asyncio
import copy
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.workflow_definition import (
    ActionNode,
    AnyNode,
    ConditionNode,
    LoopNode,
    NodeType,
    ParallelNode,
    TriggerNode,
    WorkflowDefinition,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    STARTED   = "started"
    COMPLETED = "completed"
    SKIPPED   = "skipped"
    FAILED    = "failed"


# ---------------------------------------------------------------------------
# Execution context  —  the shared variable namespace
# ---------------------------------------------------------------------------

@dataclass
class ExecutionContext:
    """
    Mutable state bag that travels through the workflow graph.

    Attributes
    ----------
    execution_id      : unique run identifier
    trigger_payload   : data that initiated the workflow
    variables         : mutable namespace (node results written here)
    step_results      : ordered list of StepRecord objects
    _cancel_requested : internal flag; set via .cancel()
    """
    trigger_payload:   Dict[str, Any]  = field(default_factory=dict)
    variables:         Dict[str, Any]  = field(default_factory=dict)
    execution_id:      str             = field(default_factory=lambda: str(uuid.uuid4()))
    step_results:      List["StepRecord"] = field(default_factory=list)
    _cancel_requested: bool            = field(default=False, repr=False)

    def set(self, key: str, value: Any) -> None:
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.variables.get(key, default)

    def cancel(self) -> None:
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_requested

    def fork(self) -> "ExecutionContext":
        """Return a shallow-copy sub-context for parallel branches."""
        child = ExecutionContext(
            trigger_payload=copy.deepcopy(self.trigger_payload),
            variables=copy.deepcopy(self.variables),
            execution_id=self.execution_id,
        )
        return child

    def merge(self, child: "ExecutionContext", branch_key: str) -> None:
        """Merge a branch sub-context result back under a namespaced key."""
        self.variables[branch_key] = copy.deepcopy(child.variables)
        self.step_results.extend(child.step_results)


# ---------------------------------------------------------------------------
# Step record  —  audit trail entry
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    execution_id: str
    node_id:      str
    node_type:    str
    status:       StepStatus
    started_at:   float   = field(default_factory=time.time)
    ended_at:     Optional[float] = None
    result:       Optional[Any]   = None
    error:        Optional[str]   = None

    def finish(self, result: Any = None) -> None:
        self.ended_at = time.time()
        self.status   = StepStatus.COMPLETED
        self.result   = result

    def fail(self, exc: Exception) -> None:
        self.ended_at = time.time()
        self.status   = StepStatus.FAILED
        self.error    = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    @property
    def elapsed(self) -> Optional[float]:
        if self.ended_at is not None:
            return self.ended_at - self.started_at
        return None


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------

@dataclass
class ExecutionResult:
    execution_id: str
    workflow_id:  str
    workflow_name: str
    status:       ExecutionStatus
    variables:    Dict[str, Any]
    step_results: List[StepRecord]
    started_at:   float
    ended_at:     Optional[float] = None
    error:        Optional[str]   = None

    @property
    def elapsed(self) -> Optional[float]:
        if self.ended_at is not None:
            return self.ended_at - self.started_at
        return None


# ---------------------------------------------------------------------------
# ActionDispatcher type alias
# ---------------------------------------------------------------------------

# Signature: async def dispatcher(node: ActionNode, ctx: ExecutionContext) -> Any
ActionDispatcher = Callable[[ActionNode, ExecutionContext], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Expression evaluator  —  safe eval against context variables
# ---------------------------------------------------------------------------

class ExpressionEvaluator:
    """
    Evaluate simple boolean/value expressions against ExecutionContext.variables.

    Syntax: Python expressions with {{key}} mustache refs resolved first.
    Only safe builtins are exposed.

    Example
    -------
        eval_ctx = ExpressionEvaluator(ctx)
        result   = eval_ctx.evaluate("{{step1.status}} == 'ok'")
    """

    _SAFE_GLOBALS: Dict[str, Any] = {
        "__builtins__": {},
        "True": True, "False": False, "None": None,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict,
    }

    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def _resolve_mustache(self, expression: str) -> str:
        """Replace {{key.sub}} refs with repr() of the resolved value."""
        import re
        def replacer(m: re.Match) -> str:
            path = m.group(1).strip().split(".")
            val: Any = self._ctx.variables
            for part in path:
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = getattr(val, part, None)
                if val is None:
                    break
            return repr(val)
        return re.sub(r"\{\{([^}]+)\}\}", replacer, expression)

    def evaluate(self, expression: str) -> Any:
        resolved = self._resolve_mustache(expression)
        try:
            return eval(resolved, self._SAFE_GLOBALS, {})
        except Exception as exc:
            raise ValueError(f"Expression evaluation failed: {expression!r} → {resolved!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# WorkflowExecutor
# ---------------------------------------------------------------------------

class WorkflowExecutor:
    """
    Async DAG executor for WorkflowDefinition.

    Parameters
    ----------
    dispatcher       : async callable(ActionNode, ExecutionContext) -> Any
    on_step_start    : optional async hook called before each node
    on_step_complete : optional async hook called after each node
    max_parallel     : max concurrent branches in ParallelNode (0 = unlimited)
    """

    def __init__(
        self,
        dispatcher: ActionDispatcher,
        *,
        on_step_start:    Optional[Callable[[StepRecord, ExecutionContext], Awaitable[None]]] = None,
        on_step_complete: Optional[Callable[[StepRecord, ExecutionContext], Awaitable[None]]] = None,
        max_parallel: int = 0,
    ) -> None:
        self._dispatcher       = dispatcher
        self._on_step_start    = on_step_start
        self._on_step_complete = on_step_complete
        self._max_parallel     = max_parallel
        self._semaphore: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(max_parallel) if max_parallel > 0 else None
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        workflow: WorkflowDefinition,
        ctx: Optional[ExecutionContext] = None,
    ) -> ExecutionResult:
        """Execute the workflow and return an ExecutionResult."""
        if ctx is None:
            ctx = ExecutionContext()

        started_at = time.time()
        result = ExecutionResult(
            execution_id=ctx.execution_id,
            workflow_id=workflow.workflow_id,
            workflow_name=workflow.name,
            status=ExecutionStatus.RUNNING,
            variables=ctx.variables,
            step_results=ctx.step_results,
            started_at=started_at,
        )

        try:
            await self._run_node(workflow, workflow.trigger_id, ctx)
            result.status = (
                ExecutionStatus.CANCELLED if ctx.is_cancelled
                else ExecutionStatus.COMPLETED
            )
        except Exception as exc:
            result.status = ExecutionStatus.FAILED
            result.error  = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        finally:
            result.ended_at  = time.time()
            result.variables = ctx.variables
            result.step_results = ctx.step_results

        return result

    # ------------------------------------------------------------------
    # Node dispatch
    # ------------------------------------------------------------------

    async def _run_node(
        self,
        workflow: WorkflowDefinition,
        node_id: Optional[str],
        ctx: ExecutionContext,
    ) -> None:
        """Recursively execute nodes starting at node_id."""
        while node_id is not None:
            if ctx.is_cancelled:
                return

            node = workflow.get_node(node_id)

            if node.node_type == NodeType.TRIGGER:
                node_id = await self._exec_trigger(workflow, node, ctx)  # type: ignore[arg-type]
            elif node.node_type == NodeType.ACTION:
                node_id = await self._exec_action(workflow, node, ctx)   # type: ignore[arg-type]
            elif node.node_type == NodeType.CONDITION:
                node_id = await self._exec_condition(workflow, node, ctx) # type: ignore[arg-type]
            elif node.node_type == NodeType.LOOP:
                node_id = await self._exec_loop(workflow, node, ctx)      # type: ignore[arg-type]
            elif node.node_type == NodeType.PARALLEL:
                node_id = await self._exec_parallel(workflow, node, ctx)  # type: ignore[arg-type]
            else:
                raise RuntimeError(f"Unknown node type: {node.node_type}")

    # ------------------------------------------------------------------
    # Individual node handlers
    # ------------------------------------------------------------------

    async def _exec_trigger(
        self,
        workflow: WorkflowDefinition,
        node: TriggerNode,
        ctx: ExecutionContext,
    ) -> Optional[str]:
        rec = StepRecord(
            execution_id=ctx.execution_id,
            node_id=node.node_id,
            node_type=node.node_type.value,
            status=StepStatus.STARTED,
        )
        await self._fire_start(rec, ctx)
        ctx.set("trigger", {"type": node.trigger_type.value, "config": node.config})
        rec.finish({"trigger_type": node.trigger_type.value})
        ctx.step_results.append(rec)
        await self._fire_complete(rec, ctx)
        return node.next_node

    async def _exec_action(
        self,
        workflow: WorkflowDefinition,
        node: ActionNode,
        ctx: ExecutionContext,
    ) -> Optional[str]:
        rec = StepRecord(
            execution_id=ctx.execution_id,
            node_id=node.node_id,
            node_type=node.node_type.value,
            status=StepStatus.STARTED,
        )
        await self._fire_start(rec, ctx)
        try:
            if self._semaphore:
                async with self._semaphore:
                    result = await self._dispatcher(node, ctx)
            else:
                result = await self._dispatcher(node, ctx)
            ctx.set(node.name, result)
            rec.finish(result)
            ctx.step_results.append(rec)
            await self._fire_complete(rec, ctx)
            return node.next_node
        except Exception as exc:
            rec.fail(exc)
            ctx.step_results.append(rec)
            await self._fire_complete(rec, ctx)
            if node.on_error:
                return node.on_error
            raise

    async def _exec_condition(
        self,
        workflow: WorkflowDefinition,
        node: ConditionNode,
        ctx: ExecutionContext,
    ) -> Optional[str]:
        rec = StepRecord(
            execution_id=ctx.execution_id,
            node_id=node.node_id,
            node_type=node.node_type.value,
            status=StepStatus.STARTED,
        )
        await self._fire_start(rec, ctx)
        evaluator = ExpressionEvaluator(ctx)
        result    = bool(evaluator.evaluate(node.expression))
        next_node = node.true_next if result else node.false_next
        rec.finish({"expression": node.expression, "result": result, "branch": next_node})
        ctx.set(f"_cond_{node.node_id}", result)
        ctx.step_results.append(rec)
        await self._fire_complete(rec, ctx)
        return next_node

    async def _exec_loop(
        self,
        workflow: WorkflowDefinition,
        node: LoopNode,
        ctx: ExecutionContext,
    ) -> Optional[str]:
        rec = StepRecord(
            execution_id=ctx.execution_id,
            node_id=node.node_id,
            node_type=node.node_type.value,
            status=StepStatus.STARTED,
        )
        await self._fire_start(rec, ctx)
        evaluator  = ExpressionEvaluator(ctx)
        iterations = 0

        while iterations < node.max_iter:
            if ctx.is_cancelled:
                break
            if node.condition is not None:
                keep_going = bool(evaluator.evaluate(node.condition))
                if not keep_going:
                    break
            await self._run_node(workflow, node.body_node, ctx)
            iterations += 1

        rec.finish({"iterations": iterations})
        ctx.set(f"_loop_{node.node_id}_iterations", iterations)
        ctx.step_results.append(rec)
        await self._fire_complete(rec, ctx)
        return node.next_node

    async def _exec_parallel(
        self,
        workflow: WorkflowDefinition,
        node: ParallelNode,
        ctx: ExecutionContext,
    ) -> Optional[str]:
        rec = StepRecord(
            execution_id=ctx.execution_id,
            node_id=node.node_id,
            node_type=node.node_type.value,
            status=StepStatus.STARTED,
        )
        await self._fire_start(rec, ctx)

        branch_ctxs = [ctx.fork() for _ in node.branches]

        async def run_branch(branch_start: str, bctx: ExecutionContext) -> None:
            await self._run_node(workflow, branch_start, bctx)

        await asyncio.gather(
            *[run_branch(start, bctx)
              for start, bctx in zip(node.branches, branch_ctxs)],
            return_exceptions=False,
        )

        for i, (bstart, bctx) in enumerate(zip(node.branches, branch_ctxs)):
            ctx.merge(bctx, branch_key=f"_branch_{node.node_id}_{i}_{bstart}")

        rec.finish({"branches": node.branches})
        ctx.step_results.append(rec)
        await self._fire_complete(rec, ctx)
        return node.join_node

    # ------------------------------------------------------------------
    # Hook helpers
    # ------------------------------------------------------------------

    async def _fire_start(self, rec: StepRecord, ctx: ExecutionContext) -> None:
        if self._on_step_start:
            try:
                await self._on_step_start(rec, ctx)
            except Exception:
                pass  # hooks must not crash the execution

    async def _fire_complete(self, rec: StepRecord, ctx: ExecutionContext) -> None:
        if self._on_step_complete:
            try:
                await self._on_step_complete(rec, ctx)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Default no-op dispatcher  —  useful for dry-run / testing
# ---------------------------------------------------------------------------

async def noop_dispatcher(node: ActionNode, ctx: ExecutionContext) -> Dict[str, Any]:
    """Passthrough dispatcher that echoes the node params without side effects."""
    return {
        "node_id":     node.node_id,
        "action_type": node.action_type.value,
        "params":      node.params,
        "dry_run":     True,
    }


# ---------------------------------------------------------------------------
# Convenience: run_workflow  —  fire-and-forget helper
# ---------------------------------------------------------------------------

async def run_workflow(
    workflow: WorkflowDefinition,
    dispatcher: ActionDispatcher = noop_dispatcher,
    trigger_payload: Optional[Dict[str, Any]] = None,
    *,
    on_step_start:    Optional[Callable] = None,
    on_step_complete: Optional[Callable] = None,
) -> ExecutionResult:
    """
    One-shot helper: create executor + context, run, return result.

    Example
    -------
        result = await run_workflow(my_wf, my_dispatcher, {"user": "Shadow"})
        print(result.status, result.elapsed)
    """
    ctx = ExecutionContext(trigger_payload=trigger_payload or {})
    executor = WorkflowExecutor(
        dispatcher=dispatcher,
        on_step_start=on_step_start,
        on_step_complete=on_step_complete,
    )
    return await executor.execute(workflow, ctx)
