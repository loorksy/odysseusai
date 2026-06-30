"""EnvironmentManager — Runtime environment detection and profile switching (C57).

Centralises all environment-specific behaviour:
  - Detect current environment: development / staging / production / test
  - Load the right config file + .env overlay for the environment
  - Provide feature-flag helpers: is_production(), is_debug(), flag(name)
  - Gate expensive operations (e.g. telemetry flush, email sending)
    behind environment guards so they never fire in test/dev
  - Expose a lightweight feature-flag store backed by ConfigLoader

Public API:
  em = EnvironmentManager(config_loader, env_var="SR_ENV")
  em.environment()              -> str   ("development" | "staging" | "production" | "test")
  em.is_production()            -> bool
  em.is_development()           -> bool
  em.is_test()                  -> bool
  em.is_debug()                 -> bool
  em.flag(name, default=False)  -> bool   (feature flag)
  em.set_flag(name, value)               # runtime override
  em.require_production(fn)     -> fn    # decorator: no-op outside prod
  em.guard(fn)                  -> fn    # decorator: raises in prod
  em.load_for_environment(base_path)     # load config.<env>.yaml
"""
from __future__ import annotations
import functools, logging, os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_KNOWN_ENVS   = ("development", "staging", "production", "test")
_DEFAULT_ENV  = "development"
_ENV_VAR      = "SR_ENV"


class EnvironmentManager:
    """Runtime environment detection and feature-flag store."""

    def __init__(
        self,
        config_loader=None,
        env_var: str = _ENV_VAR,
    ):
        self._cfg      = config_loader
        self._env_var  = env_var
        self._flags:   Dict[str, bool] = {}
        self._env:     Optional[str]   = None

    # ------------------------------------------------------------------
    # Environment detection
    # ------------------------------------------------------------------

    def environment(self) -> str:
        if self._env:
            return self._env
        raw = os.environ.get(self._env_var, "").lower().strip()
        if raw in _KNOWN_ENVS:
            self._env = raw
        else:
            self._env = _DEFAULT_ENV
            if raw:
                logger.warning(f"EnvironmentManager: unknown env '{raw}', defaulting to '{_DEFAULT_ENV}'")
        return self._env

    def set_environment(self, env: str) -> None:
        if env not in _KNOWN_ENVS:
            raise ValueError(f"Unknown environment '{env}'. Choose from {_KNOWN_ENVS}.")
        self._env = env
        logger.info(f"EnvironmentManager: environment set to '{env}'")

    def is_production(self)  -> bool: return self.environment() == "production"
    def is_staging(self)     -> bool: return self.environment() == "staging"
    def is_development(self) -> bool: return self.environment() == "development"
    def is_test(self)        -> bool: return self.environment() == "test"

    def is_debug(self) -> bool:
        if self._cfg:
            return bool(self._cfg.get("debug", False))
        return os.environ.get("SR_DEBUG", "").lower() in ("1", "true", "yes")

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------

    def flag(self, name: str, default: bool = False) -> bool:
        if name in self._flags:
            return self._flags[name]
        if self._cfg:
            val = self._cfg.get(f"features.{name}")
            if val is not None:
                return bool(val)
        return default

    def set_flag(self, name: str, value: bool) -> None:
        self._flags[name] = value
        logger.debug(f"EnvironmentManager: feature flag '{name}' = {value}")

    def all_flags(self) -> Dict[str, bool]:
        flags = {}
        if self._cfg:
            features = self._cfg.get("features", {})
            if isinstance(features, dict):
                flags.update({k: bool(v) for k, v in features.items()})
        flags.update(self._flags)
        return flags

    # ------------------------------------------------------------------
    # Decorators
    # ------------------------------------------------------------------

    def require_production(self, fn: Callable) -> Callable:
        """Decorator: silently skip function if not in production."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not self.is_production():
                logger.debug(f"EnvironmentManager: skipping '{fn.__name__}' (not production)")
                return None
            return fn(*args, **kwargs)
        return wrapper

    def guard(self, fn: Callable) -> Callable:
        """Decorator: raise RuntimeError if called in production."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if self.is_production():
                raise RuntimeError(
                    f"'{fn.__name__}' is guarded and must not run in production"
                )
            return fn(*args, **kwargs)
        return wrapper

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def load_for_environment(self, base_path: str = "config") -> None:
        """Load config.<env>.yaml then overlay with .env.<env> if they exist."""
        if not self._cfg:
            logger.warning("EnvironmentManager: no ConfigLoader attached, skipping load")
            return
        import os as _os
        env = self.environment()
        for candidate in [
            f"{base_path}.yaml",
            f"{base_path}.{env}.yaml",
            f"{base_path}.json",
            f"{base_path}.{env}.json",
        ]:
            if _os.path.exists(candidate):
                self._cfg.load_file(candidate)
                logger.info(f"EnvironmentManager: loaded '{candidate}'")
        for dotenv in [".env", f".env.{env}"]:
            if _os.path.exists(dotenv):
                self._cfg.load_dotenv(dotenv)
                logger.info(f"EnvironmentManager: loaded dotenv '{dotenv}'")
        self._cfg.load_env()
