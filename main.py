#!/usr/bin/env python3
"""
C129 — ShadowRealm entry point.

Boot order
----------
1. Parse CLI / env
2. Load & validate config
3. Init core services (event bus, memory, telemetry, health)
4. Register agents with AgentRegistry
5. Start background workers (scheduler, health monitor)
6. Launch the Flask/TUI app

Every stage is wrapped so a failure prints a clear diagnostic and exits
with a non-zero code instead of a bare traceback.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logging — configured before any other import so early errors are captured
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("shadowrealm.main")


# ---------------------------------------------------------------------------
# CLI / env helpers
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="shadowrealm",
        description="ShadowRealm AI assistant platform",
    )
    parser.add_argument(
        "--config",
        default=os.getenv("SHADOWREALM_CONFIG", "config/settings.yaml"),
        metavar="PATH",
        help="Path to YAML config file (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "7860")),
        help="Bind port (default: 7860)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.getenv("DEBUG", "").lower() in ("1", "true", "yes"),
        help="Enable debug / hot-reload mode",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        default=os.getenv("SHADOWREALM_TUI", "").lower() in ("1", "true", "yes"),
        help="Launch the Textual TUI instead of the web app",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the interactive setup wizard and exit",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Stage helpers — each returns the constructed object or calls _fatal()
# ---------------------------------------------------------------------------

def _fatal(stage: str, exc: Exception) -> None:
    """Print a human-readable error and exit 1."""
    log.error("[BOOT FAILED] stage=%s error=%s: %s", stage, type(exc).__name__, exc)
    sys.exit(1)


def _load_config(path: str) -> object:
    """Load and validate application config."""
    try:
        from core.config_loader import ConfigLoader  # type: ignore
        from core.config_validator import ConfigValidator  # type: ignore

        loader = ConfigLoader(config_path=path)
        cfg = loader.load()
        validator = ConfigValidator()
        validator.validate(cfg)
        log.info("Config loaded from %s", path)
        return cfg
    except FileNotFoundError:
        log.warning(
            "Config file not found at '%s' — using built-in defaults.", path
        )
        # Return a minimal sentinel so downstream code can proceed
        return None
    except Exception as exc:  # noqa: BLE001
        _fatal("config", exc)


def _init_event_bus():
    try:
        from core.event_bus import EventBus  # type: ignore

        bus = EventBus()
        log.info("EventBus initialised")
        return bus
    except Exception as exc:  # noqa: BLE001
        _fatal("event_bus", exc)


def _init_memory(cfg) -> object:
    try:
        from core.memory_manager import MemoryManager  # type: ignore
        from core.memory_store import MemoryStore  # type: ignore

        store = MemoryStore()
        mgr = MemoryManager(store=store)
        log.info("MemoryManager ready")
        return mgr
    except Exception as exc:  # noqa: BLE001
        _fatal("memory", exc)


def _init_telemetry(bus) -> object:
    try:
        from core.telemetry_collector import TelemetryCollector  # type: ignore

        collector = TelemetryCollector(event_bus=bus)
        log.info("TelemetryCollector ready")
        return collector
    except Exception as exc:  # noqa: BLE001
        _fatal("telemetry", exc)


def _init_health_monitor(bus) -> object:
    try:
        from core.health_monitor import HealthMonitor  # type: ignore

        monitor = HealthMonitor(event_bus=bus)
        monitor.start()
        log.info("HealthMonitor started")
        return monitor
    except Exception as exc:  # noqa: BLE001
        _fatal("health_monitor", exc)


def _init_agent_registry(memory_mgr, bus) -> object:
    try:
        from core.agent_registry import AgentRegistry  # type: ignore

        registry = AgentRegistry()

        # Register concrete agents if available
        try:
            from agents.base_agent import BaseAgent  # type: ignore
            registry.register("base", BaseAgent)
        except ImportError:
            log.debug("agents.base_agent not found — skipping registration")

        try:
            from agents.planner_agent import PlannerAgent  # type: ignore
            registry.register("planner", PlannerAgent)
        except ImportError:
            log.debug("agents.planner_agent not found — skipping registration")

        log.info("AgentRegistry ready (%d agents)", len(registry))
        return registry
    except Exception as exc:  # noqa: BLE001
        _fatal("agent_registry", exc)


def _init_task_scheduler() -> Optional[object]:
    try:
        from core.task_scheduler import TaskScheduler  # type: ignore

        scheduler = TaskScheduler()
        scheduler.start()
        log.info("TaskScheduler started")
        return scheduler
    except Exception as exc:  # noqa: BLE001
        log.warning("TaskScheduler could not start: %s", exc)
        return None


def _register_shutdown_hooks(*stoppables) -> None:
    """Register SIGINT / SIGTERM handlers to gracefully stop services."""

    def _handler(signum, frame):  # noqa: ANN001
        log.info("Received signal %s — shutting down…", signum)
        for svc in stoppables:
            if svc is not None and hasattr(svc, "stop"):
                try:
                    svc.stop()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Error stopping %s: %s", svc, exc)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# Setup wizard mode
# ---------------------------------------------------------------------------

def _run_setup_wizard() -> None:
    try:
        from core.setup_wizard import SetupWizard  # type: ignore

        wizard = SetupWizard()
        wizard.run()
    except ImportError:
        log.error("SetupWizard not found in core.setup_wizard")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        _fatal("setup_wizard", exc)


# ---------------------------------------------------------------------------
# TUI mode
# ---------------------------------------------------------------------------

def _run_tui(registry, memory_mgr, bus) -> None:
    try:
        from core.tui_dashboard import TUIDashboard  # type: ignore

        dashboard = TUIDashboard(
            agent_registry=registry,
            memory_manager=memory_mgr,
            event_bus=bus,
        )
        log.info("Launching TUI — press Ctrl+C to exit")
        dashboard.run()
    except ImportError as exc:
        _fatal("tui", exc)
    except Exception as exc:  # noqa: BLE001
        _fatal("tui_runtime", exc)


# ---------------------------------------------------------------------------
# Web app mode
# ---------------------------------------------------------------------------

def _run_web_app(host: str, port: int, debug: bool) -> None:
    try:
        # app.py exposes a `create_app()` factory *and* a fallback `app` object
        try:
            from app import create_app  # type: ignore

            flask_app = create_app()
        except ImportError:
            from app import app as flask_app  # type: ignore  # noqa: PLC0415

        log.info("Starting web server on %s:%d (debug=%s)", host, port, debug)
        flask_app.run(host=host, port=port, debug=debug, use_reloader=debug)
    except ImportError as exc:
        _fatal("app_import", exc)
    except Exception as exc:  # noqa: BLE001
        _fatal("web_app", exc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        log.debug("Debug logging enabled")

    # ── Setup wizard (early exit) ──────────────────────────────────────────
    if args.setup:
        _run_setup_wizard()
        return

    # ── Boot sequence ─────────────────────────────────────────────────────
    log.info("=== ShadowRealm boot sequence starting ===")

    cfg = _load_config(args.config)
    bus = _init_event_bus()
    memory_mgr = _init_memory(cfg)
    _init_telemetry(bus)
    health_monitor = _init_health_monitor(bus)
    registry = _init_agent_registry(memory_mgr, bus)
    scheduler = _init_task_scheduler()

    _register_shutdown_hooks(health_monitor, scheduler)

    log.info("=== Boot complete — handing off to runtime ===")

    # ── Runtime ───────────────────────────────────────────────────────────
    if args.tui:
        _run_tui(registry, memory_mgr, bus)
    else:
        _run_web_app(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
