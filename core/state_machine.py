"""
C97 — State Machine
Lightweight finite state machine (FSM) for agent workflow control.
Supports guarded transitions, entry/exit hooks, and history tracking.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Transition:
    from_state: str
    to_state: str
    trigger: str
    guard: Optional[Callable[[Any], bool]] = None  # called with context
    action: Optional[Callable[[Any], None]] = None  # called on transition


@dataclass
class StateRecord:
    state: str
    entered_at: float = field(default_factory=time.time)
    trigger: str = ""


class FSMError(Exception):
    pass


class InvalidTransitionError(FSMError):
    pass


class StateMachine:
    """
    Finite state machine.

    Usage::

        fsm = StateMachine(initial="idle")
        fsm.add_transition("idle", "running", trigger="start")
        fsm.add_transition("running", "done", trigger="finish")
        fsm.trigger("start")
        assert fsm.state == "running"
    """

    def __init__(
        self,
        initial: str,
        on_enter: Optional[dict[str, Callable]] = None,
        on_exit: Optional[dict[str, Callable]] = None,
    ):
        self._state = initial
        self._transitions: list[Transition] = []
        self._on_enter: dict[str, Callable] = on_enter or {}
        self._on_exit: dict[str, Callable] = on_exit or {}
        self._history: list[StateRecord] = [
            StateRecord(state=initial, trigger="__init__")
        ]
        self._context: Any = None

    # ------------------------------------------------------------------ #
    #  Configuration                                                       #
    # ------------------------------------------------------------------ #

    def add_transition(
        self,
        from_state: str,
        to_state: str,
        trigger: str,
        guard: Optional[Callable[[Any], bool]] = None,
        action: Optional[Callable[[Any], None]] = None,
    ) -> "StateMachine":
        self._transitions.append(
            Transition(
                from_state=from_state,
                to_state=to_state,
                trigger=trigger,
                guard=guard,
                action=action,
            )
        )
        return self

    def on_enter(self, state: str, fn: Callable) -> "StateMachine":
        self._on_enter[state] = fn
        return self

    def on_exit(self, state: str, fn: Callable) -> "StateMachine":
        self._on_exit[state] = fn
        return self

    def set_context(self, context: Any) -> "StateMachine":
        self._context = context
        return self

    # ------------------------------------------------------------------ #
    #  Runtime                                                             #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> str:
        return self._state

    def can_trigger(self, trigger: str) -> bool:
        for t in self._transitions:
            if t.from_state == self._state and t.trigger == trigger:
                if t.guard is None or t.guard(self._context):
                    return True
        return False

    def trigger(self, trigger_name: str) -> str:
        candidates = [
            t for t in self._transitions
            if t.from_state == self._state and t.trigger == trigger_name
        ]
        if not candidates:
            raise InvalidTransitionError(
                f"No transition from '{self._state}' via trigger '{trigger_name}'"
            )
        transition = None
        for t in candidates:
            if t.guard is None or t.guard(self._context):
                transition = t
                break
        if transition is None:
            raise InvalidTransitionError(
                f"Guard blocked all transitions from '{self._state}' via '{trigger_name}'"
            )
        old_state = self._state
        # Exit hook
        if old_state in self._on_exit:
            try:
                self._on_exit[old_state](self._context)
            except Exception as e:
                logger.warning("on_exit hook error for '%s': %s", old_state, e)
        # Action
        if transition.action:
            try:
                transition.action(self._context)
            except Exception as e:
                logger.warning("Transition action error (%s->%s): %s", old_state, transition.to_state, e)
        # State change
        self._state = transition.to_state
        self._history.append(StateRecord(state=self._state, trigger=trigger_name))
        logger.debug("FSM: %s -[%s]-> %s", old_state, trigger_name, self._state)
        # Enter hook
        if self._state in self._on_enter:
            try:
                self._on_enter[self._state](self._context)
            except Exception as e:
                logger.warning("on_enter hook error for '%s': %s", self._state, e)
        return self._state

    def reset(self, state: Optional[str] = None) -> None:
        target = state or self._history[0].state
        self._state = target
        self._history = [StateRecord(state=target, trigger="__reset__")]

    # ------------------------------------------------------------------ #
    #  Introspection                                                       #
    # ------------------------------------------------------------------ #

    def available_triggers(self) -> list[str]:
        return [
            t.trigger for t in self._transitions
            if t.from_state == self._state
            and (t.guard is None or t.guard(self._context))
        ]

    def history(self, limit: int = 20) -> list[StateRecord]:
        return self._history[-limit:]

    def is_in(self, *states: str) -> bool:
        return self._state in states

    def __repr__(self) -> str:
        return f"StateMachine(state={self._state!r})"
