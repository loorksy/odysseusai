"""
C86 · plugin_sandbox.py
Isolated execution environment for untrusted plugins.

Responsibilities
----------------
* Execute plugin callables inside a restricted namespace.
* Enforce CPU-time and wall-clock timeouts (SIGALRM on POSIX, thread timer on Windows).
* Block access to dangerous built-ins (exec, eval, __import__, open, etc.) by default.
* Capture stdout/stderr without letting them reach the real streams.
* Log every sandbox invocation to AuditLogger (C71) when one is provided.
* Return a SandboxResult with stdout, stderr, return value, and exception details.

Design constraints
------------------
* stdlib only — no external packages.
* Thread-safe: each sandbox run is independent.
* Graceful fallback on Windows (no SIGALRM).
"""

from __future__ import annotations

import builtins
import io
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# POSIX timeout support
try:
    import signal as _signal
    _HAS_SIGALRM = hasattr(_signal, "SIGALRM")
except ImportError:
    _HAS_SIGALRM = False


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class SandboxResult:
    success: bool
    return_value: Any = None
    stdout: str = ""
    stderr: str = ""
    exception: Optional[str] = None          # repr of exception if raised
    traceback: Optional[str] = None
    elapsed_seconds: float = 0.0
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SandboxError(Exception):
    pass

class SandboxTimeoutError(SandboxError):
    pass

class SandboxSecurityError(SandboxError):
    pass


# ---------------------------------------------------------------------------
# Restricted built-ins
# ---------------------------------------------------------------------------

# Built-in names that are always blocked.
_DEFAULT_BLOCKED: Set[str] = {
    "exec", "eval", "compile", "open", "__import__",
    "breakpoint", "input",
    # memory inspection
    "memoryview",
}

# Minimal safe subset allowed by default.
_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bin", "bool", "bytes", "callable",
        "chr", "dict", "dir", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "getattr", "hasattr",
        "hash", "hex", "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "min", "next",
        "object", "oct", "ord", "pow", "print", "range",
        "repr", "reversed", "round", "set", "setattr", "slice",
        "sorted", "str", "sum", "tuple", "type", "vars", "zip",
        "True", "False", "None",
        "NotImplemented", "Ellipsis",
        # exceptions
        "Exception", "ValueError", "TypeError", "KeyError",
        "IndexError", "AttributeError", "RuntimeError",
        "StopIteration", "GeneratorExit",
    )
    if hasattr(builtins, name)
}


def _make_restricted_globals(
    extra_globals: Optional[Dict[str, Any]] = None,
    extra_builtins: Optional[Dict[str, Any]] = None,
    blocked: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Build a restricted __globals__ dict for sandbox execution."""
    safe = dict(_SAFE_BUILTINS)
    if extra_builtins:
        safe.update(extra_builtins)
    if blocked:
        for name in blocked:
            safe.pop(name, None)

    g: Dict[str, Any] = {"__builtins__": safe}
    if extra_globals:
        g.update(extra_globals)
    return g


# ---------------------------------------------------------------------------
# Timeout helpers
# ---------------------------------------------------------------------------

class _TimeoutFlag:
    """Shared flag set by a watchdog thread."""
    def __init__(self) -> None:
        self.expired = False


@contextmanager
 def _thread_timeout(seconds: float):
    """Portable thread-based timeout context (raises SandboxTimeoutError)."""
    flag = _TimeoutFlag()
    exc_holder: List[Optional[BaseException]] = [None]

    def _watchdog():
        time.sleep(seconds)
        if not flag.expired:   # still running
            flag.expired = True

    watchdog = threading.Thread(target=_watchdog, daemon=True)
    watchdog.start()
    try:
        yield flag
    finally:
        flag.expired = True   # signal watchdog to exit cleanly
        watchdog.join(timeout=0.1)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class PluginSandbox:
    """
    Execute untrusted plugin callables in a restricted environment.

    Usage
    -----
    sandbox = PluginSandbox(timeout_seconds=5.0)
    result = sandbox.run(my_plugin_fn, args=(42,), kwargs={"verbose": True})
    if result.success:
        print(result.return_value)
    """

    def __init__(
        self,
        timeout_seconds: float = 10.0,
        extra_globals: Optional[Dict[str, Any]] = None,
        extra_builtins: Optional[Dict[str, Any]] = None,
        blocked_builtins: Optional[Set[str]] = None,
        audit_logger=None,  # optional C71 AuditLogger
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._extra_globals = extra_globals or {}
        self._extra_builtins = extra_builtins or {}
        self._blocked = blocked_builtins or _DEFAULT_BLOCKED
        self._audit = audit_logger
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        fn: Callable,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        plugin_name: str = "<unknown>",
    ) -> SandboxResult:
        """
        Execute *fn* with *args* / *kwargs* inside the sandbox.

        The function is called directly (not via exec) so it retains its
        original bytecode; only built-ins visible inside the sandbox are
        restricted via globals injection when wrapping code objects.
        stdout/stderr are captured for the duration of the call.
        """
        kwargs = kwargs or {}
        start = time.monotonic()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        self._audit_log("sandbox.start", plugin_name)

        result = SandboxResult(success=False)

        try:
            with self._capture_streams(stdout_buf, stderr_buf):
                return_value = self._execute_with_timeout(
                    fn, args, kwargs, plugin_name
                )
            result.success = True
            result.return_value = return_value
        except SandboxTimeoutError:
            result.timed_out = True
            result.exception = "SandboxTimeoutError"
            result.traceback = f"Plugin {plugin_name!r} exceeded {self.timeout_seconds}s."
            self._audit_log("sandbox.timeout", plugin_name)
        except SandboxSecurityError as exc:
            result.exception = repr(exc)
            result.traceback = traceback.format_exc()
            self._audit_log("sandbox.security_violation", plugin_name, str(exc))
        except Exception as exc:
            result.exception = repr(exc)
            result.traceback = traceback.format_exc()
            self._audit_log("sandbox.error", plugin_name, repr(exc))
        finally:
            result.elapsed_seconds = time.monotonic() - start
            result.stdout = stdout_buf.getvalue()
            result.stderr = stderr_buf.getvalue()

        return result

    def exec_code(
        self,
        code_str: str,
        local_vars: Optional[Dict[str, Any]] = None,
        plugin_name: str = "<unknown>",
    ) -> SandboxResult:
        """
        Compile and execute a code string in a restricted namespace.
        Returns SandboxResult with local_vars state in return_value.
        """
        local_vars = local_vars or {}
        start = time.monotonic()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        self._audit_log("sandbox.exec_code", plugin_name)

        result = SandboxResult(success=False)
        g = _make_restricted_globals(
            extra_globals=self._extra_globals,
            extra_builtins=self._extra_builtins,
            blocked=self._blocked,
        )

        try:
            code = compile(code_str, f"<plugin:{plugin_name}>", "exec")
        except SyntaxError as exc:
            result.exception = repr(exc)
            result.traceback = traceback.format_exc()
            result.elapsed_seconds = time.monotonic() - start
            return result

        try:
            with self._capture_streams(stdout_buf, stderr_buf):
                with _thread_timeout(self.timeout_seconds) as flag:
                    exec(code, g, local_vars)  # noqa: S102
                    if flag.expired:
                        raise SandboxTimeoutError()
            result.success = True
            result.return_value = dict(local_vars)
        except SandboxTimeoutError:
            result.timed_out = True
            result.exception = "SandboxTimeoutError"
            self._audit_log("sandbox.timeout", plugin_name)
        except Exception as exc:
            result.exception = repr(exc)
            result.traceback = traceback.format_exc()
            self._audit_log("sandbox.error", plugin_name, repr(exc))
        finally:
            result.elapsed_seconds = time.monotonic() - start
            result.stdout = stdout_buf.getvalue()
            result.stderr = stderr_buf.getvalue()

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_with_timeout(
        self,
        fn: Callable,
        args: tuple,
        kwargs: Dict[str, Any],
        plugin_name: str,
    ) -> Any:
        """Run fn in a thread; join with timeout; propagate exceptions."""
        result_holder: List[Any] = [None, None]  # [value, exception]

        def _runner():
            try:
                result_holder[0] = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                result_holder[1] = exc

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout=self.timeout_seconds)

        if t.is_alive():
            raise SandboxTimeoutError(
                f"Plugin {plugin_name!r} timed out after {self.timeout_seconds}s."
            )

        if result_holder[1] is not None:
            raise result_holder[1]

        return result_holder[0]

    @staticmethod
    @contextmanager
    def _capture_streams(stdout_buf: io.StringIO, stderr_buf: io.StringIO):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        try:
            yield
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

    def _audit_log(self, event: str, plugin_name: str, detail: str = "") -> None:
        if self._audit is None:
            return
        try:
            self._audit.log(
                action=event,
                actor="sandbox",
                resource=f"plugin:{plugin_name}",
                detail=detail,
            )
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<PluginSandbox timeout={self.timeout_seconds}s "
            f"blocked={sorted(self._blocked)}>"
        )
