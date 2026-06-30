"""
C98 — Agent Supervisor
Manages a pool of named agents: spawning, health-checking,
restarting on failure, and routing tasks by capability.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Type

from core.agent_base import AgentConfig, AgentRun, BaseAgent

logger = logging.getLogger(__name__)


@dataclass
class AgentRecord:
    name: str
    agent: BaseAgent
    capabilities: list[str] = field(default_factory=list)
    max_restarts: int = 3
    restart_count: int = 0
    total_runs: int = 0
    failed_runs: int = 0
    last_run_at: Optional[float] = None
    healthy: bool = True

    def record_run(self, run: AgentRun) -> None:
        self.total_runs += 1
        self.last_run_at = time.time()
        if not run.success:
            self.failed_runs += 1


class SupervisorError(Exception):
    pass


class AgentSupervisor:
    """
    Supervises a pool of agents.

    Usage::

        supervisor = AgentSupervisor()
        supervisor.register(my_agent, capabilities=["search", "summarize"])
        result = supervisor.run("search", "find recent AI papers")
    """

    def __init__(self, restart_on_failure: bool = True):
        self._agents: dict[str, AgentRecord] = {}
        self.restart_on_failure = restart_on_failure

    # ------------------------------------------------------------------ #
    #  Registration                                                        #
    # ------------------------------------------------------------------ #

    def register(
        self,
        agent: BaseAgent,
        capabilities: Optional[list[str]] = None,
        max_restarts: int = 3,
    ) -> "AgentSupervisor":
        name = agent.config.name
        if name in self._agents:
            raise SupervisorError(f"Agent '{name}' already registered")
        self._agents[name] = AgentRecord(
            name=name,
            agent=agent,
            capabilities=capabilities or [],
            max_restarts=max_restarts,
        )
        logger.info("Supervisor: registered agent '%s'", name)
        return self

    def unregister(self, name: str) -> None:
        if name not in self._agents:
            raise SupervisorError(f"Agent '{name}' not found")
        del self._agents[name]

    def replace(
        self,
        name: str,
        agent_class: Type[BaseAgent],
        config: Optional[AgentConfig] = None,
    ) -> AgentRecord:
        """Hot-swap an agent with a fresh instance of the same class."""
        record = self._get(name)
        new_agent = agent_class(config=config or record.agent.config)
        record.agent = new_agent
        record.healthy = True
        logger.info("Supervisor: replaced agent '%s'", name)
        return record

    # ------------------------------------------------------------------ #
    #  Routing                                                             #
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> BaseAgent:
        return self._get(name).agent

    def find_by_capability(self, capability: str) -> list[AgentRecord]:
        return [
            r for r in self._agents.values()
            if capability in r.capabilities and r.healthy
        ]

    def run(
        self,
        agent_name: str,
        user_input: str,
        **kwargs,
    ) -> AgentRun:
        record = self._get(agent_name)
        if not record.healthy:
            raise SupervisorError(f"Agent '{agent_name}' is unhealthy")
        run = record.agent.run(user_input, **kwargs)
        record.record_run(run)
        if not run.success and self.restart_on_failure:
            self._maybe_restart(record)
        return run

    def run_capable(
        self,
        capability: str,
        user_input: str,
        **kwargs,
    ) -> AgentRun:
        """Route to the first healthy agent with the given capability."""
        candidates = self.find_by_capability(capability)
        if not candidates:
            raise SupervisorError(f"No healthy agent found for capability '{capability}'")
        return self.run(candidates[0].name, user_input, **kwargs)

    # ------------------------------------------------------------------ #
    #  Health & restart                                                    #
    # ------------------------------------------------------------------ #

    def health_report(self) -> dict[str, dict]:
        return {
            name: {
                "healthy": r.healthy,
                "total_runs": r.total_runs,
                "failed_runs": r.failed_runs,
                "restart_count": r.restart_count,
                "capabilities": r.capabilities,
                "last_run_at": r.last_run_at,
            }
            for name, r in self._agents.items()
        }

    def mark_unhealthy(self, name: str) -> None:
        self._get(name).healthy = False
        logger.warning("Supervisor: marked agent '%s' as unhealthy", name)

    def mark_healthy(self, name: str) -> None:
        self._get(name).healthy = True

    def _maybe_restart(self, record: AgentRecord) -> None:
        if record.restart_count >= record.max_restarts:
            record.healthy = False
            logger.error(
                "Supervisor: agent '%s' exceeded max restarts (%d), marking unhealthy",
                record.name, record.max_restarts,
            )
            return
        try:
            record.agent.reset()
            record.restart_count += 1
            record.healthy = True
            logger.warning(
                "Supervisor: restarted agent '%s' (attempt %d/%d)",
                record.name, record.restart_count, record.max_restarts,
            )
        except Exception as e:
            record.healthy = False
            logger.error("Supervisor: failed to restart agent '%s': %s", record.name, e)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _get(self, name: str) -> AgentRecord:
        if name not in self._agents:
            raise SupervisorError(f"Agent '{name}' not registered")
        return self._agents[name]

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def agent_names(self) -> list[str]:
        return list(self._agents.keys())
