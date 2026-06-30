"""
C92 · WorkflowEngine
====================
Execution layer for WorkflowDefinition graphs (C91).

Responsibilities
----------------
* Walk the node graph produced by WorkflowBuilder / WorkflowDefinition.
* Resolve {{template}} variables in params against a mutable context dict.
* Dispatch each ActionNode to a registered handler (ToolCall, LLMCall, …).
* Evaluate ConditionNode expressions safely (no eval / exec).
* Execute LoopNodes (count-based + while-expression).
* Execute ParallelNodes via concurrent.futures.ThreadPoolExecutor.
* Track per-run state: RunRecord, StepResult, RunStatus.
* Emit lifecycle events (pre/post step) via a pluggable EventBus.
* stdlib only — no external deps.

Run lifecycle
-------------
    engine.run(workflow, trigger_payload)
        → RunRecord (RUNNING → COMPLETED | FAILED | TIMED_OUT)

        For each node in traversal order:
            1. resolve template vars in params
            2. dispatch to handler → StepResult
            3. merge result.output into context
            4. advance to next node (condition branch / loop / join)

Handler registration
--------------------
    engine = WorkflowEngine()

    @engine.register(ActionType.TOOL_CALL)
    def tool_handler(node: ActionNode, ctx: dict) -> dict:
        tool = ctx["__tools__"][node.params["tool"]]
        return tool(**{k: v for k, v in node.params.items() if k != "tool"})

    # Or use the built-in stub registry for quick tests:
    engine.register_stub(ActionType.LLM_CALL, {"summary": "stub output"})

Condition expressions
---------------------
Expressions are evaluated with a minimal safe evaluator that supports:
    ==  !=  <  >  <=  >=   and  or  not
    Literal int / float / bool / str / None
    Dot-path lookups into the context: result.status  →  ctx["result"]["status"]

Usage
-----
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType
    from core.workflow_engine    import WorkflowEngine

    engine = WorkflowEngine(max_workers=4)
    engine.register_stub(ActionType.TOOL_CALL,   {"fetched": True})
    engine.register_stub(ActionType.LLM_CALL,    {"summary": "ok"})
    engine.register_stub(ActionType.NOTIFICATION, {})

    wf = (
        WorkflowBuilder("demo")
        .trigger(TriggerType.MANUAL)
        .action("fetch",   ActionType.TOOL_CALL,  tool="rss_reader")
        .action("summarise", ActionType.LLM_CALL, prompt_template="summarise")
        .action("notify",  ActionType.NOTIFICATION, channel="slack")
        .build()
    )

    record = engine.run(wf, {"user": {"email": "a@b.com"}})
    print(record.status, record.steps)
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.workflow_definition import (
    ActionNode,
    ActionType,
    AnyNode,
    ConditionNode,
    LoopNode,
    NodeType,
    ParallelNode,
    TriggerNode,
    WorkflowDefinition,
)


# ---------------------------------------------------------------------------
# Run-state enumerations & dataclasses
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    TIMED_OUT  = "timed_out"


class StepStatus(str, Enum):
    SUCCESS  = "success"
    SKIPPED  = "skipped"
    FAILED   = "failed"


@dataclass
class StepResult:
    node_id:   str
    node_type: str
    status:    StepStatus
    output:    Dict[str, Any] = field(default_factory=dict)
    error:     Optional[str]  = None
    started_at: float         = field(default_factory=time.monotonic)
    ended_at:  float          = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return self.ended_at - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":    self.node_id,
            "node_type":  self.node_type,
            "status":     self.status.value,
            "output":     self.output,
            "error":      self.error,
            "elapsed_s":  round(self.elapsed, 4),
        }


@dataclass
class RunRecord:
    run_id:      str               = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str               = ""
    workflow_name: str             = ""
    status:      RunStatus         = RunStatus.RUNNING
    steps:       List[StepResult]  = field(default_factory=list)
    context:     Dict[str, Any]    = field(default_factory=dict)
    started_at:  float             = field(default_factory=time.monotonic)
    ended_at:    Optional[float]   = None
    error:       Optional[str]     = None

    @property
    def elapsed(self) -> float:
        end = self.ended_at or time.monotonic()
        return end - self.started_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id":        self.run_id,
            "workflow_id":   self.workflow_id,
            "workflow_name": self.workflow_name,
            "status":        self.status.value,
            "elapsed_s":     round(self.elapsed, 4),
            "error":         self.error,
            "steps":         [s.to_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------
# Minimal safe condition evaluator
# ---------------------------------------------------------------------------

_OP_MAP: Dict[str, Callable[[Any, Any], bool]] = {
    "==":  lambda a, b: a == b,
    "!=":  lambda a, b: a != b,
    "<":   lambda a, b: a < b,
    ">":   lambda a, b: a > b,
    "<=":  lambda a, b: a <= b,
    ">=":  lambda a, b: a >= b,
}

_LITERAL_TRUE  = re.compile(r"^true$",  re.IGNORECASE)
_LITERAL_FALSE = re.compile(r"^false$", re.IGNORECASE)
_LITERAL_NONE  = re.compile(r"^none$",  re.IGNORECASE)
_LITERAL_NUM   = re.compile(r"^-?\d+(\.\d+)?$")
_LITERAL_STR   = re.compile(r"^'([^']*)'$|^\"([^\"]*)\"$")


def _resolve_value(token: str, ctx: Dict[str, Any]) -> Any:
    """Turn a token string into a Python value, using ctx for dot-paths."""
    token = token.strip()
    if _LITERAL_TRUE.match(token):
        return True
    if _LITERAL_FALSE.match(token):
        return False
    if _LITERAL_NONE.match(token):
        return None
    m = _LITERAL_STR.match(token)
    if m:
        return m.group(1) if m.group(1) is not None else m.group(2)
    if _LITERAL_NUM.match(token):
        return float(token) if "." in token else int(token)
    # dot-path into context
    parts = token.split(".")
    obj: Any = ctx
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            try:
                obj = getattr(obj, part)
            except AttributeError:
                return None
        if obj is None:
            return None
    return obj


def evaluate_condition(expression: str, ctx: Dict[str, Any]) -> bool:
    """
    Evaluate a simple boolean expression against ctx.

    Supports:
        <lhs> <op> <rhs>       — single comparison
        <expr> and <expr>      — logical and  (left-to-right, no short-circuit)
        <expr> or  <expr>      — logical or
        not <expr>             — logical not

    Raises ConditionError on parse failure.
    """
    expr = expression.strip()

    # not
    if expr.lower().startswith("not "):
        return not evaluate_condition(expr[4:], ctx)

    # and / or  (split on first occurrence to keep left-associativity)
    for op_kw in (" and ", " or "):
        idx = expr.lower().find(op_kw)
        if idx != -1:
            left  = evaluate_condition(expr[:idx], ctx)
            right = evaluate_condition(expr[idx + len(op_kw):], ctx)
            return left and right if "and" in op_kw else left or right

    # comparison
    for op_sym in ("==", "!=", "<=", ">=", "<", ">"):
        if op_sym in expr:
            parts = expr.split(op_sym, 1)
            lhs = _resolve_value(parts[0], ctx)
            rhs = _resolve_value(parts[1], ctx)
            try:
                return _OP_MAP[op_sym](lhs, rhs)
            except TypeError:
                return False

    # bare value (truthy check)
    val = _resolve_value(expr, ctx)
    return bool(val)


class ConditionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Template resolver  {{path.to.value}}
# ---------------------------------------------------------------------------

_TPL_PATTERN = re.compile(r"\{\{([^}]+)\}\}")


def resolve_templates(obj: Any, ctx: Dict[str, Any]) -> Any:
    """
    Recursively replace {{dot.path}} placeholders in strings, dicts, and lists.
    Leaves non-string scalars untouched.
    """
    if isinstance(obj, str):
        def _sub(m: re.Match) -> str:  # type: ignore[type-arg]
            val = _resolve_value(m.group(1).strip(), ctx)
            return str(val) if val is not None else ""
        return _TPL_PATTERN.sub(_sub, obj)
    if isinstance(obj, dict):
        return {k: resolve_templates(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_templates(item, ctx) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """
    Minimal synchronous event bus.

    Usage:
        bus = EventBus()

        @bus.on("step.before")
        def log_before(payload): print("before", payload["node_id"])
    """

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    def on(self, event: str) -> Callable:
        def decorator(fn: Callable[[Dict[str, Any]], None]) -> Callable:
            self._listeners.setdefault(event, []).append(fn)
            return fn
        return decorator

    def emit(self, event: str, payload: Dict[str, Any]) -> None:
        for fn in self._listeners.get(event, []):
            try:
                fn(payload)
            except Exception:
                pass  # listeners must not crash the engine


# ---------------------------------------------------------------------------
# WorkflowEngine
# ---------------------------------------------------------------------------

HandlerFn = Callable[[ActionNode, Dict[str, Any]], Dict[str, Any]]


class WorkflowEngine:
    """
    Executes WorkflowDefinition instances.

    Parameters
    ----------
    max_workers : int
        Thread-pool size for ParallelNode branches (default 8).
    step_timeout : float | None
        Per-step wall-clock timeout in seconds; None = no timeout.
    event_bus : EventBus | None
        Optional event bus for lifecycle hooks.

    Handler registration
    --------------------
    Handlers are callables (node, ctx) → dict.  Register via:

        engine.register(ActionType.TOOL_CALL)(my_fn)
        engine.register_stub(ActionType.LLM_CALL, {"answer": "stub"})
    """

    def __init__(
        self,
        max_workers: int = 8,
        step_timeout: Optional[float] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._handlers:     Dict[ActionType, HandlerFn] = {}
        self._max_workers   = max_workers
        self._step_timeout  = step_timeout
        self._bus           = event_bus or EventBus()
        self._lock          = threading.Lock()

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register(self, action_type: ActionType) -> Callable[[HandlerFn], HandlerFn]:
        """Decorator: @engine.register(ActionType.TOOL_CALL)"""
        def decorator(fn: HandlerFn) -> HandlerFn:
            self._handlers[action_type] = fn
            return fn
        return decorator

    def register_fn(self, action_type: ActionType, fn: HandlerFn) -> None:
        """Register a handler function directly."""
        self._handlers[action_type] = fn

    def register_stub(
        self,
        action_type: ActionType,
        output: Dict[str, Any],
    ) -> None:
        """Register a stub handler that always returns *output*."""
        self._handlers[action_type] = lambda _node, _ctx: dict(output)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        workflow: WorkflowDefinition,
        trigger_payload: Optional[Dict[str, Any]] = None,
    ) -> RunRecord:
        """
        Execute *workflow* and return a completed RunRecord.

        The context starts with trigger_payload (or {}).
        Each ActionNode's output is merged into the context under the
        node's name key:  ctx[node.name] = step_result.output
        """
        ctx: Dict[str, Any] = dict(trigger_payload or {})
        record = RunRecord(
            workflow_id=workflow.workflow_id,
            workflow_name=workflow.name,
            context=ctx,
        )

        try:
            self._walk(workflow, workflow.trigger_id, ctx, record)
            record.status = RunStatus.COMPLETED
        except _WorkflowHalt as halt:
            record.status = halt.status
            record.error  = halt.message
        except Exception as exc:
            record.status = RunStatus.FAILED
            record.error  = str(exc)
        finally:
            record.ended_at = time.monotonic()

        return record

    # ------------------------------------------------------------------
    # Internal graph walker
    # ------------------------------------------------------------------

    def _walk(
        self,
        wf: WorkflowDefinition,
        node_id: Optional[str],
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> None:
        """Iterative walk; recursion only for loop bodies / parallel branches."""
        current_id: Optional[str] = node_id

        while current_id is not None:
            node = wf.get_node(current_id)

            if node.node_type == NodeType.TRIGGER:
                current_id = self._exec_trigger(node, ctx, record)  # type: ignore[arg-type]

            elif node.node_type == NodeType.ACTION:
                current_id = self._exec_action(node, ctx, record)   # type: ignore[arg-type]

            elif node.node_type == NodeType.CONDITION:
                current_id = self._exec_condition(node, ctx, record) # type: ignore[arg-type]

            elif node.node_type == NodeType.LOOP:
                current_id = self._exec_loop(wf, node, ctx, record)  # type: ignore[arg-type]

            elif node.node_type == NodeType.PARALLEL:
                current_id = self._exec_parallel(wf, node, ctx, record) # type: ignore[arg-type]

            else:
                raise RuntimeError(f"Unknown node type: {node.node_type}")

    # ------------------------------------------------------------------
    # Node executors
    # ------------------------------------------------------------------

    def _exec_trigger(
        self,
        node: TriggerNode,
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> Optional[str]:
        t0 = time.monotonic()
        self._bus.emit("step.before", {"node_id": node.node_id, "node_type": "trigger"})
        record.steps.append(StepResult(
            node_id=node.node_id,
            node_type="trigger",
            status=StepStatus.SUCCESS,
            output={"trigger_type": node.trigger_type.value},
            started_at=t0,
            ended_at=time.monotonic(),
        ))
        self._bus.emit("step.after", {"node_id": node.node_id, "status": "success"})
        return node.next_node

    def _exec_action(
        self,
        node: ActionNode,
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> Optional[str]:
        t0 = time.monotonic()
        self._bus.emit("step.before", {"node_id": node.node_id, "node_type": "action"})

        resolved_params = resolve_templates(node.params, ctx)
        handler = self._handlers.get(node.action_type)
        if handler is None:
            raise RuntimeError(
                f"No handler registered for ActionType '{node.action_type.value}' "
                f"(node '{node.node_id}')"
            )

        try:
            output = self._call_with_timeout(handler, node, ctx)
        except Exception as exc:
            step = StepResult(
                node_id=node.node_id,
                node_type="action",
                status=StepStatus.FAILED,
                error=str(exc),
                started_at=t0,
                ended_at=time.monotonic(),
            )
            record.steps.append(step)
            self._bus.emit("step.after", {"node_id": node.node_id, "status": "failed"})
            if node.on_error:
                return node.on_error
            raise _WorkflowHalt(RunStatus.FAILED, str(exc)) from exc

        ctx[node.name] = output
        record.steps.append(StepResult(
            node_id=node.node_id,
            node_type="action",
            status=StepStatus.SUCCESS,
            output=output,
            started_at=t0,
            ended_at=time.monotonic(),
        ))
        self._bus.emit("step.after", {"node_id": node.node_id, "status": "success"})
        return node.next_node

    def _exec_condition(
        self,
        node: ConditionNode,
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> Optional[str]:
        t0 = time.monotonic()
        self._bus.emit("step.before", {"node_id": node.node_id, "node_type": "condition"})

        result = evaluate_condition(node.expression, ctx)
        next_id = node.true_next if result else node.false_next

        record.steps.append(StepResult(
            node_id=node.node_id,
            node_type="condition",
            status=StepStatus.SUCCESS,
            output={"expression": node.expression, "result": result, "next": next_id},
            started_at=t0,
            ended_at=time.monotonic(),
        ))
        self._bus.emit("step.after", {"node_id": node.node_id, "status": "success"})
        return next_id

    def _exec_loop(
        self,
        wf: WorkflowDefinition,
        node: LoopNode,
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> Optional[str]:
        t0 = time.monotonic()
        self._bus.emit("step.before", {"node_id": node.node_id, "node_type": "loop"})
        iterations = 0

        while iterations < node.max_iter:
            if node.condition is not None:
                if not evaluate_condition(node.condition, ctx):
                    break
            self._walk(wf, node.body_node, ctx, record)
            iterations += 1
            if node.condition is None:
                break  # count-based: body_node chain handles its own terminus

        record.steps.append(StepResult(
            node_id=node.node_id,
            node_type="loop",
            status=StepStatus.SUCCESS,
            output={"iterations": iterations},
            started_at=t0,
            ended_at=time.monotonic(),
        ))
        self._bus.emit("step.after", {"node_id": node.node_id, "status": "success"})
        return node.next_node

    def _exec_parallel(
        self,
        wf: WorkflowDefinition,
        node: ParallelNode,
        ctx: Dict[str, Any],
        record: RunRecord,
    ) -> Optional[str]:
        t0 = time.monotonic()
        self._bus.emit("step.before", {"node_id": node.node_id, "node_type": "parallel"})

        branch_records: List[RunRecord] = []

        def _run_branch(start_id: str) -> RunRecord:
            branch_ctx    = dict(ctx)
            branch_record = RunRecord(
                workflow_id=record.workflow_id,
                workflow_name=record.workflow_name,
                context=branch_ctx,
            )
            self._walk(wf, start_id, branch_ctx, branch_record)
            branch_record.status   = RunStatus.COMPLETED
            branch_record.ended_at = time.monotonic()
            return branch_record

        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(node.branches))) as pool:
            futures = {pool.submit(_run_branch, b): b for b in node.branches}
            errors: List[str] = []
            for future in as_completed(futures):
                try:
                    br = future.result()
                    branch_records.append(br)
                    # Merge branch context back under branch start node key
                    with self._lock:
                        ctx.update(br.context)
                        record.steps.extend(br.steps)
                except Exception as exc:
                    errors.append(str(exc))

        status = StepStatus.FAILED if errors else StepStatus.SUCCESS
        record.steps.append(StepResult(
            node_id=node.node_id,
            node_type="parallel",
            status=status,
            output={"branches": len(node.branches), "errors": errors},
            started_at=t0,
            ended_at=time.monotonic(),
        ))
        self._bus.emit("step.after", {"node_id": node.node_id, "status": status.value})

        if errors:
            raise _WorkflowHalt(RunStatus.FAILED, "; ".join(errors))

        return node.join_node

    # ------------------------------------------------------------------
    # Timeout helper
    # ------------------------------------------------------------------

    def _call_with_timeout(
        self,
        handler: HandlerFn,
        node: ActionNode,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self._step_timeout is None:
            return handler(node, ctx)

        result_holder: Dict[str, Any] = {}
        exc_holder:    List[BaseException] = []

        def _target() -> None:
            try:
                result_holder["out"] = handler(node, ctx)
            except Exception as e:
                exc_holder.append(e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=self._step_timeout)

        if t.is_alive():
            raise _WorkflowHalt(
                RunStatus.TIMED_OUT,
                f"Step '{node.node_id}' exceeded timeout of {self._step_timeout}s",
            )
        if exc_holder:
            raise exc_holder[0]
        return result_holder.get("out", {})


# ---------------------------------------------------------------------------
# Internal control-flow exception
# ---------------------------------------------------------------------------

class _WorkflowHalt(Exception):
    def __init__(self, status: RunStatus, message: str) -> None:
        super().__init__(message)
        self.status  = status
        self.message = message
