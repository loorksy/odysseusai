"""
C94 · PipelineBuilder
=====================
Composable ETL step chain for the ShadowRealm data pipeline system.

Design principles
-----------------
* Pipeline = ordered list of Steps; each Step transforms a PipelinePayload.
* Immutable Step definitions — a Step is a pure descriptor; execution lives
  in C95 (DataTransformer) and C96 (PipelineScheduler).
* Fluent builder DSL — chain .extract() / .transform() / .load() / .filter()
  / .branch() calls then call .build().
* Schema-aware — optional input/output schema tags per step for validation
  hooks (C30 DataValidator can plug in).
* Branching — a BranchStep fans out to N named sub-pipelines and merges
  results back into the payload under namespaced keys.
* Full serialise/deserialise round-trip (dict ⟺ PipelineDefinition).
* stdlib only — no external deps.

Usage
-----
    from core.pipeline_builder import PipelineBuilder, StepType

    pipeline = (
        PipelineBuilder("user-etl")
        .description("Extract users, clean, load to store")
        .extract("load_users",   source="user_api",   format="json")
        .transform("clean",      handler="strip_pii")
        .filter("active_only",   expression="{{row.active}} == True")
        .load("save_users",      destination="user_store", mode="upsert")
        .build()
    )

    payload = pipeline.to_dict()
    p2      = PipelineDefinition.from_dict(payload)
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    EXTRACT   = "extract"    # pull data from a source
    TRANSFORM = "transform"  # mutate / enrich rows
    FILTER    = "filter"     # drop rows not matching expression
    LOAD      = "load"       # write data to a destination
    BRANCH    = "branch"     # fan-out to N sub-pipelines
    REDUCE    = "reduce"     # aggregate rows into summary
    JOIN      = "join"       # merge two streams by key
    VALIDATE  = "validate"   # assert schema / constraints
    CUSTOM    = "custom"     # user-defined handler key


class PipelineStatus(str, Enum):
    DRAFT    = "draft"
    ACTIVE   = "active"
    PAUSED   = "paused"
    ARCHIVED = "archived"


class OnError(str, Enum):
    STOP     = "stop"     # halt pipeline on step failure
    SKIP     = "skip"     # skip failed rows, continue
    RETRY    = "retry"    # retry step up to max_retries


# ---------------------------------------------------------------------------
# Step dataclass
# ---------------------------------------------------------------------------

@dataclass
class PipelineStep:
    """
    Immutable descriptor for a single pipeline step.

    Attributes
    ----------
    step_id       : unique identifier (auto-generated if not provided)
    name          : human-readable label
    step_type     : StepType enum
    handler       : key used by DataTransformer to look up the executor fn
    params        : arbitrary config passed to the handler
    on_error      : error policy for this step
    max_retries   : used when on_error=RETRY
    input_schema  : optional schema key for pre-step validation
    output_schema : optional schema key for post-step validation
    tags          : free-form labels
    """
    step_id:       str
    name:          str
    step_type:     StepType
    handler:       str
    params:        Dict[str, Any]   = field(default_factory=dict)
    on_error:      OnError          = OnError.STOP
    max_retries:   int              = 0
    input_schema:  Optional[str]    = None
    output_schema: Optional[str]    = None
    tags:          List[str]        = field(default_factory=list)
    # BranchStep only
    branches:      Dict[str, "PipelineDefinition"] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id":       self.step_id,
            "name":          self.name,
            "step_type":     self.step_type.value,
            "handler":       self.handler,
            "params":        copy.deepcopy(self.params),
            "on_error":      self.on_error.value,
            "max_retries":   self.max_retries,
            "input_schema":  self.input_schema,
            "output_schema": self.output_schema,
            "tags":          list(self.tags),
            "branches":      {k: v.to_dict() for k, v in self.branches.items()},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PipelineStep":
        branches = {
            k: PipelineDefinition.from_dict(v)
            for k, v in d.get("branches", {}).items()
        }
        return cls(
            step_id=d["step_id"],
            name=d["name"],
            step_type=StepType(d["step_type"]),
            handler=d["handler"],
            params=d.get("params", {}),
            on_error=OnError(d.get("on_error", OnError.STOP.value)),
            max_retries=d.get("max_retries", 0),
            input_schema=d.get("input_schema"),
            output_schema=d.get("output_schema"),
            tags=d.get("tags", []),
            branches=branches,
        )


# ---------------------------------------------------------------------------
# PipelineDefinition
# ---------------------------------------------------------------------------

class PipelineDefinition:
    """
    Ordered list of PipelineSteps with metadata.

    Attributes
    ----------
    pipeline_id   : unique identifier
    name          : human-readable name
    description   : optional description
    version       : monotonic version counter
    status        : PipelineStatus
    steps         : ordered list of PipelineStep
    tags          : free-form labels
    metadata      : arbitrary key/value store
    """

    def __init__(
        self,
        name: str,
        steps: List[PipelineStep],
        *,
        pipeline_id: Optional[str] = None,
        description: str = "",
        version: int = 1,
        status: PipelineStatus = PipelineStatus.DRAFT,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.pipeline_id  = pipeline_id or str(uuid.uuid4())
        self.name         = name
        self.description  = description
        self.version      = version
        self.status       = status
        self.steps        = steps
        self.tags         = tags or []
        self.metadata     = metadata or {}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pipeline_id":  self.pipeline_id,
            "name":         self.name,
            "description":  self.description,
            "version":      self.version,
            "status":       self.status.value,
            "steps":        [s.to_dict() for s in self.steps],
            "tags":         list(self.tags),
            "metadata":     copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PipelineDefinition":
        return cls(
            name=d["name"],
            steps=[PipelineStep.from_dict(s) for s in d.get("steps", [])],
            pipeline_id=d.get("pipeline_id"),
            description=d.get("description", ""),
            version=d.get("version", 1),
            status=PipelineStatus(d.get("status", PipelineStatus.DRAFT.value)),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_step(self, step_id: str) -> PipelineStep:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(f"Step '{step_id}' not found in pipeline '{self.name}'")

    def steps_of_type(self, step_type: StepType) -> List[PipelineStep]:
        return [s for s in self.steps if s.step_type == step_type]

    def validate(self) -> List[str]:
        """
        Return list of validation error strings (empty = valid).
        Checks: non-empty steps, unique step IDs, branch sub-pipeline validity.
        """
        errors: List[str] = []
        if not self.steps:
            errors.append("Pipeline has no steps")
            return errors
        seen_ids: set = set()
        for step in self.steps:
            if step.step_id in seen_ids:
                errors.append(f"Duplicate step_id: '{step.step_id}'")
            seen_ids.add(step.step_id)
            if step.step_type == StepType.BRANCH:
                for branch_name, sub_pipeline in step.branches.items():
                    sub_errors = sub_pipeline.validate()
                    for e in sub_errors:
                        errors.append(f"Branch '{branch_name}': {e}")
        return errors

    def __repr__(self) -> str:
        return (
            f"PipelineDefinition(name={self.name!r}, "
            f"steps={len(self.steps)}, status={self.status.value})"
        )


# ---------------------------------------------------------------------------
# PipelineBuilder  —  fluent DSL
# ---------------------------------------------------------------------------

class PipelineBuilder:
    """
    Fluent builder that assembles a PipelineDefinition.

    Example
    -------
        pipeline = (
            PipelineBuilder("enrich-events")
            .description("Enrich raw events with user data")
            .extract("raw_events",  source="event_stream", format="jsonl")
            .transform("enrich",    handler="user_enricher")
            .filter("valid_only",   expression="{{row.valid}} == True")
            .validate("schema_chk", schema="enriched_event_v1")
            .load("emit",           destination="output_queue", mode="append")
            .build()
        )
    """

    def __init__(self, name: str) -> None:
        self._name        = name
        self._description = ""
        self._tags: List[str] = []
        self._metadata: Dict[str, Any] = {}
        self._steps: List[PipelineStep] = []

    def description(self, text: str) -> "PipelineBuilder":
        self._description = text
        return self

    def tags(self, *tags: str) -> "PipelineBuilder":
        self._tags.extend(tags)
        return self

    def meta(self, **kwargs: Any) -> "PipelineBuilder":
        self._metadata.update(kwargs)
        return self

    # --- step adders -------------------------------------------------------

    def _add_step(
        self,
        name: str,
        step_type: StepType,
        handler: str,
        on_error: OnError = OnError.STOP,
        max_retries: int = 0,
        input_schema: Optional[str] = None,
        output_schema: Optional[str] = None,
        tags: Optional[List[str]] = None,
        branches: Optional[Dict[str, "PipelineDefinition"]] = None,
        **params: Any,
    ) -> "PipelineBuilder":
        step = PipelineStep(
            step_id=f"{name}_{uuid.uuid4().hex[:6]}",
            name=name,
            step_type=step_type,
            handler=handler,
            params=params,
            on_error=on_error,
            max_retries=max_retries,
            input_schema=input_schema,
            output_schema=output_schema,
            tags=list(tags or []),
            branches=branches or {},
        )
        self._steps.append(step)
        return self

    def extract(
        self,
        name: str,
        handler: str = "default_extractor",
        on_error: OnError = OnError.STOP,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add an EXTRACT step (pull data from a source)."""
        return self._add_step(name, StepType.EXTRACT, handler, on_error=on_error, **params)

    def transform(
        self,
        name: str,
        handler: str,
        on_error: OnError = OnError.SKIP,
        max_retries: int = 0,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a TRANSFORM step (mutate / enrich rows)."""
        return self._add_step(
            name, StepType.TRANSFORM, handler,
            on_error=on_error, max_retries=max_retries, **params
        )

    def filter(
        self,
        name: str,
        expression: str,
        handler: str = "expression_filter",
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a FILTER step (drop rows not matching expression)."""
        return self._add_step(
            name, StepType.FILTER, handler,
            expression=expression, **params
        )

    def load(
        self,
        name: str,
        handler: str = "default_loader",
        on_error: OnError = OnError.STOP,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a LOAD step (write data to a destination)."""
        return self._add_step(name, StepType.LOAD, handler, on_error=on_error, **params)

    def reduce(
        self,
        name: str,
        handler: str,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a REDUCE step (aggregate rows into a summary)."""
        return self._add_step(name, StepType.REDUCE, handler, **params)

    def join(
        self,
        name: str,
        right_source: str,
        on_key: str,
        handler: str = "key_joiner",
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a JOIN step (merge two streams by key)."""
        return self._add_step(
            name, StepType.JOIN, handler,
            right_source=right_source, on_key=on_key, **params
        )

    def validate_step(
        self,
        name: str,
        schema: str,
        handler: str = "schema_validator",
        on_error: OnError = OnError.STOP,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a VALIDATE step (assert schema / constraints)."""
        return self._add_step(
            name, StepType.VALIDATE, handler,
            on_error=on_error, input_schema=schema, **params
        )

    def branch(
        self,
        name: str,
        branches: Dict[str, "PipelineDefinition"],
        handler: str = "branch_router",
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a BRANCH step (fan-out to N named sub-pipelines)."""
        return self._add_step(
            name, StepType.BRANCH, handler,
            branches=branches, **params
        )

    def custom(
        self,
        name: str,
        handler: str,
        on_error: OnError = OnError.STOP,
        max_retries: int = 0,
        **params: Any,
    ) -> "PipelineBuilder":
        """Add a CUSTOM step with a user-defined handler key."""
        return self._add_step(
            name, StepType.CUSTOM, handler,
            on_error=on_error, max_retries=max_retries, **params
        )

    # --- build -------------------------------------------------------------

    def build(self) -> PipelineDefinition:
        pipeline = PipelineDefinition(
            name=self._name,
            steps=self._steps,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata,
        )
        errors = pipeline.validate()
        if errors:
            raise ValueError(
                "PipelineDefinition validation failed:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )
        return pipeline


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def simple_etl(
    name: str,
    source: str,
    destination: str,
    *,
    transform_handler: Optional[str] = None,
    description: str = "",
) -> PipelineDefinition:
    """
    Build a minimal Extract → [Transform] → Load pipeline.

    Example
    -------
        p = simple_etl("user-sync", source="user_api", destination="user_store",
                       transform_handler="pii_stripper")
    """
    builder = PipelineBuilder(name).description(description)
    builder.extract("extract", source=source)
    if transform_handler:
        builder.transform("transform", handler=transform_handler)
    builder.load("load", destination=destination)
    return builder.build()
