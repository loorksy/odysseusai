"""
C95 · WorkflowAPI
=================
Lightweight HTTP/REST API over WorkflowEngine, WorkflowStore, and
WorkflowScheduler — zero external dependencies (stdlib http.server only).

Endpoints
---------
Workflows
  POST   /workflows                  Create a workflow definition
  GET    /workflows                  List workflows (?status=&tag=&limit=&offset=)
  GET    /workflows/<id>             Get a workflow definition
  PUT    /workflows/<id>/activate    Set status → ACTIVE (starts scheduling)
  PUT    /workflows/<id>/deactivate  Set status → INACTIVE
  DELETE /workflows/<id>             Delete definition + all runs
  POST   /workflows/<id>/run         Trigger a manual run immediately

Runs
  GET    /workflows/<id>/runs        List runs for a workflow (?status=&limit=&offset=)
  GET    /runs/<run_id>              Get a specific run record

Events
  POST   /events                     Publish an event to the scheduler
                                     Body: {"event_type": "...", "payload": {...}}

Health
  GET    /health                     Returns {"status": "ok", "scheduler": true/false}

Request / Response
------------------
* All bodies are JSON (UTF-8).
* Successful responses: 200 OK or 201 Created.
* Client errors: 400 Bad Request, 404 Not Found.
* Server errors: 500 Internal Server Error.
* Error shape: {"error": "<message>"}

Usage
-----
    from core.workflow_store     import WorkflowStore
    from core.workflow_engine    import WorkflowEngine
    from core.workflow_scheduler import WorkflowScheduler
    from core.workflow_api       import WorkflowAPI

    store     = WorkflowStore("./data/store")
    engine    = WorkflowEngine()
    scheduler = WorkflowScheduler(engine, store)
    scheduler.start()

    api = WorkflowAPI(engine, store, scheduler, host="127.0.0.1", port=8741)
    api.serve_forever()          # blocks; Ctrl-C to stop

    # Or non-blocking:
    api.start()
    # ... later ...
    api.stop()
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from core.workflow_definition import WorkflowDefinition, WorkflowStatus
from core.workflow_engine import WorkflowEngine
from core.workflow_scheduler import WorkflowScheduler
from core.workflow_store import WorkflowNotFound, WorkflowStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Route table (method, regex) → handler name
# ---------------------------------------------------------------------------

_ROUTES: list[Tuple[str, re.Pattern, str]] = [
    ("GET",    re.compile(r"^/health$"),                          "handle_health"),
    ("POST",   re.compile(r"^/workflows$"),                       "handle_create_workflow"),
    ("GET",    re.compile(r"^/workflows$"),                       "handle_list_workflows"),
    ("GET",    re.compile(r"^/workflows/(?P<id>[^/]+)$"),         "handle_get_workflow"),
    ("PUT",    re.compile(r"^/workflows/(?P<id>[^/]+)/activate$"),"handle_activate_workflow"),
    ("PUT",    re.compile(r"^/workflows/(?P<id>[^/]+)/deactivate$"),"handle_deactivate_workflow"),
    ("DELETE", re.compile(r"^/workflows/(?P<id>[^/]+)$"),         "handle_delete_workflow"),
    ("POST",   re.compile(r"^/workflows/(?P<id>[^/]+)/run$"),     "handle_run_workflow"),
    ("GET",    re.compile(r"^/workflows/(?P<id>[^/]+)/runs$"),    "handle_list_runs"),
    ("GET",    re.compile(r"^/runs/(?P<run_id>[^/]+)$"),          "handle_get_run"),
    ("POST",   re.compile(r"^/events$"),                          "handle_publish_event"),
]


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

    # injected by WorkflowAPI.start()
    engine:    WorkflowEngine
    store:     WorkflowStore
    scheduler: Optional[WorkflowScheduler]

    def log_message(self, fmt: str, *args: Any) -> None:  # suppress default stderr log
        log.debug("HTTP %s", fmt % args)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/"
        query  = parse_qs(parsed.query, keep_blank_values=False)

        for m, pattern, handler_name in _ROUTES:
            if m != method:
                continue
            match = pattern.match(path)
            if match:
                groups = match.groupdict()
                try:
                    body = self._read_json() if method in ("POST", "PUT", "PATCH") else {}
                    getattr(self, handler_name)(groups, query, body)
                except _HTTPError as exc:
                    self._send(exc.status, {"error": exc.message})
                except Exception as exc:
                    log.exception("Unhandled error in %s %s", method, path)
                    self._send(500, {"error": str(exc)})
                return

        self._send(404, {"error": f"No route for {method} {path}"})

    def do_GET(self)    -> None: self._dispatch("GET")
    def do_POST(self)   -> None: self._dispatch("POST")
    def do_PUT(self)    -> None: self._dispatch("PUT")
    def do_DELETE(self) -> None: self._dispatch("DELETE")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise _HTTPError(400, f"Invalid JSON: {exc}") from exc

    def _send(self, status: int, data: Any, created: bool = False) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        code = 201 if created else status
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data: Any, created: bool = False) -> None:
        self._send(201 if created else 200, data, created=False)

    def _q(self, query: Dict[str, list], key: str, default: Any = None) -> Any:
        vals = query.get(key)
        return vals[0] if vals else default

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    def handle_health(self, _g, _q, _b) -> None:
        self._ok({
            "status": "ok",
            "scheduler_running": bool(self.scheduler and self.scheduler.is_running),
        })

    # --- Workflows ---

    def handle_create_workflow(self, _g, _q, body: Dict) -> None:
        try:
            wf = WorkflowDefinition.from_dict(body)
        except Exception as exc:
            raise _HTTPError(400, f"Invalid workflow definition: {exc}") from exc
        self.store.save_workflow(wf)
        if self.scheduler:
            self.scheduler.register_workflow(wf)
        self._ok(wf.to_dict(), created=True)

    def handle_list_workflows(self, _g, query, _b) -> None:
        status = self._q(query, "status")
        tag    = self._q(query, "tag")
        limit  = int(self._q(query, "limit", 50))
        offset = int(self._q(query, "offset", 0))
        wfs = self.store.list_workflows(status=status, tag=tag, limit=limit, offset=offset)
        self._ok([w.to_dict() for w in wfs])

    def handle_get_workflow(self, groups, _q, _b) -> None:
        wf = self._load_wf(groups["id"])
        self._ok(wf.to_dict())

    def handle_activate_workflow(self, groups, _q, _b) -> None:
        wf = self._load_wf(groups["id"])
        wf.status = WorkflowStatus.ACTIVE
        self.store.save_workflow(wf)
        if self.scheduler:
            self.scheduler.register_workflow(wf)
        self._ok({"workflow_id": wf.workflow_id, "status": wf.status.value})

    def handle_deactivate_workflow(self, groups, _q, _b) -> None:
        wf = self._load_wf(groups["id"])
        wf.status = WorkflowStatus.INACTIVE
        self.store.save_workflow(wf)
        if self.scheduler:
            self.scheduler.unregister_workflow(wf.workflow_id)
        self._ok({"workflow_id": wf.workflow_id, "status": wf.status.value})

    def handle_delete_workflow(self, groups, _q, _b) -> None:
        wf_id = groups["id"]
        self._load_wf(wf_id)  # 404 guard
        if self.scheduler:
            self.scheduler.unregister_workflow(wf_id)
        self.store.delete_workflow(wf_id)
        self._ok({"deleted": wf_id})

    def handle_run_workflow(self, groups, _q, body: Dict) -> None:
        wf = self._load_wf(groups["id"])
        context = body.get("context", {})
        context["trigger"] = "manual"
        record = self.engine.run(wf, context)
        self.store.save_run(record)
        self._ok(record.to_dict(), created=True)

    # --- Runs ---

    def handle_list_runs(self, groups, query, _b) -> None:
        status = self._q(query, "status")
        limit  = int(self._q(query, "limit", 50))
        offset = int(self._q(query, "offset", 0))
        runs = self.store.list_runs(groups["id"], status=status, limit=limit, offset=offset)
        self._ok(runs)

    def handle_get_run(self, groups, _q, _b) -> None:
        try:
            record = self.store.load_run(groups["run_id"])
        except Exception as exc:
            raise _HTTPError(404, str(exc)) from exc
        self._ok(record.to_dict())

    # --- Events ---

    def handle_publish_event(self, _g, _q, body: Dict) -> None:
        event_type = body.get("event_type")
        if not event_type:
            raise _HTTPError(400, "Missing required field: event_type")
        if not self.scheduler:
            raise _HTTPError(503, "Scheduler not configured")
        payload = body.get("payload", {})
        self.scheduler.publish(event_type, payload)
        self._ok({"queued": event_type})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_wf(self, wf_id: str) -> WorkflowDefinition:
        try:
            return self.store.load_workflow(wf_id)
        except WorkflowNotFound as exc:
            raise _HTTPError(404, str(exc)) from exc


# ---------------------------------------------------------------------------
# HTTP error sentinel
# ---------------------------------------------------------------------------

class _HTTPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status  = status
        self.message = message


# ---------------------------------------------------------------------------
# WorkflowAPI
# ---------------------------------------------------------------------------

class WorkflowAPI:
    """
    Wraps an HTTPServer and wires Engine / Store / Scheduler into the handler.

    Parameters
    ----------
    engine    : WorkflowEngine
    store     : WorkflowStore
    scheduler : WorkflowScheduler | None
        Pass None if you don't need scheduling (manual-run-only mode).
    host      : str   (default "127.0.0.1")
    port      : int   (default 8741)
    """

    def __init__(
        self,
        engine:    WorkflowEngine,
        store:     WorkflowStore,
        scheduler: Optional[WorkflowScheduler] = None,
        host:      str = "127.0.0.1",
        port:      int = 8741,
    ) -> None:
        self._engine    = engine
        self._store     = store
        self._scheduler = scheduler
        self._host      = host
        self._port      = port
        self._server:   Optional[HTTPServer] = None
        self._thread:   Optional[threading.Thread] = None

        # Patch handler class attributes so every request gets the same objects
        _Handler.engine    = engine        # type: ignore[attr-defined]
        _Handler.store     = store         # type: ignore[attr-defined]
        _Handler.scheduler = scheduler     # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def serve_forever(self) -> None:
        """Start the server in the calling thread (blocks until stop() or Ctrl-C)."""
        self._server = HTTPServer((self._host, self._port), _Handler)
        log.info("WorkflowAPI listening on http://%s:%d", self._host, self._port)
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.server_close()

    def start(self) -> None:
        """Start the server in a background daemon thread (non-blocking)."""
        self._server = HTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="WorkflowAPI",
            daemon=True,
        )
        self._thread.start()
        log.info("WorkflowAPI started on http://%s:%d (background)", self._host, self._port)

    def stop(self) -> None:
        """Shut down the HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        log.info("WorkflowAPI stopped")

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"
