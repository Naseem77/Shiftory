"""Git repository discovery, comparison resolution, and patch acquisition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import urlparse

from shiftory.errors import GitError, GitFilterError, ScopeError
from shiftory.models.core import Comparison


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    staged: bool = False
    unstaged: bool = False
    commit: str | None = None
    range: str | None = None
    branch: str | None = None
    pr: int | None = None
    remote: str = "origin"
    parent: int | None = None

    def selected(self) -> int:
        return sum(
            (
                self.staged,
                self.unstaged,
                self.commit is not None,
                self.range is not None,
                self.branch is not None,
                self.pr is not None,
            )
        )


def _git(
    root: Path,
    args: Sequence[str],
    *,
    check: bool = True,
    text: bool = True,
    input_data: str | bytes | None = None,
) -> str | bytes:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "GIT_PAGER": "cat"}
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=text,
        input=input_data,
    )
    if check and process.returncode:
        stderr = process.stderr if text else process.stderr.decode("utf-8", "replace")
        raise GitError(
            stderr.strip() or f"git {' '.join(args)} failed",
            details={"command": ["git", *args], "returncode": process.returncode},
        )
    return cast(str | bytes, process.stdout)


def normalize_worktree_bytes(root: Path, path: str, content: bytes) -> bytes:
    """Ask Git for canonical bytes using an ephemeral alternate object database."""
    object_output = _git(root, ["rev-parse", "--path-format=absolute", "--git-path", "objects"])
    assert isinstance(object_output, str)
    object_directory = Path(object_output.strip()).resolve()
    transient = object_directory.parent / f"shiftory-normalize-{uuid.uuid4().hex}"
    transient.mkdir(mode=0o700)
    alternates = str(object_directory)
    if inherited := os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        alternates = f"{alternates}{os.pathsep}{inherited}"
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_OBJECT_DIRECTORY": str(transient),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": alternates,
    }
    try:
        hashed = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "hash-object",
                "-w",
                f"--path={path}",
                "--stdin",
            ],
            cwd=root,
            env=env,
            input=content,
            check=False,
            capture_output=True,
        )
        if hashed.returncode:
            raise GitFilterError(
                f"Git cannot produce normalized content for {path!r}",
                details={
                    "path": path,
                    "reason": "git_normalization_failed",
                    "returncode": hashed.returncode,
                    "stderr": hashed.stderr.decode("utf-8", "replace").strip(),
                },
            )
        object_id = hashed.stdout.decode("ascii").strip()
        materialized = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            cwd=root,
            env=env,
            check=False,
            capture_output=True,
        )
        if materialized.returncode:
            raise GitFilterError(
                f"Git normalized content for {path!r} but it could not be read",
                details={
                    "path": path,
                    "reason": "normalized_object_unavailable",
                    "object_id": object_id,
                    "stderr": materialized.stderr.decode("utf-8", "replace").strip(),
                },
            )
        return materialized.stdout
    finally:
        shutil.rmtree(transient)


def normalize_path(path: str) -> str:
    if "\0" in path:
        raise GitError("Git produced a repository path containing NUL")
    normalized = PurePosixPath(path).as_posix()
    if (
        not normalized
        or normalized.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
    ):
        raise GitError(f"Git produced an unsafe repository path: {path!r}")
    return normalized[2:] if normalized.startswith("./") else normalized


def resolve_repository(path: str | Path = ".") -> Path:
    candidate = Path(path).expanduser().resolve()
    try:
        output = _git(candidate, ["rev-parse", "--show-toplevel"])
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise GitError(f"Repository path does not exist: {candidate}") from exc
    assert isinstance(output, str)
    return Path(output.strip()).resolve()


def repository_identity(root: Path) -> str:
    common = _git(root, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    remote = _git(root, ["config", "--get", "remote.origin.url"], check=False)
    assert isinstance(common, str) and isinstance(remote, str)
    payload = f"shiftory-repository/v1\0{Path(common.strip()).resolve()}\0{remote.strip()}".encode()
    return hashlib.sha256(payload).hexdigest()


def _rev(root: Path, expression: str) -> str:
    try:
        output = _git(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{expression}^{{commit}}"],
        )
    except GitError as exc:
        raise GitError(
            f"Unable to resolve revision {expression!r} to a commit",
            details={
                "revision": expression,
                "recovery": "git fetch --all --tags --prune",
                "cause": str(exc),
            },
        ) from exc
    assert isinstance(output, str)
    return output.strip()


def _tree(root: Path, revision: str) -> str:
    try:
        output = _git(root, ["rev-parse", "--verify", f"{revision}^{{tree}}"])
    except GitError as exc:
        shallow = _git(root, ["rev-parse", "--is-shallow-repository"], check=False)
        recovery = (
            "git fetch --unshallow"
            if isinstance(shallow, str) and shallow.strip() == "true"
            else f"git fetch --all --tags --prune  # missing {revision}"
        )
        raise GitError(
            f"Unable to resolve the source tree for commit {revision}",
            details={"revision": revision, "recovery": recovery, "cause": str(exc)},
        ) from exc
    assert isinstance(output, str)
    return output.strip()


def _fingerprint_worktree(root: Path) -> str:
    listing = _git(
        root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        text=False,
    )
    assert isinstance(listing, bytes)
    digest = hashlib.sha256(b"shiftory-working-tree/v2\0")
    for raw_path in sorted(set(item for item in listing.split(b"\0") if item)):
        path = root / os.fsdecode(raw_path)
        digest.update(len(raw_path).to_bytes(8, "big") + raw_path)
        try:
            path.parent.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise GitError(
                f"Refusing a working-tree path that traverses outside the repository: "
                f"{os.fsdecode(raw_path)!r}"
            ) from exc
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
            continue
        digest.update(
            f"{stat.S_IFMT(metadata.st_mode):o}:{stat.S_IMODE(metadata.st_mode):o}\0".encode()
        )
        try:
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(os.fsencode(path))
                assert isinstance(target, bytes)
                digest.update(b"link\0" + hashlib.sha256(target).digest())
            elif stat.S_ISREG(metadata.st_mode):
                digest.update(b"file\0" + hashlib.sha256(path.read_bytes()).digest())
            elif stat.S_ISDIR(metadata.st_mode):
                head = _git(path, ["rev-parse", "--verify", "HEAD"], check=False, text=False)
                status = _git(
                    path,
                    ["status", "--porcelain=v2", "-z", "--untracked-files=normal"],
                    check=False,
                    text=False,
                )
                assert isinstance(head, bytes) and isinstance(status, bytes)
                digest.update(
                    b"directory\0"
                    + hashlib.sha256(head.strip()).digest()
                    + hashlib.sha256(status).digest()
                )
            else:
                digest.update(b"other\0")
        except OSError as exc:
            raise GitError(
                f"Working-tree path changed while it was being fingerprinted: "
                f"{os.fsdecode(raw_path)!r}",
                details={"recovery": "retry after the working tree stops changing"},
            ) from exc
    return digest.hexdigest()


def _mutable_fingerprint(root: Path, mode: str) -> str | None:
    if mode not in {"staged", "unstaged", "working"}:
        return None
    digest = hashlib.sha256(f"shiftory-mutable-scope/v1\0{mode}\0".encode())
    if mode in {"staged", "unstaged", "working"}:
        digest.update(_index_fingerprint(root).encode())
    if mode in {"unstaged", "working"}:
        digest.update(_fingerprint_worktree(root).encode())
    return digest.hexdigest()


def _stable_mutable_fingerprint(root: Path, mode: str) -> str | None:
    before = _mutable_fingerprint(root, mode)
    after = _mutable_fingerprint(root, mode)
    if before != after:
        raise GitError(
            f"The {mode} comparison changed while its source state was being fingerprinted",
            details={
                "mode": mode,
                "before_fingerprint": before,
                "after_fingerprint": after,
                "recovery": "retry after the index and working tree stop changing",
            },
        )
    return after


def assert_comparison_consistent(comparison: Comparison, *, operation: str) -> None:
    """Fail closed when a mutable comparison no longer has its resolved source state."""
    expected = comparison.after_fingerprint
    if expected is None:
        return
    actual = _stable_mutable_fingerprint(comparison.repository_root, comparison.mode)
    if actual != expected:
        raise GitError(
            f"The {comparison.mode} comparison changed during {operation}",
            details={
                "mode": comparison.mode,
                "operation": operation,
                "expected_fingerprint": expected,
                "actual_fingerprint": actual,
                "recovery": "retry after the index and working tree stop changing",
            },
        )


def _comparison_id(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"shiftory-comparison/v1\0" + encoded).hexdigest()


def resolve_comparison(root: Path, scope: ScopeSpec | None = None) -> Comparison:
    scope = scope or ScopeSpec()
    if scope.selected() > 1:
        raise ScopeError("Comparison modes are mutually exclusive")
    if scope.parent is not None and scope.commit is None:
        raise ScopeError("--parent is only valid with --commit")
    if scope.parent is not None and scope.parent < 1:
        raise ScopeError("--parent must be a positive integer")
    if scope.pr is not None and scope.pr < 1:
        raise ScopeError("--pr must be a positive integer")
    if not any((scope.commit, scope.range, scope.branch, scope.pr is not None)):
        _reject_unmerged_index(root)
    repo_id = repository_identity(root)
    head = _rev(root, "HEAD")
    parent = scope.parent
    after_fingerprint: str | None = None

    if scope.staged:
        mode, base, target = "staged", head, None
        base_label, head_label = "HEAD", "index"
        after_fingerprint = _stable_mutable_fingerprint(root, mode)
    elif scope.unstaged:
        mode, base, target = "unstaged", None, None
        base_label, head_label = "index", "working-tree"
        after_fingerprint = _stable_mutable_fingerprint(root, mode)
    elif scope.commit:
        target = _rev(root, scope.commit)
        parents = _git(root, ["show", "-s", "--format=%P", target])
        assert isinstance(parents, str)
        parent_shas = parents.strip().split()
        if len(parent_shas) > 1 and parent is None:
            raise ScopeError(
                "Merge commits require an explicit --parent selection",
                details={"parents": parent_shas},
            )
        if not parent_shas:
            if parent is not None:
                raise ScopeError(f"Parent {parent} does not exist for root commit {target}")
            base = _empty_tree(root)
        else:
            selected = (parent or 1) - 1
            if selected < 0 or selected >= len(parent_shas):
                raise ScopeError(f"Parent {parent} does not exist for commit {target}")
            base = parent_shas[selected]
        mode, base_label, head_label = "commit", base, target
    elif scope.range:
        separator = "..." if "..." in scope.range else ".."
        parts = scope.range.split(separator)
        if len(parts) != 2 or not all(parts):
            raise ScopeError("Range must be BASE..HEAD or BASE...HEAD")
        left, right = _rev(root, parts[0]), _rev(root, parts[1])
        base = _merge_base(root, left, right) if separator == "..." else left
        target = right
        mode, base_label, head_label = f"range-{separator}", left, right
    elif scope.branch:
        branch = _rev(root, scope.branch)
        base, target = _merge_base(root, head, branch), head
        mode, base_label, head_label = "branch", f"merge-base(HEAD,{scope.branch})", "HEAD"
    elif scope.pr is not None:
        data = _resolve_pr(root, scope.pr, scope.remote)
        base, target = data["base"], data["head"]
        mode, base_label, head_label = "pr", base, target
    else:
        mode, base, target = "working", head, None
        base_label, head_label = "HEAD", "working-tree"
        after_fingerprint = _stable_mutable_fingerprint(root, mode)

    payload: dict[str, object] = {
        "repository_id": repo_id,
        "mode": mode,
        "base": base,
        "head": target,
        "base_tree": (
            after_fingerprint if mode == "unstaged" else _tree(root, base) if base else None
        ),
        "head_tree": _tree(root, target) if target else after_fingerprint,
        "parent": parent,
    }
    return Comparison(
        repository_root=root,
        repository_id=repo_id,
        mode=mode,
        base_sha=base,
        head_sha=target,
        base_label=base_label,
        head_label=head_label,
        identity=_comparison_id(payload),
        after_fingerprint=after_fingerprint,
        parent=parent,
    )


def _index_fingerprint(root: Path) -> str:
    output = _git(root, ["ls-files", "-s", "-z"], text=False)
    assert isinstance(output, bytes)
    return hashlib.sha256(b"shiftory-index/v1\0" + output).hexdigest()


def _reject_unmerged_index(root: Path) -> None:
    output = _git(root, ["ls-files", "--unmerged", "-z"], text=False)
    assert isinstance(output, bytes)
    if output:
        raise GitError(
            "Cannot inventory changes while the index contains unresolved merges",
            details={"recovery": "resolve conflicts and run git add before analyzing"},
        )


def _merge_base(root: Path, left: str, right: str) -> str:
    try:
        output = _git(root, ["merge-base", "--all", left, right])
    except GitError as exc:
        shallow = _git(root, ["rev-parse", "--is-shallow-repository"], check=False)
        recovery = (
            "git fetch --unshallow"
            if isinstance(shallow, str) and shallow.strip() == "true"
            else f"git merge-base {left} {right}"
        )
        raise GitError(
            f"Unable to determine a merge base for {left} and {right}",
            details={"left": left, "right": right, "recovery": recovery, "cause": str(exc)},
        ) from exc
    assert isinstance(output, str)
    merge_bases = output.split()
    if len(merge_bases) != 1:
        raise GitError(
            f"Expected exactly one merge base for {left} and {right}",
            details={"merge_bases": merge_bases},
        )
    return merge_bases[0]


def _empty_tree(root: Path) -> str:
    output = _git(root, ["hash-object", "-t", "tree", "/dev/null"])
    assert isinstance(output, str)
    return output.strip()


def _resolve_pr(root: Path, number: int, remote: str) -> dict[str, str]:
    if not remote or remote.startswith("-") or "\0" in remote:
        raise ScopeError(f"Invalid Git remote name: {remote!r}")
    repository = _remote_repository(root, remote)
    try:
        process = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                repository,
                "--json",
                "baseRefOid,headRefOid",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ScopeError(
            "--pr requires the authenticated GitHub CLI and network access",
            details={"repository": repository, "cause": str(exc)},
        ) from exc
    if process.returncode:
        raise ScopeError(
            "--pr requires network access and an authenticated GitHub CLI",
            details={"stderr": process.stderr.strip()},
        )
    try:
        value = json.loads(process.stdout)
        result = {"base": value["baseRefOid"], "head": value["headRefOid"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ScopeError(
            f"GitHub returned an invalid response for pull request {number}",
            details={"repository": repository},
        ) from exc
    valid_ids = all(
        isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{40,64}", sha) for sha in result.values()
    )
    if not valid_ids:
        raise ScopeError(f"Pull request {number} did not resolve to valid commit IDs")
    for sha in result.values():
        if _object_exists(root, sha):
            continue
        try:
            _git(root, ["fetch", "--no-tags", remote, sha])
        except GitError as exc:
            raise GitError(
                f"Unable to fetch PR object {sha}",
                details={
                    "recovery": f"git fetch --no-tags {remote} {sha}",
                    "cause": str(exc),
                },
            ) from exc
        if not _object_exists(root, sha):
            raise GitError(f"Fetched PR object {sha} is still unavailable")
    return result


def _remote_repository(root: Path, remote: str) -> str:
    output = _git(root, ["remote", "get-url", remote], check=False)
    assert isinstance(output, str)
    url = output.strip()
    if not url:
        raise ScopeError(f"Git remote {remote!r} is not configured")
    if "://" in url:
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
        host = parsed.hostname
    elif ":" in url and "@" in url.split(":", 1)[0]:
        authority, path = url.split(":", 1)
        host = authority.rsplit("@", 1)[-1]
    else:
        parsed = urlparse(f"//{url}")
        host, path = parsed.hostname, parsed.path.lstrip("/")
    path = path.removesuffix(".git")
    if not host or path.count("/") != 1:
        raise ScopeError(
            f"Unable to derive a GitHub repository from remote {remote!r}",
            details={"remote_url": url},
        )
    return f"{host}/{path}"


def _object_exists(root: Path, sha: str) -> bool:
    process = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process.returncode == 0


def _diff_args(context_lines: int) -> list[str]:
    return [
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--full-index",
        "--binary",
        "--find-renames",
        "--find-copies",
        "--find-copies-harder",
        f"--unified={context_lines}",
    ]


def acquire_patch(comparison: Comparison, *, context_lines: int = 3) -> bytes:
    if context_lines < 0:
        raise ScopeError("Context lines must be non-negative")
    root = comparison.repository_root
    assert_comparison_consistent(comparison, operation="patch acquisition")
    args = ["diff", *_diff_args(context_lines)]
    if comparison.mode == "staged":
        args.extend(["--cached", comparison.base_sha or "HEAD"])
    elif comparison.mode == "unstaged":
        pass
    elif comparison.mode == "working":
        args.append(comparison.base_sha or "HEAD")
    else:
        if not comparison.base_sha or not comparison.head_sha:
            raise ScopeError("Resolved committed comparison is incomplete")
        args.extend([comparison.base_sha, comparison.head_sha])
    try:
        output = _git(root, args, text=False)
    except GitError as exc:
        if "filter" not in str(exc).lower():
            raise
        raise GitFilterError(
            "Git could not acquire a patch because a content filter failed",
            details={"reason": "git_filter_failed", "cause": str(exc)},
        ) from exc
    assert isinstance(output, bytes)
    if comparison.mode == "working":
        output += _untracked_patches(root, context_lines)
    assert_comparison_consistent(comparison, operation="patch acquisition")
    return output


def _untracked_patches(root: Path, context_lines: int) -> bytes:
    listing = _git(
        root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        text=False,
    )
    assert isinstance(listing, bytes)
    patches: list[bytes] = []
    for raw in sorted(item for item in listing.split(b"\0") if item):
        path = os.fsdecode(raw).rstrip("/")
        candidate = root / path
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise GitError(f"Unable to inspect untracked path {path!r}") from exc
        if stat.S_ISDIR(metadata.st_mode):
            patches.append(_nested_repository_patch(root, path))
            continue
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            raise GitError(f"Unsupported untracked file type at {path!r}")
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(os.fsencode(candidate))
            assert isinstance(target, bytes)
            content = target
            mode = b"120000"
        else:
            content = normalize_worktree_bytes(root, path, candidate.read_bytes())
            mode = b"100755" if metadata.st_mode & 0o111 else b"100644"
        process = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "diff",
                *_diff_args(context_lines),
                "--no-index",
                "--",
                "/dev/null",
                "-",
            ],
            cwd=root,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            input=content,
            check=False,
            capture_output=True,
        )
        if process.returncode not in (0, 1):
            raise GitError(
                f"Unable to inventory untracked path {path!r}",
                details={"stderr": process.stderr.decode("utf-8", "replace").strip()},
            )
        patch = process.stdout
        if not patch:
            patch = _empty_untracked_patch(root, os.fsencode(path), content, mode)
        else:
            patch = _rewrite_stdin_patch(patch, os.fsencode(path), mode)
        patches.append(patch)
    return b"".join(patches)


def _rewrite_stdin_patch(patch: bytes, raw_path: bytes, mode: bytes) -> bytes:
    old_path = _quote_patch_path(raw_path, b"a/")
    new_path = _quote_patch_path(raw_path, b"b/")
    patch = patch.replace(
        b"diff --git a/- b/-\n",
        b"diff --git " + old_path + b" " + new_path + b"\n",
        1,
    )
    patch = patch.replace(b"new file mode 100644\n", b"new file mode " + mode + b"\n", 1)
    return patch.replace(b"+++ b/-\n", b"+++ " + new_path + b"\n", 1)


def _empty_untracked_patch(root: Path, raw_path: bytes, content: bytes, mode: bytes) -> bytes:
    if content:
        raise GitError(f"Git emitted no patch for untracked path {os.fsdecode(raw_path)!r}")
    object_id = _git(root, ["hash-object", "--stdin"], text=False, input_data=content)
    assert isinstance(object_id, bytes)
    old_path = _quote_patch_path(raw_path, b"a/")
    new_path = _quote_patch_path(raw_path, b"b/")
    zero = b"0" * len(object_id.strip())
    return b"".join(
        (
            b"diff --git " + old_path + b" " + new_path + b"\n",
            b"new file mode " + mode + b"\n",
            b"index " + zero + b".." + object_id.strip() + b"\n",
        )
    )


def _nested_repository_patch(root: Path, path: str) -> bytes:
    nested = root / path
    top = _git(nested, ["rev-parse", "--show-toplevel"], check=False)
    assert isinstance(top, str)
    if not top or Path(top.strip()).resolve() != nested.resolve():
        raise GitError(
            f"Unsupported untracked directory at {path!r}",
            details={"path": path, "reason": "untracked_directory_not_git_repository"},
        )
    try:
        object_id = _rev(nested, "HEAD").encode("ascii")
    except GitError as exc:
        raise GitError(
            f"Untracked nested Git repository at {path!r} has no stable HEAD object",
            details={"path": path, "reason": "nested_repository_head_unavailable"},
        ) from exc
    old_path = _quote_patch_path(os.fsencode(path), b"a/")
    new_path = _quote_patch_path(os.fsencode(path), b"b/")
    zero = b"0" * len(object_id)
    return b"".join(
        (
            b"diff --git " + old_path + b" " + new_path + b"\n",
            b"new file mode 160000\n",
            b"index " + zero + b".." + object_id + b"\n",
        )
    )


def _quote_patch_path(path: bytes, prefix: bytes) -> bytes:
    escaped = bytearray(b'"')
    for value in prefix + path:
        if value in (34, 92):
            escaped.extend(b"\\" + bytes((value,)))
        elif 32 <= value <= 126:
            escaped.append(value)
        else:
            escaped.extend(f"\\{value:03o}".encode())
    escaped.extend(b'"')
    return bytes(escaped)
