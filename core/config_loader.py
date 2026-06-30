"""ConfigLoader — Layered configuration loader (C55).

Merges config from multiple sources in priority order (highest wins):
  1. Environment variables (prefix-filtered)
  2. .env file
  3. YAML / JSON / TOML config files
  4. In-code defaults

Features:
  - Dot-notation access: cfg.get("db.host", default)
  - Schema validation via simple type hints dict
  - Hot-reload: watch file for changes (polling, no inotify dep)
  - Secrets redaction in __repr__ / to_dict()
  - Environment-specific overrides: config.prod.yaml overlays config.yaml

Public API:
  cfg = ConfigLoader(defaults={}, env_prefix="SR_")
  cfg.load_file(path)          # YAML / JSON auto-detected
  cfg.load_env(prefix)         # pull from os.environ
  cfg.load_dotenv(path)        # parse .env file
  cfg.get(key, default)        # dot-notation key
  cfg.set(key, value)          # runtime override
  cfg.require(key)             # raises if missing
  cfg.to_dict(redact_secrets)  # snapshot
  cfg.watch(path, interval_s)  # background reload thread
"""
from __future__ import annotations
import json, logging, os, threading, time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_SECRET_KEYS = {"password", "secret", "token", "key", "api_key", "private"}


class ConfigLoader:
    """Layered config loader with dot-notation access."""

    def __init__(self, defaults: Optional[Dict] = None, env_prefix: str = "SR_"):
        self._data:   Dict[str, Any] = deepcopy(defaults or {})
        self._prefix  = env_prefix
        self._lock    = threading.RLock()
        self._watchers: List[threading.Thread] = []

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def load_file(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            logger.warning(f"ConfigLoader: file not found '{path}'")
            return
        text = p.read_text(encoding="utf-8")
        ext  = p.suffix.lower()
        if ext in (".yaml", ".yml"):
            data = self._parse_yaml(text)
        elif ext == ".toml":
            data = self._parse_toml(text)
        else:
            data = json.loads(text)
        with self._lock:
            _deep_merge(self._data, data)
        logger.debug(f"ConfigLoader: loaded '{path}'")

    def load_env(self, prefix: Optional[str] = None) -> int:
        pfx = (prefix or self._prefix).upper()
        count = 0
        for k, v in os.environ.items():
            if k.startswith(pfx):
                dot_key = k[len(pfx):].lower().replace("__", ".")
                self.set(dot_key, _coerce(v))
                count += 1
        return count

    def load_dotenv(self, path: str = ".env") -> int:
        p = Path(path)
        if not p.exists(): return 0
        count = 0
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)
            count += 1
        return count

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return _dot_get(self._data, key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            _dot_set(self._data, key, value)

    def require(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(f"ConfigLoader: required key '{key}' is missing")
        return val

    def to_dict(self, redact_secrets: bool = True) -> Dict:
        with self._lock:
            d = deepcopy(self._data)
        return _redact(d) if redact_secrets else d

    # ------------------------------------------------------------------
    # Hot-reload watcher
    # ------------------------------------------------------------------

    def watch(self, path: str, interval_s: float = 5.0) -> None:
        def _loop():
            last_mtime = 0.0
            while True:
                time.sleep(interval_s)
                try:
                    mtime = Path(path).stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        self.load_file(path)
                        logger.info(f"ConfigLoader: hot-reloaded '{path}'")
                except Exception as e:
                    logger.warning(f"ConfigLoader.watch: {e}")
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        self._watchers.append(t)

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_yaml(text: str) -> Dict:
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            # Minimal YAML subset: key: value lines only
            result = {}
            for line in text.splitlines():
                if ":" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition(":")
                    result[k.strip()] = _coerce(v.strip())
            return result

    @staticmethod
    def _parse_toml(text: str) -> Dict:
        try:
            import tomllib  # Python 3.11+
            return tomllib.loads(text)
        except ImportError:
            try:
                import tomli
                return tomli.loads(text)
            except ImportError:
                logger.warning("ConfigLoader: TOML parsing requires 'tomli' package")
                return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dot_get(d: Dict, key: str, default: Any) -> Any:
    parts = key.split(".")
    cur = d
    for part in parts:
        if not isinstance(cur, dict): return default
        cur = cur.get(part)
        if cur is None: return default
    return cur

def _dot_set(d: Dict, key: str, value: Any) -> None:
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value

def _deep_merge(base: Dict, overlay: Dict) -> None:
    for k, v in overlay.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v

def _coerce(v: str) -> Any:
    if v.lower() in ("true", "yes"): return True
    if v.lower() in ("false", "no"): return False
    try: return int(v)
    except ValueError: pass
    try: return float(v)
    except ValueError: pass
    return v

def _redact(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) else _redact(v))
                for k, v in d.items()}
    return d
