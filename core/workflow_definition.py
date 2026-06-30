"""
C91 · WorkflowDefinition
========================
Trigger → Condition → Action node graph for the ShadowRealm workflow engine.

Design principles
-----------------
* Pure data layer — no execution here (C92 handles that).
* Immutable node objects; graph is assembled via WorkflowBuilder.
* Full serialise/deserialise round-trip (dict ↔ WorkflowDefinition).
* Cycle detection at build-time to prevent infinite loops.
* stdlib only — no external deps.

Graph anatomy
-------------
    [Trigger] ──► [Condition?] ──► [Action] ──► [Action] ──► …
                        │
                        └──(else)──► [Action / branch end]

Node types
----------
    TriggerNode   — what starts the workflow
    ConditionNode — boolean branch (true_next / false_next)
    ActionNode    — work unit (tool call, sub-agent, notification, etc.)
    LoopNode      — repeat N times or while-condition
    ParallelNode  — fan-out to N child branches, join on completion

Usage
-----
    from core.workflow_definition import WorkflowBuilder, TriggerType, ActionType

    wf = (
        WorkflowBuilder("daily-digest")
        .description("Fetch + summarise + email daily digest")
        .trigger(TriggerType.SCHEDULE, cron="0 7 * * *")
        .action("fetch_news",  ActionType.TOOL_CALL,  tool="rss_reader")
        .action("summarise",   ActionType.LLM_CALL,   prompt_template="summarise_news")
        .action("send_email",  ActionType.TOOL_CALL,  tool="email_composer", to="{{user.email}}")
        .build()
    )

    payload = wf.to_dict()
    wf2     = WorkflowDefinition.from_dict(payload)
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

class TriggerType(str, Enum):
    SCHEDULE    = "schedule"     # cron / interval
    EVENT       = "event"        # internal event bus
    WEBHOOK     = "webhook"      # inbound HTTP call
    MANUAL      = "manual"       # user or API trigger
    CONDITION   = "condition"    # another node signals start


class ActionType(str, Enum):
    TOOL_CALL   = "tool_call"    # invoke a registered tool
    LLM_CALL    = "llm_call"     # call the LLM with a prompt
    SUB_AGENT   = "sub_agent"    # delegate to another agent
    NOTIFICATION = "notification" # emit a notification
    DATA_WRITE  = "data_write"   # write to a store
    CUSTOM      = "custom"       # user-defined handler key


class NodeType(str, Enum):
    TRIGGER     = "trigger"
    CONDITION   = "condition"
    ACTION      = "action"
    LOOP        = "loop"
    PARALLEL    = "parallel"


class WorkflowStatus(str, Enum):
    DRAFT       = "draft"
    ACTIVE      = "active"
    PAUSED      = "paused"
    ARCHIVED    = "archived"


# ---------------------------------------------------------------------------
# Node dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TriggerNode:
    node_id:      str
    trigger_type: TriggerType
    config:       Dict[str, Any] = field(default_factory=dict)
    next_node:    Optional[str]  = None

    node_type: NodeType = field(default=NodeType.TRIGGER, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type":    self.node_type.value,
            "node_id":      self.node_id,
            "trigger_type": self.trigger_type.value,
            "config":       copy.deepcopy(self.config),
            "next_node":    self.next_node,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TriggerNode":
        return cls(
            node_id=d["node_id"],
            trigger_type=TriggerType(d["trigger_type"]),
            config=d.get("config", {}),
            next_node=d.get("next_node"),
        )


@dataclass
class ConditionNode:
    node_id:    str
    expression: str                # e.g. "{{result.status}} == 'ok'"
    true_next:  Optional[str] = None
    false_next: Optional[str] = None

    node_type: NodeType = field(default=NodeType.CONDITION, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type":  self.node_type.value,
            "node_id":    self.node_id,
            "expression": self.expression,
            "true_next":  self.true_next,
            "false_next": self.false_next,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConditionNode":
        return cls(
            node_id=d["node_id"],
            expression=d["expression"],
            true_next=d.get("true_next"),
            false_next=d.get("false_next"),
        )


@dataclass
class ActionNode:
    node_id:     str
    name:        str
    action_type: ActionType
    params:      Dict[str, Any] = field(default_factory=dict)
    next_node:   Optional[str]  = None
    on_error:    Optional[str]  = None   # node_id to jump to on failure

    node_type: NodeType = field(default=NodeType.ACTION, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type":   self.node_type.value,
            "node_id":     self.node_id,
            "name":        self.name,
            "action_type": self.action_type.value,
            "params":      copy.deepcopy(self.params),
            "next_node":   self.next_node,
            "on_error":    self.on_error,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActionNode":
        return cls(
            node_id=d["node_id"],
            name=d["name"],
            action_type=ActionType(d["action_type"]),
            params=d.get("params", {}),
            next_node=d.get("next_node"),
            on_error=d.get("on_error"),
        )


@dataclass
class LoopNode:
    node_id:      str
    body_node:    str              # first node of loop body
    condition:    Optional[str] = None  # while-expression; None = count-based
    max_iter:     int = 10
    next_node:    Optional[str] = None  # after loop exits

    node_type: NodeType = field(default=NodeType.LOOP, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type":  self.node_type.value,
            "node_id":    self.node_id,
            "body_node":  self.body_node,
            "condition":  self.condition,
            "max_iter":   self.max_iter,
            "next_node":  self.next_node,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoopNode":
        return cls(
            node_id=d["node_id"],
            body_node=d["body_node"],
            condition=d.get("condition"),
            max_iter=d.get("max_iter", 10),
            next_node=d.get("next_node"),
        )


@dataclass
class ParallelNode:
    node_id:    str
    branches:   List[str]          # list of starting node_ids (one per branch)
    join_node:  Optional[str] = None  # converge here after all branches

    node_type: NodeType = field(default=NodeType.PARALLEL, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type": self.node_type.value,
            "node_id":   self.node_id,
            "branches":  list(self.branches),
            "join_node": self.join_node,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ParallelNode":
        return cls(
            node_id=d["node_id"],
            branches=d.get("branches", []),
            join_node=d.get("join_node"),
        )


# Union type for node storage
AnyNode = TriggerNode | ConditionNode | ActionNode | LoopNode | ParallelNode

_NODE_CONSTRUCTORS = {
    NodeType.TRIGGER:   TriggerNode.from_dict,
    NodeType.CONDITION: ConditionNode.from_dict,
    NodeType.ACTION:    ActionNode.from_dict,
    NodeType.LOOP:      LoopNode.from_dict,
    NodeType.PARALLEL:  ParallelNode.from_dict,
}


# ---------------------------------------------------------------------------
# WorkflowDefinition
# ---------------------------------------------------------------------------

class WorkflowDefinition:
    """
    Immutable (post-build) directed graph of workflow nodes.

    Attributes
    ----------
    workflow_id  : str
    name         : str
    description  : str
    version      : int            (monotonic, bumped on update)
    status       : WorkflowStatus
    trigger_id   : str            (node_id of the single TriggerNode)
    nodes        : Dict[str, AnyNode]
    tags         : List[str]
    metadata     : Dict[str, Any]
    """

    def __init__(
        self,
        name: str,
        trigger_id: str,
        nodes: Dict[str, AnyNode],
        *,
        workflow_id: Optional[str] = None,
        description: str = "",
        version: int = 1,
        status: WorkflowStatus = WorkflowStatus.DRAFT,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.workflow_id = workflow_id or str(uuid.uuid4())
        self.name        = name
        self.description = description
        self.version     = version
        self.status      = status
        self.trigger_id  = trigger_id
        self.nodes       = nodes
        self.tags        = tags or []
        self.metadata    = metadata or {}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "name":        self.name,
            "description": self.description,
            "version":     self.version,
            "status":      self.status.value,
            "trigger_id":  self.trigger_id,
            "nodes":       {nid: n.to_dict() for nid, n in self.nodes.items()},
            "tags":        list(self.tags),
            "metadata":    copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkflowDefinition":
        nodes: Dict[str, AnyNode] = {}
        for nid, raw in d.get("nodes", {}).items():
            nt = NodeType(raw["node_type"])
            nodes[nid] = _NODE_CONSTRUCTORS[nt](raw)
        return cls(
            name=d["name"],
            trigger_id=d["trigger_id"],
            nodes=nodes,
            workflow_id=d.get("workflow_id"),
            description=d.get("description", ""),
            version=d.get("version", 1),
            status=WorkflowStatus(d.get("status", WorkflowStatus.DRAFT.value)),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> AnyNode:
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found in workflow '{self.name}'")
        return self.nodes[node_id]

    def successors(self, node_id: str) -> List[str]:
        """Return immediate successor node_ids (all branches)."""
        node = self.get_node(node_id)
        succs: List[str] = []
        if isinstance(node, TriggerNode) and node.next_node:
            succs.append(node.next_node)
        elif isinstance(node, ConditionNode):
            if node.true_next:
                succs.append(node.true_next)
            if node.false_next:
                succs.append(node.false_next)
        elif isinstance(node, ActionNode):
            if node.next_node:
                succs.append(node.next_node)
            if node.on_error:
                succs.append(node.on_error)
        elif isinstance(node, LoopNode):
            succs.append(node.body_node)
            if node.next_node:
                succs.append(node.next_node)
        elif isinstance(node, ParallelNode):
            succs.extend(node.branches)
            if node.join_node:
                succs.append(node.join_node)
        return succs

    def validate(self) -> List[str]:
        """
        Return a list of validation error strings (empty = valid).

        Checks:
        - trigger_id exists
        - all successor refs exist
        - no cycles (DFS)
        """
        errors: List[str] = []

        if self.trigger_id not in self.nodes:
            errors.append(f"trigger_id '{self.trigger_id}' not in nodes")
            return errors  # pointless to continue

        # Check dangling refs
        for nid, node in self.nodes.items():
            for succ in self.successors(nid):
                if succ not in self.nodes:
                    errors.append(f"Node '{nid}' references unknown node '{succ}'")

        # Cycle detection (DFS with colour marking: 0=white, 1=grey, 2=black)
        colour: Dict[str, int] = {nid: 0 for nid in self.nodes}

        def dfs(nid: str) -> bool:
            colour[nid] = 1
            for succ in self.successors(nid):
                if succ not in colour:
                    continue
                if colour[succ] == 1:
                    errors.append(f"Cycle detected through node '{succ}'")
                    return True
                if colour[succ] == 0:
                    if dfs(succ):
                        return True
            colour[nid] = 2
            return False

        for nid in self.nodes:
            if colour[nid] == 0:
                if dfs(nid):
                    break  # one cycle report is enough

        return errors

    def __repr__(self) -> str:
        return (
            f"WorkflowDefinition(name={self.name!r}, "
            f"nodes={len(self.nodes)}, status={self.status.value})"
        )


# ---------------------------------------------------------------------------
# WorkflowBuilder  —  fluent DSL
# ---------------------------------------------------------------------------

class WorkflowBuilder:
    """
    Fluent builder that assembles a WorkflowDefinition.

    Example
    -------
        wf = (
            WorkflowBuilder("my-workflow")
            .trigger(TriggerType.SCHEDULE, cron="0 8 * * *")
            .action("step1", ActionType.TOOL_CALL, tool="fetcher")
            .condition("check", "{{result.ok}} == true", true_next="step2", false_next="notify_fail")
            .action("step2", ActionType.LLM_CALL, prompt_template="summarise")
            .action("notify_fail", ActionType.NOTIFICATION, channel="slack")
            .build()
        )
    """

    def __init__(self, name: str) -> None:
        self._name        = name
        self._description = ""
        self._tags: List[str] = []
        self._metadata: Dict[str, Any] = {}
        self._nodes: Dict[str, AnyNode] = {}
        self._order: List[str] = []   # insertion order for auto-linking
        self._trigger_id: Optional[str] = None

    def description(self, text: str) -> "WorkflowBuilder":
        self._description = text
        return self

    def tags(self, *tags: str) -> "WorkflowBuilder":
        self._tags.extend(tags)
        return self

    def meta(self, **kwargs: Any) -> "WorkflowBuilder":
        self._metadata.update(kwargs)
        return self

    # --- node adders -------------------------------------------------------

    def trigger(
        self,
        trigger_type: TriggerType,
        node_id: Optional[str] = None,
        **config: Any,
    ) -> "WorkflowBuilder":
        nid = node_id or f"trigger_{uuid.uuid4().hex[:6]}"
        node = TriggerNode(node_id=nid, trigger_type=trigger_type, config=config)
        self._nodes[nid] = node
        self._order.append(nid)
        self._trigger_id = nid
        return self

    def action(
        self,
        name: str,
        action_type: ActionType,
        node_id: Optional[str] = None,
        on_error: Optional[str] = None,
        **params: Any,
    ) -> "WorkflowBuilder":
        nid = node_id or name
        node = ActionNode(
            node_id=nid, name=name, action_type=action_type,
            params=params, on_error=on_error,
        )
        self._nodes[nid] = node
        self._order.append(nid)
        return self

    def condition(
        self,
        name: str,
        expression: str,
        true_next: Optional[str] = None,
        false_next: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> "WorkflowBuilder":
        nid = node_id or name
        node = ConditionNode(
            node_id=nid, expression=expression,
            true_next=true_next, false_next=false_next,
        )
        self._nodes[nid] = node
        self._order.append(nid)
        return self

    def loop(
        self,
        name: str,
        body_node: str,
        condition: Optional[str] = None,
        max_iter: int = 10,
        node_id: Optional[str] = None,
    ) -> "WorkflowBuilder":
        nid = node_id or name
        node = LoopNode(
            node_id=nid, body_node=body_node,
            condition=condition, max_iter=max_iter,
        )
        self._nodes[nid] = node
        self._order.append(nid)
        return self

    def parallel(
        self,
        name: str,
        branches: List[str],
        join_node: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> "WorkflowBuilder":
        nid = node_id or name
        node = ParallelNode(node_id=nid, branches=branches, join_node=join_node)
        self._nodes[nid] = node
        self._order.append(nid)
        return self

    # --- build -------------------------------------------------------------

    def build(self) -> WorkflowDefinition:
        if not self._trigger_id:
            raise ValueError("WorkflowBuilder: no trigger defined — call .trigger() first")

        # Auto-link sequential nodes (Trigger/Action/Loop connect to next in order)
        for i, nid in enumerate(self._order[:-1]):
            next_nid = self._order[i + 1]
            node = self._nodes[nid]
            if isinstance(node, (TriggerNode, ActionNode, LoopNode)):
                if node.next_node is None:
                    node.next_node = next_nid

        wf = WorkflowDefinition(
            name=self._name,
            trigger_id=self._trigger_id,
            nodes=self._nodes,
            description=self._description,
            tags=self._tags,
            metadata=self._metadata,
        )

        errors = wf.validate()
        if errors:
            raise ValueError(f"WorkflowDefinition validation failed:\n" + "\n".join(f"  • {e}" for e in errors))

        return wf


# ---------------------------------------------------------------------------
# Convenience factory helpers
# ---------------------------------------------------------------------------

def schedule_workflow(
    name: str,
    cron: str,
    actions: List[Dict[str, Any]],
    *,
    description: str = "",
) -> WorkflowDefinition:
    """
    Build a simple linear scheduled workflow from a list of action dicts.

    Each action dict: {"name": str, "action_type": ActionType, **params}
    """
    builder = WorkflowBuilder(name).description(description)
    builder.trigger(TriggerType.SCHEDULE, cron=cron)
    for act in actions:
        act = dict(act)
        aname = act.pop("name")
        atype = ActionType(act.pop("action_type"))
        builder.action(aname, atype, **act)
    return builder.build()


def event_workflow(
    name: str,
    event_type: str,
    actions: List[Dict[str, Any]],
    *,
    description: str = "",
) -> WorkflowDefinition:
    """Build a simple linear event-triggered workflow."""
    builder = WorkflowBuilder(name).description(description)
    builder.trigger(TriggerType.EVENT, event_type=event_type)
    for act in actions:
        act = dict(act)
        aname = act.pop("name")
        atype = ActionType(act.pop("action_type"))
        builder.action(aname, atype, **act)
    return builder.build()
