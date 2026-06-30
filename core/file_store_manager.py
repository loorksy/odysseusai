"""FileStoreManager — Structured file I/O with metadata tracking (C58).

Abstracts local-disk file operations behind a consistent interface:
  - Organised storage under a configurable base_dir with owner/namespace subdirs
  - Atomic writes (write to .tmp then rename)
  - Per-file metadata sidecar: size, hash, mimetype, created_at, owner
  - List, search, and delete with glob patterns
  - Auto-cleanup of files older than TTL
  - TelemetryCollector integration for write/read/delete events

Public API:
  fs = FileStoreManager(base_dir, telemetry=None)
  path = fs.write(name, data, *, owner, namespace, ttl_s)  -> str
  data = fs.read(name, *, owner, namespace)                -> bytes | None
  ok   = fs.delete(name, *, owner, namespace)              -> bool
  meta = fs.metadata(name, *, owner, namespace)            -> dict | None
  files = fs.list(*, owner, namespace, pattern)            -> list[dict]
  fs.cleanup_expired()
"""
from __future__ import annotations
import hashlib, json, logging, mimetypes, os, shutil, tempfile, threading, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class FileStoreManager:
    """Atomic file store with metadata sidecars."""

    def __init__(self, base_dir: str = "data/files", telemetry=None):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        self._tel  = telemetry
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(
        self,
        name:      str,
        data:      Union[bytes, str],
        *,
        owner:     str = "default",
        namespace: str = "default",
        ttl_s:     Optional[float] = None,
        mimetype:  Optional[str]   = None,
    ) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        dest_dir = self._dir(owner, namespace)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest     = dest_dir / name
        # Atomic write via temp file
        fd, tmp  = tempfile.mkstemp(dir=dest_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            shutil.move(tmp, dest)
        except Exception:
            os.unlink(tmp)
            raise
        meta = {
            "name":       name,
            "owner":      owner,
            "namespace":  namespace,
            "size":       len(data),
            "sha256":     hashlib.sha256(data).hexdigest(),
            "mimetype":   mimetype or (mimetypes.guess_type(name)[0] or "application/octet-stream"),
            "created_at": time.time(),
            "expires_at": (time.time() + ttl_s) if ttl_s else None,
        }
        (dest_dir / f"{name}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self._emit("file_write", owner, name, len(data))
        return str(dest)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(
        self,
        name:      str,
        *,
        owner:     str = "default",
        namespace: str = "default",
    ) -> Optional[bytes]:
        dest = self._dir(owner, namespace) / name
        if not dest.exists():
            return None
        meta = self._read_meta(name, owner=owner, namespace=namespace)
        if meta and meta.get("expires_at") and time.time() > meta["expires_at"]:
            self.delete(name, owner=owner, namespace=namespace)
            return None
        data = dest.read_bytes()
        self._emit("file_read", owner, name, len(data))
        return data

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete(
        self,
        name:      str,
        *,
        owner:     str = "default",
        namespace: str = "default",
    ) -> bool:
        dest = self._dir(owner, namespace) / name
        if not dest.exists():
            return False
        dest.unlink(missing_ok=True)
        meta_file = dest.parent / f"{name}.meta.json"
        meta_file.unlink(missing_ok=True)
        self._emit("file_delete", owner, name, 0)
        return True

    # ------------------------------------------------------------------
    # Metadata / list
    # ------------------------------------------------------------------

    def metadata(
        self, name: str, *, owner: str = "default", namespace: str = "default"
    ) -> Optional[Dict]:
        return self._read_meta(name, owner=owner, namespace=namespace)

    def list(
        self,
        *,
        owner:     str = "default",
        namespace: str = "default",
        pattern:   str = "*",
    ) -> List[Dict]:
        d = self._dir(owner, namespace)
        if not d.exists():
            return []
        results = []
        for p in d.glob(pattern):
            if p.suffix == ".json" and p.stem.endswith(".meta"):
                continue
            meta = self._read_meta(p.name, owner=owner, namespace=namespace) or {}
            meta["path"] = str(p)
            results.append(meta)
        return results

    def cleanup_expired(self) -> int:
        removed = 0
        for meta_file in self._base.rglob("*.meta.json"):
            try:
                meta = json.loads(meta_file.read_text())
                exp  = meta.get("expires_at")
                if exp and time.time() > exp:
                    data_file = meta_file.parent / meta["name"]
                    data_file.unlink(missing_ok=True)
                    meta_file.unlink(missing_ok=True)
                    removed += 1
            except Exception as e:
                logger.debug(f"FileStoreManager.cleanup: {e}")
        return removed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _dir(self, owner: str, namespace: str) -> Path:
        return self._base / owner / namespace

    def _read_meta(self, name: str, *, owner: str, namespace: str) -> Optional[Dict]:
        p = self._dir(owner, namespace) / f"{name}.meta.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _emit(self, kind: str, owner: str, name: str, size: int) -> None:
        if self._tel:
            try:
                self._tel.emit(kind, {"name": name, "size": size}, owner=owner)
            except Exception:
                pass
