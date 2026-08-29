"""Repository-scoped, atomic local cache primitives."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

from shiftory.errors import CacheError
from shiftory.models.json import canonical_json

CACHE_SCHEMA = "shiftory.cache/v1"


def default_cache_root() -> Path:
    configured = os.environ.get("SHIFTORY_CACHE_DIR")
    return Path(configured).expanduser().resolve() if configured else user_cache_path("shiftory")


def repository_cache_path(repository_id: str, cache_root: Path | None = None) -> Path:
    if len(repository_id) != 64 or any(
        character not in "0123456789abcdef" for character in repository_id
    ):
        raise CacheError("Refusing an invalid repository cache identity")
    root = (cache_root or default_cache_root()).expanduser().resolve()
    return root / "repositories" / repository_id


class CacheStore:
    def __init__(
        self,
        repository_id: str,
        *,
        cache_root: Path | None = None,
        enabled: bool = True,
    ) -> None:
        self.repository_id = repository_id
        self.cache_root = (cache_root or default_cache_root()).expanduser().resolve()
        self.root = repository_cache_path(repository_id, self.cache_root)
        self.enabled = enabled

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            self.root.chmod(0o700)
        return self.root

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        lock_directory = self.cache_root / ".locks"
        lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = lock_directory / f"{self.repository_id}.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read_manifest(self, relative: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self._entry_path(relative)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA:
            return None
        return value

    def atomic_write(self, relative: str, value: dict[str, Any]) -> Path:
        if not self.enabled:
            raise CacheError("Cache is disabled")
        self.ensure()
        path = self._entry_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        payload = {"schema": CACHE_SCHEMA, **value}
        try:
            temporary.write_text(canonical_json(payload), encoding="utf-8")
            os.chmod(temporary, 0o600)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def status(self) -> dict[str, Any]:
        exists = self.root.is_dir()
        files = (
            sorted(
                str(path.relative_to(self.root))
                for path in self.root.rglob("*")
                if path.is_file() and path.name != ".lock"
            )
            if exists
            else []
        )
        return {"path": str(self.root), "exists": exists, "files": files}

    def clear(self) -> Path:
        root = self.root.resolve()
        repositories = (self.cache_root / "repositories").resolve()
        if (
            root == self.cache_root
            or root.parent != repositories
            or root.name != self.repository_id
        ):
            raise CacheError(f"Refusing to clear unsafe cache path: {root}")
        with self.lock():
            if root.exists():
                shutil.rmtree(root)
            self._fsync_directory(repositories)
        return root

    def _entry_path(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise CacheError(f"Refusing an unsafe cache entry path: {relative!r}")
        path = (self.root / candidate).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise CacheError(f"Refusing an unsafe cache entry path: {relative!r}") from exc
        return path

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
