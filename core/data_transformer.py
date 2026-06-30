"""
C95 · DataTransformer
=====================
Runtime execution engine for PipelineDefinition objects produced by C94
(pipeline_builder.py).

Responsibilities
----------------
* HandlerRegistry  — register, overwrite-guard, unregister, lookup.
* DataTransformer  — step-by-step execution of a PipelineDefinition,
                     dispatching each step to its registered handler.
* Error policies   — STOP / SKIP / RETRY with per-step max_retries.
* BranchStep       — fan-out to N named sub-pipelines, merge results back
                     into the payload under namespaced keys.
* StepResult       — per-step metrics (success, retries, duration_ms, etc.).
* ExecutionContext  — lightweight mutable context threaded through handlers.
* Built-in handlers for the nine StepTypes so pipelines work out of the box.
* stdlib-only, deterministic, test-friendly.

Usage
-----
    from core.pipeline_builder import PipelineBuilder
    from core.data_transformer import DataTransformer

    pipeline = (
        PipelineBuilder("user-etl")
        .extract("load_users", source="user_api")
        .transform("clean", handler="pii_stripper")
        .filter("active_only", expression="row['active'] == True")
        .load("save_users", destination="user_store", mode="upsert")
        .build()
    )

    dt = DataTransformer()
    dt.register("pii_stripper", my_pii_handler)

    payload, results = dt.execute(pipeline, {"rows": users})
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from core.pipeline_builder import (
        OnError,
        PipelineDefinition,
        PipelineStep,
        StepType,
    )
except ImportError:  # pragma: no cover — standalone usage
    PipelineDefinition = Any  # type: ignore
    PipelineStep = Any        # type: ignore
    StepType = Any            # type: ignore
    OnError = Any             # type: ignore


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Row     = Dict[str, Any]
Payload = Dict[str, Any]
Handler = Callable[["Payload", Dict[str, Any], "ExecutionContext"], "Payload"]


# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Metrics and outcome for a single executed step."""
    step_id:     str
    name:        str
    step_type:   str
    success:     bool
    skipped:     bool               = False
    retries:     int                = 0
    duration_ms: float              = 0.0
    error:       Optional[str]      = None
    output_keys: List[str]          = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id":     self.step_id,
            "name":        self.name,
            "step_type":   self.step_type,
            "success":     self.success,
            "skipped":     self.skipped,
            "retries":     self.retries,
            "duration_ms": round(self.duration_ms, 3),
            "error":       self.error,
            "output_keys": list(self.output_keys),
        }

    @property
    def failed(self) -> bool:
        return not self.success and not self.skipped


# ---------------------------------------------------------------------------
# ExecutionContext
# ---------------------------------------------------------------------------

@dataclass
class ExecutionContext:
    """
    Lightweight mutable context threaded through every handler call.

    Handlers can read / write ctx.state to share transient data between
    steps without polluting the main payload.
    """
    pipeline_id:        str
    pipeline_name:      str
    step_index:         int             = 0
    current_step_id:    str             = ""
    current_step_name:  str             = ""
    current_step_type:  str             = ""
    # Free-form scratchpad visible to all handlers in this execution
    state:              Dict[str, Any]  = field(default_factory=dict)
    # Accumulates per-step timings and counters; written by DataTransformer
    metrics:            Dict[str, Any]  = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HandlerRegistry
# ---------------------------------------------------------------------------

class HandlerRegistry:
    """
    Maps handler key strings to callable handler functions.

    Handlers have the signature::

        def my_handler(
            payload: Payload,
            params:  Dict[str, Any],
            ctx:     ExecutionContext,
        ) -> Payload: ...
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        key: str,
        handler: Handler,
        *,
        overwrite: bool = False,
    ) -> None:
        """Register *handler* under *key*.

        Raises KeyError if *key* is already registered and *overwrite* is
        False.
        """
        if not overwrite and key in self._handlers:
            raise KeyError(
                f"Handler '{key}' is already registered. "
                f"Pass overwrite=True to replace it."
            )
        self._handlers[key] = handler

    def unregister(self, key: str) -> None:
        """Remove a handler (no-op if absent)."""
        self._handlers.pop(key, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, key: str) -> Handler:
        """Return the handler for *key* or raise KeyError."""
        if key not in self._handlers:
            raise KeyError(
                f"Handler '{key}' is not registered. "
                f"Available: {self.keys()}"
            )
        return self._handlers[key]

    def has(self, key: str) -> bool:
        return key in self._handlers

    def keys(self) -> List[str]:
        return sorted(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:
        return f"HandlerRegistry(handlers={self.keys()})"


# ---------------------------------------------------------------------------
# DataTransformer
# ---------------------------------------------------------------------------

class DataTransformer:
    """
    Executes a PipelineDefinition step by step.

    Parameters
    ----------
    registry : HandlerRegistry, optional
        Provide a pre-populated registry, or leave blank to use a fresh one
        with built-in handlers pre-installed.

    Example
    -------
        dt = DataTransformer()
        dt.register("enrich", lambda payload, params, ctx: payload)
        payload, results = dt.execute(pipeline)
    """

    def __init__(self, registry: Optional[HandlerRegistry] = None) -> None:
        self.registry = registry or HandlerRegistry()
        self._install_defaults()

    # ------------------------------------------------------------------
    # Public registration proxy
    # ------------------------------------------------------------------

    def register(
        self,
        key: str,
        handler: Handler,
        *,
        overwrite: bool = False,
    ) -> None:
        """Proxy to HandlerRegistry.register."""
        self.registry.register(key, handler, overwrite=overwrite)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        pipeline: PipelineDefinition,
        payload: Optional[Payload] = None,
    ) -> Tuple[Payload, List[StepResult]]:
        """
        Run *pipeline* against *payload* and return
        ``(final_payload, [StepResult, ...])``

        Execution halts early if a step returns ``success=False``
        (i.e. on_error=STOP and step raised an exception).
        """
        data: Payload = copy.deepcopy(payload or {})
        data.setdefault("rows", [])

        ctx = ExecutionContext(
            pipeline_id=pipeline.pipeline_id,
            pipeline_name=pipeline.name,
        )
        results: List[StepResult] = []

        for idx, step in enumerate(pipeline.steps):
            ctx.step_index        = idx
            ctx.current_step_id   = step.step_id
            ctx.current_step_name = step.name
            ctx.current_step_type = _sval(step.step_type)

            result = self._run_step(step, data, ctx)
            results.append(result)

            if result.failed:
                break  # STOP policy — abort pipeline

        ctx.metrics["total_steps"]     = len(pipeline.steps)
        ctx.metrics["executed_steps"]  = len(results)
        ctx.metrics["step_results"]    = [r.to_dict() for r in results]
        ctx.metrics["success"]         = all(r.success for r in results)

        return data, results

    # ------------------------------------------------------------------
    # Internal step runner
    # ------------------------------------------------------------------

    def _run_step(
        self,
        step: PipelineStep,
        data: Payload,
        ctx: ExecutionContext,
    ) -> StepResult:
        start    = time.perf_counter()
        retries  = 0

        while True:
            try:
                if _sval(step.step_type) == StepType.BRANCH.value:
                    data = self._exec_branch(step, data, ctx)
                else:
                    handler = self.registry.get(step.handler)
                    data    = handler(data, step.params, ctx)

                duration = (time.perf_counter() - start) * 1000.0
                return StepResult(
                    step.step_id, step.name, _sval(step.step_type),
                    success=True,
                    retries=retries,
                    duration_ms=duration,
                    output_keys=sorted(data.keys()),
                )

            except Exception as exc:
                error_msg = str(exc)
                policy    = _sval(step.on_error)

                if policy == OnError.RETRY.value and retries < step.max_retries:
                    retries += 1
                    continue  # retry the step

                duration = (time.perf_counter() - start) * 1000.0

                if policy == OnError.SKIP.value:
                    return StepResult(
                        step.step_id, step.name, _sval(step.step_type),
                        success=True,   # pipeline continues
                        skipped=True,
                        retries=retries,
                        duration_ms=duration,
                        error=error_msg,
                        output_keys=sorted(data.keys()),
                    )

                # STOP — fail the pipeline
                return StepResult(
                    step.step_id, step.name, _sval(step.step_type),
                    success=False,
                    retries=retries,
                    duration_ms=duration,
                    error=error_msg,
                    output_keys=sorted(data.keys()),
                )

    # ------------------------------------------------------------------
    # Branch execution
    # ------------------------------------------------------------------

    def _exec_branch(
        self,
        step: PipelineStep,
        data: Payload,
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Fan-out: run each named sub-pipeline on a deep copy of *data*.
        Merge results back under ``data["branch::<step.name>"][<branch_name>"]``.
        """
        branch_results: Dict[str, Payload] = {}

        for branch_name, sub_pipeline in step.branches.items():
            branch_payload, _ = self.execute(sub_pipeline, copy.deepcopy(data))
            branch_results[branch_name] = branch_payload

        data[f"branch::{step.name}"] = branch_results
        return data

    # ------------------------------------------------------------------
    # Built-in handlers
    # ------------------------------------------------------------------

    def _install_defaults(self) -> None:
        defaults = {
            "default_extractor":  self._h_extractor,
            "default_loader":     self._h_loader,
            "expression_filter":  self._h_filter,
            "schema_validator":   self._h_validator,
            "branch_router":      self._h_branch_noop,
            "key_joiner":         self._h_key_joiner,
            "sum_reducer":        self._h_sum_reducer,
            "passthrough":        self._h_passthrough,
        }
        for key, fn in defaults.items():
            self.registry.register(key, fn, overwrite=True)

    # --- extract ---

    def _h_extractor(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Populate data['rows'] from params['rows'] (inline test data) or
        leave empty and record params['source'] for the caller to hydrate.
        """
        rows = params.get("rows")
        if rows is not None:
            data["rows"] = copy.deepcopy(rows)
        else:
            data.setdefault("rows", [])
            data["source"] = params.get("source")
        return data

    # --- load ---

    def _h_loader(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Record a load event in data['loads'] (list of dicts).
        Caller can inspect these to drive actual I/O.
        """
        destination = params.get("destination") or params.get("target", "unknown")
        data.setdefault("loads", []).append({
            "destination": destination,
            "mode":        params.get("mode", "append"),
            "rows":        copy.deepcopy(data.get("rows", [])),
        })
        return data

    # --- filter ---

    def _h_filter(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Keep rows where *expression* evaluates to True.

        Expression may use ``row`` (current row dict) and ``data`` (payload).
        Example: ``"row['active'] == True"``
        """
        expression = params.get("expression", "").strip()
        if not expression or expression.lower() == "true":
            return data

        rows     = data.get("rows", [])
        filtered = []
        safe_builtins = {
            "True": True, "False": False, "None": None,
            "len": len, "str": str, "int": int, "float": float,
        }
        for row in rows:
            env = {"row": row, "data": data, **safe_builtins}
            try:
                if eval(expression, {"__builtins__": {}}, env):  # noqa: S307
                    filtered.append(row)
            except Exception:
                pass  # malformed expression — skip row
        data["rows"] = filtered
        return data

    # --- validate ---

    def _h_validator(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Record schema name in data['validated_schemas'].
        Plug in C30 DataValidator for real constraint checking.
        """
        schema = params.get("schema") or params.get("input_schema")
        if schema:
            data.setdefault("validated_schemas", []).append(schema)
        return data

    # --- branch no-op (branch is handled by _exec_branch directly) ---

    def _h_branch_noop(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        return data

    # --- key joiner ---

    def _h_key_joiner(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """
        Left-join data['rows'] with params['right_rows'] on params['on_key'].
        Right-side fields are prefixed with ``right_``.
        """
        right   = params.get("right_rows", [])
        on_key  = params.get("on_key", "id")
        index   = {r.get(on_key): r for r in right if on_key in r}
        merged  = []
        for row in data.get("rows", []):
            match = index.get(row.get(on_key), {})
            merged.append({
                **row,
                **{f"right_{k}": v for k, v in match.items() if k != on_key},
            })
        data["rows"] = merged
        return data

    # --- sum reducer ---

    def _h_sum_reducer(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """Sum numeric values of params['field'] across all rows."""
        src_field  = params.get("field", "value")
        out_key    = params.get("output", f"{src_field}_sum")
        data[out_key] = sum(
            (row.get(src_field) or 0) for row in data.get("rows", [])
        )
        return data

    # --- passthrough ---

    def _h_passthrough(
        self,
        data: Payload,
        params: Dict[str, Any],
        ctx: ExecutionContext,
    ) -> Payload:
        """Identity handler — returns data unchanged. Useful for testing."""
        return data

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"DataTransformer(handlers={len(self.registry)}, "
            f"keys={self.registry.keys()})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sval(enum_or_str: Any) -> str:
    """Return .value if enum, else str — safe for both import modes."""
    return enum_or_str.value if hasattr(enum_or_str, "value") else str(enum_or_str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DataTransformer",
    "ExecutionContext",
    "HandlerRegistry",
    "StepResult",
]
