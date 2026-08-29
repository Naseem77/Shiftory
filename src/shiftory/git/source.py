"""Safe source snapshot materialization for structural enrichment."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

from shiftory.cache.store import CacheStore
from shiftory.errors import GitError
from shiftory.git.repository import assert_comparison_consistent, normalize_worktree_bytes
from shiftory.models.core import Comparison

_SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def _safe_source_path(path: str) -> bool:
    candidate = Path(path)
    lowered = candidate.name.lower()
    return (
        not candidate.is_absolute()
        and ".." not in candidate.parts
        and lowered not in _SENSITIVE_NAMES
        and candidate.suffix.lower() not in _SENSITIVE_SUFFIXES
        and ".git" not in candidate.parts
    )


def _git(root: Path, args: list[str], *, input_data: bytes | None = None) -> bytes:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_data,
        check=False,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    if process.returncode:
        raise GitError(process.stderr.decode("utf-8", "replace").strip())
    return process.stdout


def materialize_snapshot(
    comparison: Comparison,
    cache: CacheStore,
    side: str,
) -> tuple[Path | None, str]:
    if side not in {"before", "after"}:
        raise ValueError(f"Unknown source side: {side}")
    if not cache.enabled:
        return None, "none"
    assert_comparison_consistent(comparison, operation=f"{side} snapshot materialization")
    if side == "before":
        source_kind = "index" if comparison.mode == "unstaged" else "commit"
        snapshot_id = comparison.identity if source_kind == "index" else comparison.base_sha
    else:
        source_kind = (
            "commit"
            if comparison.head_sha
            else "index"
            if comparison.mode == "staged"
            else "working"
        )
        snapshot_id = comparison.head_sha or (
            comparison.identity
            if source_kind in {"index", "working"}
            else comparison.after_fingerprint
        )
    if not snapshot_id:
        return None, "none"
    key = hashlib.sha256(
        f"shiftory-source-snapshot/v2\0{side}\0{source_kind}\0{snapshot_id}".encode()
    ).hexdigest()
    relative = f"sources/{key}"
    destination = cache.root / relative / "tree"
    manifest = cache.read_manifest(f"{relative}/manifest.json")
    if manifest and manifest.get("key") == key and destination.is_dir():
        assert_comparison_consistent(comparison, operation=f"{side} snapshot materialization")
        return destination, key
    with cache.lock():
        manifest = cache.read_manifest(f"{relative}/manifest.json")
        if manifest and manifest.get("key") == key and destination.is_dir():
            assert_comparison_consistent(comparison, operation=f"{side} snapshot materialization")
            return destination, key
        base = cache.ensure() / "sources"
        base.mkdir(exist_ok=True)
        partial = base / f".{key}.{uuid.uuid4().hex}.partial"
        tree = partial / "tree"
        tree.mkdir(parents=True)
        try:
            if source_kind == "commit":
                revision = comparison.base_sha if side == "before" else comparison.head_sha
                assert revision is not None
                files = _materialize_commit(comparison.repository_root, revision, tree)
            elif source_kind == "index":
                files = _materialize_index(comparison.repository_root, tree)
            else:
                files = _materialize_worktree(comparison.repository_root, tree, cache.root)
            assert_comparison_consistent(comparison, operation=f"{side} snapshot materialization")
            target = cache.root / relative
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(partial, target)
            cache.atomic_write(
                f"{relative}/manifest.json",
                {
                    "key": key,
                    "files": files,
                    "source": comparison.base_label if side == "before" else comparison.head_label,
                    "side": side,
                },
            )
        finally:
            if partial.exists():
                shutil.rmtree(partial)
    return destination, key


def materialize_after_snapshot(
    comparison: Comparison,
    cache: CacheStore,
) -> tuple[Path | None, str]:
    return materialize_snapshot(comparison, cache, "after")


def _materialize_commit(root: Path, revision: str, destination: Path) -> list[dict[str, str]]:
    output = _git(root, ["ls-tree", "-r", "-z", revision])
    files: list[dict[str, str]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if not _safe_source_path(path):
            continue
        if mode == "160000" and kind == "commit":
            files.append({"path": path, "object": object_id, "mode": mode})
            continue
        if kind != "blob":
            continue
        content = _git(root, ["cat-file", "blob", object_id])
        _write_snapshot_file(destination, path, content, mode)
        files.append({"path": path, "object": object_id, "mode": mode})
    return files


def _materialize_index(root: Path, destination: Path) -> list[dict[str, str]]:
    output = _git(root, ["ls-files", "-s", "-z"])
    files: list[dict[str, str]] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode().split()
        path = raw_path.decode("utf-8", "surrogateescape")
        if stage != "0" or not _safe_source_path(path):
            continue
        if mode == "160000":
            files.append({"path": path, "object": object_id, "mode": mode})
            continue
        content = _git(root, ["cat-file", "blob", object_id])
        _write_snapshot_file(destination, path, content, mode)
        files.append({"path": path, "object": object_id, "mode": mode})
    return files


def _materialize_worktree(root: Path, destination: Path, cache_root: Path) -> list[dict[str, str]]:
    output = _git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    files: list[dict[str, str]] = []
    for raw_path in sorted(item for item in output.split(b"\0") if item):
        path = raw_path.decode("utf-8", "surrogateescape").rstrip("/")
        source = root / path
        try:
            source.parent.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        try:
            source.resolve().relative_to(cache_root.resolve())
            continue
        except ValueError:
            pass
        if not _safe_source_path(path):
            continue
        try:
            metadata = source.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(os.fsencode(source))
            assert isinstance(target, bytes)
            files.append(
                {
                    "path": path,
                    "object": hashlib.sha256(target).hexdigest(),
                    "mode": "120000",
                }
            )
            continue
        if stat.S_ISDIR(metadata.st_mode):
            object_id = _optional_git(source, ["rev-parse", "--verify", "HEAD"])
            files.append(
                {
                    "path": path,
                    "object": (
                        object_id.decode("ascii").strip()
                        if object_id is not None
                        else "unavailable"
                    ),
                    "mode": "160000",
                }
            )
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise GitError(
                f"Working-tree path changed during snapshot materialization: {path!r}",
                details={"recovery": "retry after the working tree stops changing"},
            ) from exc
        content = normalize_worktree_bytes(root, path, content)
        mode = "100755" if metadata.st_mode & 0o111 else "100644"
        _write_snapshot_file(destination, path, content, mode)
        files.append(
            {
                "path": path,
                "object": hashlib.sha256(content).hexdigest(),
                "mode": mode,
            }
        )
    return files


def _write_snapshot_file(destination: Path, path: str, content: bytes, mode: str) -> None:
    target = destination / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "120000":
        return
    target.write_bytes(content)
    target.chmod(0o755 if mode == "100755" else 0o644)


def source_bytes(comparison: Comparison, path: str, side: str) -> bytes | None:
    """Read source truth without mutating the checkout."""
    root = comparison.repository_root
    if side not in {"before", "after"}:
        raise ValueError(f"Unknown source side: {side}")
    if side == "before":
        if comparison.mode == "unstaged":
            return _index_source(root, path)
        if comparison.base_sha is None:
            return None
        return _committed_source(root, comparison.base_sha, path)
    if comparison.head_sha:
        return _committed_source(root, comparison.head_sha, path)
    if comparison.mode == "staged":
        return _index_source(root, path)
    candidate = root / path
    try:
        normalized = Path(path)
        if normalized.is_absolute() or ".." in normalized.parts:
            return None
        candidate.parent.resolve().relative_to(root.resolve())
        candidate.lstat()
    except (OSError, ValueError):
        return None
    if candidate.is_symlink():
        target = os.readlink(os.fsencode(candidate))
        assert isinstance(target, bytes)
        return target
    if not candidate.is_file():
        return None
    try:
        content = candidate.read_bytes()
    except OSError:
        return None
    return normalize_worktree_bytes(root, path, content)


def _index_source(root: Path, path: str) -> bytes | None:
    listing = _optional_git(
        root,
        ["ls-files", "--stage", "-z", "--", f":(literal){path}"],
    )
    if not listing:
        return None
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1:
        return None
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, object_id, stage = metadata.split()
    except ValueError:
        return None
    if stage != b"0" or mode == b"160000" or raw_path.decode("utf-8", "surrogateescape") != path:
        return None
    return _optional_git(root, ["cat-file", "blob", object_id.decode("ascii")])


def _committed_source(root: Path, revision: str, path: str) -> bytes | None:
    listing = _optional_git(
        root,
        ["ls-tree", "-z", "--full-tree", revision, "--", f":(literal){path}"],
    )
    if not listing:
        return None
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1:
        return None
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        _mode, kind, object_id = metadata.split()
    except ValueError:
        return None
    if kind != b"blob" or raw_path.decode("utf-8", "surrogateescape") != path:
        return None
    return _optional_git(root, ["cat-file", "blob", object_id.decode("ascii")])


def _optional_git(root: Path, args: list[str]) -> bytes | None:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    return process.stdout if process.returncode == 0 else None
