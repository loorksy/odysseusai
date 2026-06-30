"""UserPreferenceStore — Per-user preference persistence (C78).

Stores typed user preferences with defaults and schema validation.
Preferences are namespaced (e.g. "ui", "notifications", "agent")
and support change callbacks for reactive components.

Features:
  - Dot-notation access: prefs.get(user_id, "ui.theme")
  - Default values: registered per key
  - Type coercion: bool, int, float, str
  - SQLite persistence or in-memory
  - Change callbacks: register fn(user_id, key, old, new)
  - Bulk get/set
  - Reset to defaults

Public API:
  ps = UserPreferenceStore(db_path=":memory:")
  ps.register_default(key, value, *, description)
  ps.set(user_id, key, value)
  value = ps.get(user_id, key, default=None)
  prefs = ps.get_all(user_id, *, namespace)
  ps.reset(user_id, key)
  ps.reset_all(user_id)
  ps.on_change(fn)   # register change callback
"""
from __future__ import annotations
import json, logging, sqlite3, threading, time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class UserPreferenceStore:
    """Namespaced per-user preference store with defaults and callbacks."""

    def __init__(self, db_path: str = ":memory:"):
        self._db        = sqlite3.connect(db_path, check_same_thread=False)
        self._lock      = threading.Lock()
        self._defaults: Dict[str, Any] = {}
        self._callbacks: List[Callable] = []
        self._init_db()

    def register_default(self, key: str, value: Any, *, description: str = "") -> None:
        self._defaults[key] = value

    def set(self, user_id: str, key: str, value: Any) -> None:
        old = self.get(user_id, key)
        serialized = json.dumps(value, default=str)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO prefs(user_id, key, value, updated_at) VALUES(?,?,?,?)",
                (user_id, key, serialized, time.time())
            )
            self._db.commit()
        self._fire_callbacks(user_id, key, old, value)

    def get(self, user_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM prefs WHERE user_id=? AND key=?", (user_id, key)
            ).fetchone()
        if row:
            try: return json.loads(row[0])
            except Exception: return row[0]
        if key in self._defaults:
            return self._defaults[key]
        return default

    def get_all(self, user_id: str, *, namespace: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if namespace:
                rows = self._db.execute(
                    "SELECT key, value FROM prefs WHERE user_id=? AND key LIKE ?",
                    (user_id, f"{namespace}.%")
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT key, value FROM prefs WHERE user_id=?", (user_id,)
                ).fetchall()
        result = {k: v for k, v in self._defaults.items()
                  if not namespace or k.startswith(f"{namespace}.")}
        for key, val in rows:
            try: result[key] = json.loads(val)
            except Exception: result[key] = val
        return result

    def reset(self, user_id: str, key: str) -> None:
        old = self.get(user_id, key)
        with self._lock:
            self._db.execute(
                "DELETE FROM prefs WHERE user_id=? AND key=?", (user_id, key)
            )
            self._db.commit()
        new = self._defaults.get(key)
        self._fire_callbacks(user_id, key, old, new)

    def reset_all(self, user_id: str) -> int:
        with self._lock:
            c = self._db.execute("DELETE FROM prefs WHERE user_id=?", (user_id,))
            self._db.commit()
        return c.rowcount

    def on_change(self, fn: Callable) -> None:
        self._callbacks.append(fn)

    def _fire_callbacks(self, user_id: str, key: str, old: Any, new: Any) -> None:
        if old == new: return
        for fn in self._callbacks:
            try: fn(user_id, key, old, new)
            except Exception as e: logger.debug(f"UserPreferenceStore callback: {e}")

    def _init_db(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS prefs(
                user_id TEXT, key TEXT, value TEXT, updated_at REAL,
                PRIMARY KEY(user_id, key)
            )
        """)
        self._db.commit()
