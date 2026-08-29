#!/usr/bin/env python3
"""Reproducible, fail-closed Shiftory benchmark runner."""

from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ROOT / "benchmarks"
SCENARIOS = BENCHMARKS / "scenarios.toml"
DEFAULT_WORKSPACE = Path.home() / ".cache" / "shiftory-benchmarks"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONCEPT_HUNK_RE = re.compile(r"^conceptual-hunk:(.+)#([1-9][0-9]*)$")
CONCEPT_UNIT_RE = re.compile(r"^conceptual-unit:(.+)#([1-9][0-9]*)$")
GRAPHORA_REQUIREMENT = "graphora-kg==0.2.1"
GRAPHORA_VERSION = "0.2.1"
TREE_SITTER_VERSION = "0.25.2"
DIFF_ARGS = (
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--src-prefix=a/",
    "--dst-prefix=b/",
    "--full-index",
    "--binary",
    "--find-renames",
    "--find-copies",
    "--find-copies-harder",
    "--unified=3",
)
IMPLEMENTATION_PATHS = (
    "src/shiftory/",
    "benchmarks/runner.py",
    "benchmarks/scenarios.toml",
    "benchmarks/golden/",
    "benchmarks/fixtures/",
    "pyproject.toml",
    "constraints-dev.txt",
)
SHIFTORY_BOOTSTRAP = """
import pathlib
import runpy
import sys

source = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
package = (source / "shiftory").resolve(strict=True)
sys.path.insert(0, str(source))
import shiftory

origin = pathlib.Path(shiftory.__file__).resolve(strict=True)
if origin != package / "__init__.py":
    raise RuntimeError(
        f"refusing to execute Shiftory from {origin}; expected {package / '__init__.py'}"
    )
runpy.run_module("shiftory.cli", run_name="__main__", alter_sys=True)
"""
SHIFTORY_IDENTITY_PROBE = """
import hashlib
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).resolve(strict=True)
package = (source / "shiftory").resolve(strict=True)
sys.path.insert(0, str(source))
import shiftory

origin = pathlib.Path(shiftory.__file__).resolve(strict=True)
expected_origin = package / "__init__.py"
if origin != expected_origin:
    raise RuntimeError(f"imported Shiftory from {origin}; expected {expected_origin}")
files = []
for path in sorted(package.rglob("*.py")):
    resolved = path.resolve(strict=True)
    if package != resolved.parent and package not in resolved.parents:
        raise RuntimeError(f"Shiftory source escapes the repository: {path}")
    payload = resolved.read_bytes()
    files.append(
        {
            "path": path.relative_to(source).as_posix(),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
manifest = {"files": files}
serialized = json.dumps(
    manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
)
manifest["sha256"] = hashlib.sha256(
    (serialized + "\\n").encode()
).hexdigest()
print(
    json.dumps(
        {
            "schema": "shiftory.benchmark-execution/v1",
            "distribution": "shiftory",
            "import_root": "repository:src",
            "module_file": origin.relative_to(source.parent).as_posix(),
            "package_code": manifest,
        },
        sort_keys=True,
    )
)
"""
SHIFTORY_EVIDENCE_RENDERER = """
import hashlib
import importlib
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).resolve(strict=True)
package = (source / "shiftory").resolve(strict=True)
sys.path.insert(0, str(source))
import shiftory

shiftory_origin = pathlib.Path(shiftory.__file__).resolve(strict=True)
expected_shiftory_origin = package / "__init__.py"
if shiftory_origin != expected_shiftory_origin:
    raise RuntimeError(
        f"imported Shiftory from {shiftory_origin}; expected {expected_shiftory_origin}"
    )
renderer = importlib.import_module("shiftory.render.evidence")
renderer_origin = pathlib.Path(renderer.__file__).resolve(strict=True)
expected_renderer_origin = (package / "render" / "evidence.py").resolve(strict=True)
if renderer_origin != expected_renderer_origin:
    raise RuntimeError(
        f"imported evidence renderer from {renderer_origin}; expected {expected_renderer_origin}"
    )
if package != renderer_origin.parent and package not in renderer_origin.parents:
    raise RuntimeError(f"evidence renderer escapes the repository: {renderer_origin}")

files = []
for path in sorted(package.rglob("*.py")):
    resolved = path.resolve(strict=True)
    if package != resolved.parent and package not in resolved.parents:
        raise RuntimeError(f"Shiftory source escapes the repository: {path}")
    payload = resolved.read_bytes()
    files.append(
        {
            "path": path.relative_to(source).as_posix(),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
manifest = {"files": files}
serialized = json.dumps(
    manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
)
manifest["sha256"] = hashlib.sha256(
    (serialized + "\\n").encode()
).hexdigest()
payload = renderer_origin.read_bytes()
evidence = json.loads(sys.stdin.buffer.read())
markdown = renderer.render_evidence_markdown(evidence)
if not isinstance(markdown, str):
    raise TypeError("evidence renderer returned a non-string value")
print(
    json.dumps(
        {
            "schema": "shiftory.benchmark-render-result/v1",
            "markdown": markdown,
            "renderer": {
                "schema": "shiftory.benchmark-renderer/v1",
                "distribution": "shiftory",
                "module": "shiftory.render.evidence",
                "module_file": renderer_origin.relative_to(source.parent).as_posix(),
                "module_sha256": hashlib.sha256(payload).hexdigest(),
                "package_code_sha256": manifest["sha256"],
            },
            "package_code": manifest,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
)
"""


class BenchmarkError(RuntimeError):
    """A benchmark invariant failed."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.partial")
    try:
        partial.write_text(payload, encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def command(
    args: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
    text: bool = True,
    input_data: bytes | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    process = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=text,
        input=input_data,
        env=env,
        shell=False,
    )
    if check and process.returncode:
        stderr = process.stderr if text else process.stderr.decode("utf-8", "replace")
        raise BenchmarkError(f"Command failed ({process.returncode}): {' '.join(args)}\n{stderr}")
    return process


def git(repository: Path, *args: str, text: bool = True) -> str | bytes:
    result = command(["git", *args], cwd=repository, text=text)
    return result.stdout


def implementation_path(path: str) -> bool:
    return any(
        path == candidate or (candidate.endswith("/") and path.startswith(candidate))
        for candidate in IMPLEMENTATION_PATHS
    )


def file_bytes(path: Path) -> bytes:
    if path.is_symlink():
        return os.readlink(path).encode("utf-8")
    return path.read_bytes()


def shiftory_environment(root: Path | None = None) -> dict[str, str]:
    root = ROOT if root is None else root
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment["PYTHONPATH"] = str((root / "src").resolve())
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def shiftory_process(
    args: list[str], *, root: Path | None = None
) -> subprocess.CompletedProcess[str]:
    root = ROOT if root is None else root
    source = (root / "src").resolve(strict=True)
    return command(
        [sys.executable, "-I", "-B", "-c", SHIFTORY_BOOTSTRAP, str(source), *args],
        cwd=root,
        env=shiftory_environment(root),
    )


def executed_package_identity(root: Path | None = None) -> dict[str, Any]:
    root = ROOT if root is None else root
    source = (root / "src").resolve(strict=True)
    process = command(
        [sys.executable, "-I", "-B", "-c", SHIFTORY_IDENTITY_PROBE, str(source)],
        cwd=root,
        env=shiftory_environment(root),
    )
    try:
        identity = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise BenchmarkError("Shiftory execution identity probe returned invalid JSON") from error
    expected = {
        "distribution": "shiftory",
        "import_root": "repository:src",
        "module_file": "src/shiftory/__init__.py",
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise BenchmarkError("Shiftory execution probe did not resolve from repository:src")
    return identity


def implementation_identity(root: Path | None = None) -> dict[str, Any]:
    root = ROOT if root is None else root
    commit = str(git(root, "rev-parse", "--verify", "HEAD^{commit}")).strip()
    if not SHA_RE.fullmatch(commit):
        raise BenchmarkError(f"Shiftory HEAD is not a full commit SHA: {commit!r}")
    tree = str(git(root, "rev-parse", "--verify", "HEAD^{tree}")).strip()
    status = str(git(root, "status", "--porcelain=v1", "--untracked-files=all"))
    clean = not status.strip()

    raw_tree = git(root, "ls-tree", "-r", "-z", "HEAD", text=False)
    assert isinstance(raw_tree, bytes)
    committed_blobs: dict[str, str] = {}
    for record in raw_tree.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        committed_blobs[path] = metadata.split()[2].decode("ascii")

    raw_paths = git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        text=False,
    )
    assert isinstance(raw_paths, bytes)
    paths = sorted(
        raw_path.decode("utf-8")
        for raw_path in raw_paths.split(b"\0")
        if raw_path and implementation_path(raw_path.decode("utf-8"))
    )
    files = []
    for path in paths:
        absolute = root / path
        if not absolute.is_file():
            raise BenchmarkError(f"Benchmark implementation path is not a file: {path}")
        payload = file_bytes(absolute)
        files.append(
            {
                "path": path,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "committed_blob": committed_blobs.get(path),
            }
        )
    manifest_payload = {"selection": list(IMPLEMENTATION_PATHS), "files": files}
    manifest_sha256 = hashlib.sha256(canonical_json(manifest_payload).encode("utf-8")).hexdigest()
    package_code_files = [
        {
            "path": entry["path"].removeprefix("src/"),
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
        }
        for entry in files
        if entry["path"].startswith("src/shiftory/") and entry["path"].endswith(".py")
    ]
    package_code_payload = {"files": package_code_files}
    package_code_sha256 = hashlib.sha256(
        canonical_json(package_code_payload).encode("utf-8")
    ).hexdigest()

    by_path = {entry["path"]: entry for entry in files}
    runner = by_path.get("benchmarks/runner.py")
    if runner is None:
        raise BenchmarkError("benchmarks/runner.py is missing from the implementation manifest")
    golden_files = [
        entry
        for entry in files
        if entry["path"] == "benchmarks/scenarios.toml"
        or entry["path"].startswith(("benchmarks/golden/", "benchmarks/fixtures/"))
    ]
    golden_sha256 = hashlib.sha256(canonical_json(golden_files).encode("utf-8")).hexdigest()

    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        raise BenchmarkError("pyproject.toml is missing from the benchmark implementation")
    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project", {})
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or dependencies.count(GRAPHORA_REQUIREMENT) != 1:
        raise BenchmarkError(
            f"benchmark implementation must declare exactly {GRAPHORA_REQUIREMENT}"
        )
    distributions = {}
    for name in ("shiftory", "graphora-kg", "tree-sitter", "jsonschema", "platformdirs"):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            distributions[name] = None
    if distributions["graphora-kg"] != GRAPHORA_VERSION:
        raise BenchmarkError(f"benchmark environment requires graphora-kg=={GRAPHORA_VERSION}")
    if distributions["tree-sitter"] != TREE_SITTER_VERSION:
        raise BenchmarkError(f"benchmark environment requires tree-sitter=={TREE_SITTER_VERSION}")
    return {
        "schema": "shiftory.benchmark-implementation/v1",
        "shiftory_commit": commit,
        "shiftory_tree": tree,
        "shiftory_worktree_clean": clean,
        "manifest": {
            **manifest_payload,
            "sha256": manifest_sha256,
        },
        "runner_sha256": runner["sha256"],
        "golden_inputs_sha256": golden_sha256,
        "package_code": {
            **package_code_payload,
            "sha256": package_code_sha256,
        },
        "package": {
            "name": project.get("name"),
            "version": project.get("version"),
            "requires_python": project.get("requires-python"),
            "dependencies": dependencies,
            "installed_distributions": distributions,
        },
    }


def require_bound_execution(
    implementation: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    execution = executed_package_identity(root)
    if execution["package_code"] != implementation["package_code"]:
        raise BenchmarkError(
            "Executed Shiftory package code differs from this repository's ROOT/src implementation"
        )
    return execution


def evidence_renderer_identity(
    execution: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    root = ROOT if root is None else root
    source = (root / "src").resolve(strict=True)
    expected_renderer = (source / "shiftory" / "render" / "evidence.py").resolve(strict=True)
    if execution.get("module_file") != "src/shiftory/__init__.py":
        raise BenchmarkError("Recorded Shiftory execution did not resolve from ROOT/src")
    if execution.get("import_root") != "repository:src":
        raise BenchmarkError("Recorded Shiftory execution source root differs from ROOT/src")
    renderer_entry = next(
        (
            entry
            for entry in execution.get("package_code", {}).get("files", [])
            if entry.get("path") == "shiftory/render/evidence.py"
        ),
        None,
    )
    if renderer_entry is None:
        raise BenchmarkError("Recorded execution does not include the evidence renderer")
    return {
        "schema": "shiftory.benchmark-renderer/v1",
        "distribution": "shiftory",
        "module": "shiftory.render.evidence",
        "module_file": expected_renderer.relative_to(root).as_posix(),
        "module_sha256": renderer_entry["sha256"],
        "package_code_sha256": execution["package_code"]["sha256"],
    }


def require_publishable_source(root: Path | None = None) -> dict[str, Any]:
    root = ROOT if root is None else root
    identity = implementation_identity(root)
    if not identity["shiftory_worktree_clean"]:
        raise BenchmarkError(
            "Publication requires a clean Shiftory worktree at a committed revision; "
            "run suite without --publish for pre-commit validation"
        )
    require_bound_execution(identity, root)
    return identity


def load_scenarios() -> dict[str, dict[str, Any]]:
    data = tomllib.loads(SCENARIOS.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise BenchmarkError("Unsupported scenarios.toml schema")
    scenarios: dict[str, dict[str, Any]] = {}
    for raw in data.get("scenario", []):
        scenario = dict(raw)
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not SCENARIO_ID_RE.fullmatch(scenario_id):
            raise BenchmarkError("Every scenario needs a safe lowercase id")
        if scenario_id in scenarios:
            raise BenchmarkError(f"Duplicate scenario id: {scenario_id}")
        for key in ("base", "head"):
            if not SHA_RE.fullmatch(str(scenario.get(key, ""))):
                raise BenchmarkError(f"{scenario_id} has an invalid full {key} SHA")
        scenarios[scenario_id] = scenario
    if len(scenarios) != 3:
        raise BenchmarkError(f"Expected exactly three public scenarios, found {len(scenarios)}")
    return scenarios


def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BenchmarkError(f"Unsafe relative path: {value!r}")
    return path


def canonical_remote(value: str) -> str:
    return value.rstrip("/").removesuffix(".git")


def detect_license(path: str, content: bytes) -> str:
    text = content.decode("utf-8", "replace").lower()
    if "free and unencumbered software released into the public domain" in text:
        return "Unlicense"
    if "permission is hereby granted, free of charge" in text:
        return "MIT"
    if (
        "redistribution and use in source and binary forms" in text
        and "neither the name" in text
        and "contributors may be used to endorse or promote" in text
    ):
        return "BSD-3-Clause"
    raise BenchmarkError(f"Unrecognized license text in {path}")


def verify_licenses(repository: Path, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    detected = []
    for raw_path in scenario["license_files"]:
        path = safe_relative(raw_path).as_posix()
        process = command(
            ["git", "show", f"{scenario['head']}:{path}"],
            cwd=repository,
            text=False,
            check=False,
        )
        if process.returncode:
            raise BenchmarkError(f"{scenario['id']} is missing license file {path} at head")
        content = process.stdout
        if not content:
            raise BenchmarkError(f"{scenario['id']} has an empty license file {path}")
        identifier = detect_license(path, content)
        detected.append(identifier)
        records.append(
            {
                "path": path,
                "spdx": identifier,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    expected = {
        part.strip() for part in re.split(r"\s+(?:OR|AND)\s+", scenario["license_expression"])
    }
    if set(detected) != expected:
        raise BenchmarkError(
            f"{scenario['id']} license mismatch: expected {sorted(expected)}, "
            f"detected {sorted(set(detected))}"
        )
    return records


def input_inventory(repository: Path, base: str, head: str) -> dict[str, int]:
    patch = git(repository, "diff", *DIFF_ARGS, base, head, text=False)
    assert isinstance(patch, bytes)
    names = git(repository, "diff", *DIFF_ARGS[:-1], "--name-only", "-z", base, head, text=False)
    assert isinstance(names, bytes)
    numstat = git(repository, "diff", *DIFF_ARGS[:-1], "--numstat", "-z", base, head, text=False)
    assert isinstance(numstat, bytes)
    added = deleted = 0
    for record in numstat.split(b"\0"):
        if not record:
            continue
        fields = record.split(b"\t", 2)
        if len(fields) >= 2 and fields[0] != b"-" and fields[1] != b"-":
            added += int(fields[0])
            deleted += int(fields[1])
    return {
        "files": len([name for name in names.split(b"\0") if name]),
        "hunks": sum(line.startswith(b"@@ ") for line in patch.splitlines()),
        "added_lines": added,
        "deleted_lines": deleted,
        "raw_patch_bytes": len(patch),
    }


def check_expected_inventory(scenario: dict[str, Any], inventory: dict[str, int]) -> None:
    expected = scenario["expected_inventory"]
    actual = {key: inventory[key] for key in expected}
    if actual != expected:
        raise BenchmarkError(
            f"{scenario['id']} Git inventory mismatch: expected {expected}, got {actual}"
        )


def acquire_scenario(
    scenario: dict[str, Any], workspace: Path, *, fetch: bool = True
) -> dict[str, Any]:
    repository = workspace / "sources" / scenario["id"]
    started = time.perf_counter_ns()
    if not repository.exists():
        if not fetch:
            raise BenchmarkError(f"Source is not acquired: {repository}")
        repository.parent.mkdir(parents=True, exist_ok=True)
        command(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--no-tags",
                scenario["repository"],
                str(repository),
            ]
        )
    if not (repository / ".git").is_dir():
        raise BenchmarkError(f"Refusing non-Git source directory: {repository}")
    remote = str(git(repository, "remote", "get-url", "origin")).strip()
    if canonical_remote(remote) != canonical_remote(scenario["repository"]):
        raise BenchmarkError(
            f"{scenario['id']} origin mismatch: expected {scenario['repository']}, got {remote}"
        )
    refs = {
        "base": f"refs/shiftory-benchmark/{scenario['id']}/base",
        "head": f"refs/shiftory-benchmark/{scenario['id']}/head",
    }
    if fetch:
        command(
            [
                "git",
                "fetch",
                "--force",
                "--no-tags",
                "origin",
                f"+{scenario['base']}:{refs['base']}",
                f"+{scenario['head']}:{refs['head']}",
            ],
            cwd=repository,
        )
    resolved = {}
    for side in ("base", "head"):
        process = command(
            ["git", "rev-parse", "--verify", f"{refs[side]}^{{commit}}"],
            cwd=repository,
            check=False,
        )
        if process.returncode:
            raise BenchmarkError(f"{scenario['id']} has no acquired {side} ref; run acquire")
        resolved[side] = process.stdout.strip()
        if resolved[side] != scenario[side]:
            raise BenchmarkError(
                f"{scenario['id']} {side} ref mismatch: expected {scenario[side]}, "
                f"got {resolved[side]}"
            )
    command(["git", "checkout", "--quiet", "--force", "--detach", scenario["head"]], cwd=repository)
    inventory = input_inventory(repository, scenario["base"], scenario["head"])
    check_expected_inventory(scenario, inventory)
    manifest = {
        "schema": "shiftory.benchmark-acquisition/v1",
        "scenario_id": scenario["id"],
        "repository": scenario["repository"],
        "remote_verified": True,
        "commits": resolved,
        "licenses": verify_licenses(repository, scenario),
        "inventory": inventory,
        "acquisition_wall_seconds": (time.perf_counter_ns() - started) / 1_000_000_000,
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(
        workspace / "acquisitions" / scenario["id"] / "acquisition-v1.json",
        pretty_json(manifest),
    )
    return {"repository": repository, "manifest": manifest}


def reconstruct_offline_fixture(workspace: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    fixture = BENCHMARKS / "fixtures" / "offline-smoke"
    metadata = json.loads((fixture / "metadata.json").read_text(encoding="utf-8"))
    repository = workspace / "sources" / metadata["id"]
    shutil.rmtree(repository, ignore_errors=True)
    repository.mkdir(parents=True)
    command(["git", "init", "--quiet"], cwd=repository)
    history = (fixture / metadata["history"]).read_bytes()
    command(["git", "fast-import", "--quiet"], cwd=repository, text=False, input_data=history)
    command(["git", "checkout", "--quiet", metadata["import_ref"]], cwd=repository)
    scenario = {
        "id": metadata["id"],
        "name": "Offline complete-path smoke",
        "repository": "committed fast-import fixture",
        "base": metadata["expected_commits"]["base"],
        "head": metadata["expected_commits"]["head"],
        "expected_inventory": metadata["expected_inventory"],
        "assertions": f"fixtures/offline-smoke/{metadata['assertions']}",
        "explanation_template": (f"fixtures/offline-smoke/{metadata['explanation_template']}"),
        "network_required": False,
    }
    resolved = {
        side: str(git(repository, "rev-parse", "--verify", f"{sha}^{{commit}}")).strip()
        for side, sha in metadata["expected_commits"].items()
    }
    if resolved != metadata["expected_commits"]:
        raise BenchmarkError(f"Offline fixture commit mismatch: {resolved}")
    inventory = input_inventory(repository, scenario["base"], scenario["head"])
    check_expected_inventory(scenario, inventory)
    acquisition = {
        "schema": "shiftory.benchmark-acquisition/v1",
        "scenario_id": scenario["id"],
        "repository": scenario["repository"],
        "remote_verified": False,
        "commits": resolved,
        "licenses": [],
        "inventory": inventory,
        "acquisition_wall_seconds": 0.0,
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    return scenario, repository, acquisition


def actual_hunk_map(evidence: dict[str, Any]) -> dict[str, str]:
    result = {}
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        for index, hunk in enumerate(file["hunks"], 1):
            result[f"conceptual-hunk:{path}#{index}"] = hunk["id"]
    return result


def actual_unit_map(evidence: dict[str, Any]) -> dict[str, str]:
    result = {}
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        for index, unit in enumerate(file["units"], 1):
            result[f"conceptual-unit:{path}#{index}"] = unit["id"]
    return result


def instantiate_manifest(
    template_document: dict[str, Any], evidence: dict[str, Any], scenario_id: str
) -> dict[str, Any]:
    if template_document.get("template_schema") != "shiftory.benchmark-explanation-template/v1":
        raise BenchmarkError("Unsupported explanation template schema")
    if template_document.get("target_schema") != "shiftory.explanation/v1":
        raise BenchmarkError("Explanation template targets an unsupported schema")
    if template_document.get("scenario_id") != scenario_id:
        raise BenchmarkError("Explanation template scenario id does not match")
    manifest = copy.deepcopy(template_document["manifest"])
    hunks = actual_hunk_map(evidence)
    units = actual_unit_map(evidence)
    concepts = {**hunks, **units}
    owner_by_concept: dict[str, str] = {}
    for owner in manifest["coverage_owners"]:
        concept = owner["evidence_id"]
        if concept in owner_by_concept:
            raise BenchmarkError(f"Template has duplicate owner for {concept}")
        if concept not in concepts:
            raise BenchmarkError(f"Template references missing conceptual evidence {concept}")
        owner_by_concept[concept] = owner["owner_id"]
    if set(hunks) != {key for key in owner_by_concept if CONCEPT_HUNK_RE.fullmatch(key)}:
        missing = sorted(set(hunks) - set(owner_by_concept))
        extra = sorted(
            key for key in owner_by_concept if CONCEPT_HUNK_RE.fullmatch(key) and key not in hunks
        )
        raise BenchmarkError(f"Template hunk inventory mismatch; missing={missing}, extra={extra}")
    for item in manifest["items"]:
        citations = []
        for citation in item.get("citations", []):
            identity = citation.get("id") if isinstance(citation, dict) else citation
            if identity in concepts:
                replacement = concepts[identity]
                citations.append(
                    {**citation, "id": replacement} if isinstance(citation, dict) else replacement
                )
            else:
                citations.append(citation)
        item["citations"] = citations
    owners = []
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        hunk_owner = {
            hunk["id"]: owner_by_concept[f"conceptual-hunk:{path}#{index}"]
            for index, hunk in enumerate(file["hunks"], 1)
        }
        for hunk in file["hunks"]:
            for line in hunk["lines"]:
                owners.append({"evidence_id": line["id"], "owner_id": hunk_owner[hunk["id"]]})
        for index, unit in enumerate(file["units"], 1):
            if unit["kind"] != "text":
                concept = f"conceptual-unit:{path}#{index}"
                owner = owner_by_concept.get(concept)
                if owner is None:
                    file_owners = set(hunk_owner.values())
                    if len(file_owners) != 1:
                        raise BenchmarkError(f"Non-text unit lacks an unambiguous owner: {concept}")
                    owner = next(iter(file_owners))
                owners.append({"evidence_id": unit["id"], "owner_id": owner})
    manifest["coverage_owners"] = sorted(
        owners, key=lambda value: (value["evidence_id"], value["owner_id"])
    )
    return manifest


def source_text(repository: Path, revision: str, path: str) -> tuple[bool, str]:
    safe_relative(path)
    process = command(
        ["git", "show", f"{revision}:{path}"], cwd=repository, check=False, text=False
    )
    if process.returncode:
        return False, ""
    return True, process.stdout.decode("utf-8", "replace")


def result(
    assertion_id: str,
    domain: str,
    target: str,
    passed: bool | None,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": assertion_id,
        "domain": domain,
        "target": target,
        "status": "skip" if passed is None else "pass" if passed else "fail",
        "detail": detail,
    }


def evaluate_assertions(
    document: dict[str, Any],
    scenario: dict[str, Any],
    repository: Path,
    evidence: dict[str, Any],
    manifest: dict[str, Any],
    verify: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    if document.get("schema") != "shiftory.benchmark-source-assertions/v1":
        raise BenchmarkError("Unsupported benchmark assertions schema")
    if (
        document.get("scenario_id") != scenario["id"]
        or document.get("base") != scenario["base"]
        or document.get("head") != scenario["head"]
    ):
        raise BenchmarkError("Assertion identity does not match the scenario")
    results = [
        result(
            "evidence-base-sha",
            "evidence",
            "$.comparison.base_sha",
            evidence["comparison"]["base_sha"] == scenario["base"],
            evidence["comparison"]["base_sha"],
        ),
        result(
            "evidence-head-sha",
            "evidence",
            "$.comparison.head_sha",
            evidence["comparison"]["head_sha"] == scenario["head"],
            evidence["comparison"]["head_sha"],
        ),
        result(
            "verify-valid",
            "report",
            "$.verify.valid",
            verify.get("valid") is True,
            f"valid={verify.get('valid')!r}",
        ),
    ]
    for key, expected in scenario["expected_inventory"].items():
        actual = evidence["metrics"][key]
        results.append(
            result(
                f"evidence-inventory-{key}",
                "evidence",
                f"$.metrics.{key}",
                actual == expected,
                f"expected={expected}, actual={actual}",
            )
        )
    item_by_id = {item["id"]: item for item in manifest["items"]}
    section_by_item = {
        item["id"]: section for section, items in report["sections"].items() for item in items
    }
    evidence_ids = {
        entry["id"]
        for file in evidence["files"]
        for category in ("units", "hunks", "spans", "citations")
        for entry in file.get(category, [])
    }
    evidence_ids.update(
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    )
    evidence_ids.update(fact["id"] for fact in evidence["graph"].get("facts", []))
    for assertion in document["assertions"]:
        assertion_id = assertion["id"]
        path = assertion["path"]
        behavior_id = assertion["behavior_id"]
        for side, revision in (("before", scenario["base"]), ("after", scenario["head"])):
            exists, text = source_text(repository, revision, path)
            conditions = assertion[side]
            results.append(
                result(
                    f"{assertion_id}:{side}:file-exists",
                    f"{side}-source",
                    path,
                    exists is conditions["file_exists"],
                    f"expected={conditions['file_exists']}, actual={exists}",
                )
            )
            for index, needle in enumerate(conditions.get("contains", []), 1):
                results.append(
                    result(
                        f"{assertion_id}:{side}:contains:{index}",
                        f"{side}-source",
                        path,
                        exists and needle in text,
                        f"contains {needle!r}",
                    )
                )
            for index, needle in enumerate(conditions.get("not_contains", []), 1):
                results.append(
                    result(
                        f"{assertion_id}:{side}:not-contains:{index}",
                        f"{side}-source",
                        path,
                        (not exists) or needle not in text,
                        f"does not contain {needle!r}",
                    )
                )
        item = item_by_id.get(behavior_id)
        results.append(
            result(
                f"{assertion_id}:evidence-citations",
                "evidence",
                behavior_id,
                bool(item)
                and bool(item.get("citations"))
                and all(
                    (citation.get("id") if isinstance(citation, dict) else citation) in evidence_ids
                    for citation in item["citations"]
                ),
                "behavior item exists and all citations resolve",
            )
        )
        expected_section = (
            "ambiguity"
            if item and item["kind"] == "unresolved"
            else (item["kind"] if item else "missing")
        )
        results.append(
            result(
                f"{assertion_id}:report-section",
                "report",
                behavior_id,
                section_by_item.get(behavior_id) == expected_section,
                f"expected={expected_section}, actual={section_by_item.get(behavior_id)}",
            )
        )
    graph = evidence["graph"]
    if graph["status"] == "available":
        facts = graph.get("facts", [])
        valid = all(
            isinstance(fact.get("id"), str)
            and fact.get("provenance")
            and fact.get("side") in {"before", "after"}
            for fact in facts
        )
        results.append(
            result(
                "graph-fact-integrity",
                "graph",
                "$.graph.facts",
                valid,
                f"facts={len(facts)}",
            )
        )
    else:
        results.append(
            result(
                "graph-fact-integrity",
                "graph",
                "$.graph.facts",
                None,
                f"graph status is {graph['status']}",
            )
        )
    counts = {
        status: sum(entry["status"] == status for entry in results)
        for status in ("pass", "fail", "skip")
    }
    return {
        "schema": "shiftory.benchmark-assertion-results/v1",
        "scenario_id": scenario["id"],
        "passed": counts["fail"] == 0,
        "counts": counts,
        "results": results,
    }


def cache_status(repository: Path, cache_dir: Path) -> dict[str, Any]:
    process = shiftory_process(
        [
            "cache",
            "status",
            "--repo",
            str(repository),
            "--cache-dir",
            str(cache_dir),
        ],
    )
    value = json.loads(process.stdout)
    return {
        "exists": value["exists"],
        "files": value["files"],
        "file_count": len(value["files"]),
    }


def run_cli(args: list[str], *, output: Path | None = None) -> tuple[Any, float]:
    started = time.perf_counter_ns()
    process = shiftory_process(args)
    elapsed = (time.perf_counter_ns() - started) / 1_000_000_000
    if output is not None and not output.is_file():
        raise BenchmarkError(f"Shiftory did not produce expected output: {output}")
    return process, elapsed


def render_evidence(
    evidence: dict[str, Any],
    execution: dict[str, Any],
    *,
    root: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    root = ROOT if root is None else root
    source = (root / "src").resolve(strict=True)
    process = command(
        [sys.executable, "-I", "-B", "-c", SHIFTORY_EVIDENCE_RENDERER, str(source)],
        cwd=root,
        text=False,
        input_data=canonical_json(evidence).encode("utf-8"),
        env=shiftory_environment(root),
    )
    try:
        rendered = json.loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BenchmarkError("Evidence renderer subprocess returned invalid JSON") from error
    if rendered.get("schema") != "shiftory.benchmark-render-result/v1":
        raise BenchmarkError("Evidence renderer subprocess returned an unsupported result")
    if rendered.get("package_code") != execution.get("package_code"):
        raise BenchmarkError(
            "Evidence renderer executed code other than the recorded Shiftory implementation"
        )
    expected_identity = evidence_renderer_identity(execution, root)
    if rendered.get("renderer") != expected_identity:
        raise BenchmarkError(
            "Evidence renderer module path or digest differs from the recorded implementation"
        )
    markdown = rendered.get("markdown")
    if not isinstance(markdown, str):
        raise BenchmarkError("Evidence renderer subprocess returned non-text Markdown")
    return markdown, expected_identity


def artifact_sizes(paths: dict[str, Path]) -> dict[str, int]:
    return {f"{name}_bytes": path.stat().st_size for name, path in paths.items()}


NON_SEMANTIC_KEYS = {
    "acquired_at_utc",
    "acquisition_wall_seconds",
    "cache_dir",
    "cache_path",
    "direct_url",
    "duration",
    "elapsed",
    "install_path",
    "module_file",
    "paths",
    "recorded_at_utc",
    "shiftory_file",
    "source_root",
    "timestamp",
    "timings",
    "wall_seconds",
    "workspace",
    "workspace_path",
}
ABSOLUTE_PATH_IN_TEXT_RE = re.compile(r"(?<![:/A-Za-z0-9.])/(?:[^/\s`\"'<>|]+/)+[^/\s`\"'<>|,;)]*")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\s`\"'<>|]+")
LOCAL_FILE_URI_RE = re.compile(r"(?i)\bfile:///[^\s`\"'<>|]+")
PRIVATE_PATH_MARKERS = (
    "/site-packages/",
    "\\site-packages\\",
    "/.cache/",
    "\\.cache\\",
    "/copilot-worktrees/",
    "\\copilot-worktrees\\",
)


def _normalized_roots(roots: tuple[Path, ...]) -> tuple[str, ...]:
    values = {
        str(root.expanduser().resolve()) for root in roots if str(root) not in {"", ".", os.sep}
    }
    return tuple(sorted(values, key=len, reverse=True))


def _semantic_value(value: Any, roots: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: _semantic_value(item, roots)
            for key, item in sorted(value.items())
            if key not in NON_SEMANTIC_KEYS
            and not key.endswith(("_at_utc", "_duration", "_elapsed", "_seconds"))
        }
    if isinstance(value, list):
        return [_semantic_value(item, roots) for item in value]
    if isinstance(value, str):
        result = value
        for root in roots:
            result = result.replace(root, "<local-root>")
        result = WINDOWS_ABSOLUTE_PATH_RE.sub("<local-path>", result)
        return ABSOLUTE_PATH_IN_TEXT_RE.sub("<local-path>", result)
    return value


def semantic_bundle(
    paths: dict[str, Path],
    assertions: dict[str, Any],
    provenance: dict[str, str],
    *,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[str, dict[str, str]]:
    hashes = {}
    bundle: dict[str, Any] = {}
    roots = _normalized_roots(excluded_roots)
    for name, path in sorted(paths.items()):
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        if path.suffix == ".json":
            bundle[name] = _semantic_value(json.loads(raw), roots)
        else:
            bundle[name] = _semantic_value(raw.decode("utf-8"), roots)
    bundle["assertions"] = _semantic_value(assertions, roots)
    bundle["verified_provenance"] = provenance
    payload = canonical_json(bundle).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), hashes


def _private_artifact_reason(payload: str, roots: tuple[Path, ...]) -> str | None:
    for root in _normalized_roots(roots):
        if root in payload:
            return f"local root {root!r}"
    lowered = payload.lower()
    for marker in PRIVATE_PATH_MARKERS:
        if marker.lower() in lowered:
            return f"private path marker {marker!r}"
    if (
        WINDOWS_ABSOLUTE_PATH_RE.search(payload)
        or ABSOLUTE_PATH_IN_TEXT_RE.search(payload)
        or LOCAL_FILE_URI_RE.search(payload)
    ):
        return "absolute local path"
    home = Path.home()
    usernames = {
        home.name,
        getpass.getuser(),
        os.environ.get("USER", ""),
        os.environ.get("USERNAME", ""),
    } - {"", "root", "runner"}
    for username in usernames:
        if re.search(
            rf"(?i)(?<![A-Za-z0-9_.-]){re.escape(username)}(?![A-Za-z0-9_.-])",
            payload,
        ):
            return "local username"
    return None


def _artifact_strings(value: Any, path: str = "$") -> list[tuple[str, str]]:
    strings: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}[{json.dumps(key, ensure_ascii=True)}]"
            strings.append((f"{key_path}<key>", key))
            strings.extend(_artifact_strings(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            strings.extend(_artifact_strings(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        strings.append((path, value))
    return strings


def validate_public_artifacts(
    metrics: dict[str, Any],
    report: str,
    *,
    roots: tuple[Path, ...],
) -> None:
    sources = (
        ("metrics JSON", _artifact_strings(metrics)),
        (
            "report Markdown",
            [(f"line {number}", line) for number, line in enumerate(report.splitlines(), 1)],
        ),
    )
    for name, strings in sources:
        for location, payload in strings:
            reason = _private_artifact_reason(payload, roots)
            if reason is not None:
                raise BenchmarkError(f"Refusing to publish {name} {location} containing {reason}")


def run_complete_path(
    scenario: dict[str, Any],
    repository: Path,
    workspace: Path,
    cache_dir: Path,
    label: str,
    graphora: str,
    execution: dict[str, Any],
    implementation: dict[str, Any],
) -> dict[str, Any]:
    run_dir = workspace / "runs" / scenario["id"] / label
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    paths = {
        "evidence_json": run_dir / "evidence.json",
        "evidence_markdown": run_dir / "evidence.md",
        "explanation_json": run_dir / "explanation.json",
        "verify_json": run_dir / "verify.json",
        "report_json": run_dir / "shiftory-report.json",
        "report_markdown": run_dir / "shiftory-report.md",
        "assertions_json": run_dir / "assertions.json",
    }
    cache_before = cache_status(repository, cache_dir)
    phases: dict[str, float] = {}
    _, phases["analyze"] = run_cli(
        [
            "analyze",
            "--repo",
            str(repository),
            "--range",
            f"{scenario['base']}..{scenario['head']}",
            "--graphora",
            graphora,
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(paths["evidence_json"]),
        ],
        output=paths["evidence_json"],
    )
    evidence = json.loads(paths["evidence_json"].read_text(encoding="utf-8"))
    check_expected_inventory(
        scenario, {key: int(evidence["metrics"][key]) for key in scenario["expected_inventory"]}
    )
    complete_started = time.perf_counter_ns()
    started = time.perf_counter_ns()
    evidence_markdown, renderer_identity = render_evidence(evidence, execution)
    atomic_write(paths["evidence_markdown"], evidence_markdown)
    phases["render_evidence_markdown"] = (time.perf_counter_ns() - started) / 1_000_000_000
    started = time.perf_counter_ns()
    template = json.loads(
        (BENCHMARKS / safe_relative(scenario["explanation_template"])).read_text(encoding="utf-8")
    )
    manifest = instantiate_manifest(template, evidence, scenario["id"])
    atomic_write(paths["explanation_json"], canonical_json(manifest))
    phases["instantiate_manifest"] = (time.perf_counter_ns() - started) / 1_000_000_000
    verified, phases["verify"] = run_cli(
        [
            "verify",
            "--evidence",
            str(paths["evidence_json"]),
            "--explanation",
            str(paths["explanation_json"]),
        ]
    )
    atomic_write(paths["verify_json"], verified.stdout)
    _, phases["render_json"] = run_cli(
        [
            "render",
            "--format",
            "json",
            "--evidence",
            str(paths["evidence_json"]),
            "--explanation",
            str(paths["explanation_json"]),
            "--output",
            str(paths["report_json"]),
        ],
        output=paths["report_json"],
    )
    _, phases["render_markdown"] = run_cli(
        [
            "render",
            "--format",
            "markdown",
            "--evidence",
            str(paths["evidence_json"]),
            "--explanation",
            str(paths["explanation_json"]),
            "--output",
            str(paths["report_markdown"]),
        ],
        output=paths["report_markdown"],
    )
    complete_seconds = (
        phases["analyze"] + (time.perf_counter_ns() - complete_started) / 1_000_000_000
    )
    assertion_document = json.loads(
        (BENCHMARKS / safe_relative(scenario["assertions"])).read_text(encoding="utf-8")
    )
    assertions = evaluate_assertions(
        assertion_document,
        scenario,
        repository,
        evidence,
        manifest,
        json.loads(paths["verify_json"].read_text(encoding="utf-8")),
        json.loads(paths["report_json"].read_text(encoding="utf-8")),
    )
    atomic_write(paths["assertions_json"], canonical_json(assertions))
    cache_after = cache_status(repository, cache_dir)
    digest, hashes = semantic_bundle(
        paths,
        assertions,
        {
            "shiftory_package_code_sha256": execution["package_code"]["sha256"],
            "evidence_renderer_sha256": renderer_identity["module_sha256"],
            "runner_sha256": implementation["runner_sha256"],
            "golden_inputs_sha256": implementation["golden_inputs_sha256"],
        },
        excluded_roots=(ROOT, repository, workspace, cache_dir, Path.home()),
    )
    if not assertions["passed"]:
        failed = [entry["id"] for entry in assertions["results"] if entry["status"] == "fail"]
        raise BenchmarkError(f"{scenario['id']} assertions failed: {', '.join(failed)}")
    return {
        "label": label,
        "commands": [
            [
                "shiftory",
                "analyze",
                "--repo",
                "<repository>",
                "--range",
                f"{scenario['base']}..{scenario['head']}",
                "--graphora",
                graphora,
                "--cache-dir",
                "<scenario-cache>",
                "--output",
                "<run>/evidence.json",
            ],
            ["shiftory-python", "render-evidence-markdown", "--evidence", "<run>/evidence.json"],
            ["benchmark-runner", "instantiate-manifest"],
            [
                "shiftory",
                "verify",
                "--evidence",
                "<run>/evidence.json",
                "--explanation",
                "<run>/explanation.json",
            ],
            [
                "shiftory",
                "render",
                "--format",
                "json",
                "--evidence",
                "<run>/evidence.json",
                "--explanation",
                "<run>/explanation.json",
            ],
            [
                "shiftory",
                "render",
                "--format",
                "markdown",
                "--evidence",
                "<run>/evidence.json",
                "--explanation",
                "<run>/explanation.json",
            ],
        ],
        "wall_seconds": {"complete_path": complete_seconds, "phases": phases},
        "sizes": artifact_sizes(paths),
        "cache": {
            "before": cache_before,
            "after": cache_after,
            "graph_status": evidence["graph"]["status"],
            "graph_cache_key": evidence["graph"].get("cache_key"),
        },
        "coverage": json.loads(paths["verify_json"].read_text(encoding="utf-8"))["coverage"],
        "assertions": assertions,
        "evidence_markdown_renderer": renderer_identity,
        "canonical_output_sha256": digest,
        "artifact_sha256": hashes,
        "paths": {name: str(path) for name, path in paths.items()},
    }


def memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def environment_facts(implementation: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    shiftory = shiftory_process(["--version"]).stdout.strip()
    git_version = command(["git", "--version"]).stdout.strip()
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes(),
        "python": sys.version.splitlines()[0],
        "git": git_version,
        "shiftory": shiftory,
        "shiftory_commit": implementation["shiftory_commit"],
        "shiftory_worktree_dirty": not implementation["shiftory_worktree_clean"],
        "shiftory_distribution": execution["distribution"],
        "shiftory_module": execution["module_file"],
        "executed_package_code_sha256": execution["package_code"]["sha256"],
        "dependencies": implementation["package"]["installed_distributions"],
    }


def benchmark_report(metrics: dict[str, Any], shiftory_report: str) -> str:
    scenario = metrics["scenario"]
    cold, warm = metrics["runs"][0], metrics["runs"][1]
    coverage = warm["coverage"]
    assertions = warm["assertions"]["counts"]
    lines = [
        f"# {scenario['name']}",
        "",
        f"Scenario: `{scenario['id']}`",
        "",
        f"Comparison: `{scenario['base']}..{scenario['head']}`",
        "",
        "## Measured results",
        "",
        "| Measure | Cold | Warm |",
        "|---|---:|---:|",
        (
            f"| Complete path wall time (seconds) | "
            f"{cold['wall_seconds']['complete_path']:.6f} | "
            f"{warm['wall_seconds']['complete_path']:.6f} |"
        ),
        (
            f"| Evidence JSON (bytes) | {cold['sizes']['evidence_json_bytes']} | "
            f"{warm['sizes']['evidence_json_bytes']} |"
        ),
        (
            f"| Evidence Markdown (bytes) | {cold['sizes']['evidence_markdown_bytes']} | "
            f"{warm['sizes']['evidence_markdown_bytes']} |"
        ),
        (
            f"| Shiftory report Markdown (bytes) | "
            f"{cold['sizes']['report_markdown_bytes']} | "
            f"{warm['sizes']['report_markdown_bytes']} |"
        ),
        "",
        "## Input and accounting",
        "",
        (
            f"- {metrics['input']['files']} files, {metrics['input']['hunks']} hunks, "
            f"{metrics['input']['spans']} spans, {metrics['input']['added_lines']} added "
            f"and {metrics['input']['deleted_lines']} deleted lines"
        ),
        f"- Raw Git patch: {metrics['input']['raw_patch_bytes']} bytes",
        (
            f"- Coverage: lines {coverage['line_owned']}/{coverage['line_total']}, "
            f"hunks {coverage['hunk_covered']}/{coverage['hunk_total']}, "
            f"units {coverage['unit_covered']}/{coverage['unit_total']}"
        ),
        (
            f"- Assertions: {assertions['pass']} passed, {assertions['fail']} failed, "
            f"{assertions['skip']} skipped"
        ),
        (
            f"- Canonical repeatability (environment and timing excluded): "
            f"**{'passed' if metrics['repeatability']['passed'] else 'failed'}**"
        ),
        "",
        "## Cache facts",
        "",
        "| Run | Before files | After files | Graph status |",
        "|---|---:|---:|---|",
        (
            f"| Cold | {cold['cache']['before']['file_count']} | "
            f"{cold['cache']['after']['file_count']} | {cold['cache']['graph_status']} |"
        ),
        (
            f"| Warm | {warm['cache']['before']['file_count']} | "
            f"{warm['cache']['after']['file_count']} | {warm['cache']['graph_status']} |"
        ),
        "",
        "## Assertion results",
        "",
        "| Assertion | Domain | Status |",
        "|---|---|---|",
        *[
            f"| `{entry['id']}` | {entry['domain']} | **{entry['status']}** |"
            for entry in warm["assertions"]["results"]
        ],
        "",
        "## Reproduction envelope",
        "",
        f"- Recorded: {metrics['environment']['recorded_at_utc']}",
        f"- OS: `{metrics['environment']['os']}`",
        f"- Python: `{metrics['environment']['python']}`",
        f"- Git: `{metrics['environment']['git']}`",
        (
            f"- Shiftory: `{metrics['environment']['shiftory']}` at "
            f"`{metrics['environment']['shiftory_commit']}`"
        ),
        f"- Clean committed source: `{metrics['implementation']['shiftory_worktree_clean']}`",
        f"- Implementation manifest: `{metrics['implementation']['manifest']['sha256']}`",
        f"- Executed package code: `{metrics['execution']['package_code']['sha256']}`",
        (
            f"- Imported package: `{metrics['execution']['distribution']}` "
            f"from `{metrics['execution']['import_root']}` "
            f"(`{metrics['execution']['module_file']}`)"
        ),
        (
            f"- Evidence Markdown renderer: "
            f"`{metrics['evidence_markdown_renderer']['module']}` "
            f"(`{metrics['evidence_markdown_renderer']['module_file']}`; "
            f"`{metrics['evidence_markdown_renderer']['module_sha256']}`)"
        ),
        f"- Benchmark runner: `{metrics['implementation']['runner_sha256']}`",
        f"- Golden inputs: `{metrics['implementation']['golden_inputs_sha256']}`",
        "",
        "## Rendered Shiftory report",
        "",
    ]
    rendered = "\n".join(
        f"{'#' + line}" if line.startswith("#") else line
        for line in shiftory_report.rstrip().splitlines()
    )
    return "\n".join(lines) + rendered + "\n"


def run_benchmark(
    scenario: dict[str, Any],
    repository: Path,
    acquisition: dict[str, Any],
    workspace: Path,
    *,
    graphora: str,
) -> tuple[dict[str, Any], str]:
    implementation = implementation_identity()
    execution = require_bound_execution(implementation)
    local_inventory = input_inventory(repository, scenario["base"], scenario["head"])
    check_expected_inventory(scenario, local_inventory)
    cache_dir = workspace / "cache" / scenario["id"]
    try:
        if cache_dir.is_symlink():
            raise OSError("cache path is a symbolic link")
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
    except OSError as exc:
        raise BenchmarkError(f"Unable to clear cold-run cache {cache_dir}: {exc}") from exc
    cold = run_complete_path(
        scenario, repository, workspace, cache_dir, "cold", graphora, execution, implementation
    )
    if cold["cache"]["before"] != {"exists": False, "files": [], "file_count": 0}:
        raise BenchmarkError("Cold benchmark run started with a non-empty cache")
    warm = run_complete_path(
        scenario, repository, workspace, cache_dir, "warm", graphora, execution, implementation
    )
    if implementation_identity() != implementation:
        raise BenchmarkError("Shiftory implementation changed while the benchmark was running")
    if require_bound_execution(implementation) != execution:
        raise BenchmarkError("Shiftory execution identity changed while the benchmark was running")
    renderer_identity = evidence_renderer_identity(execution)
    if any(run.get("evidence_markdown_renderer") != renderer_identity for run in (cold, warm)):
        raise BenchmarkError("Evidence Markdown was not rendered by the recorded implementation")
    repeatability = {
        "schema": "shiftory.benchmark-repeatability/v1",
        "excluded_envelopes": [
            "environment",
            "acquisition timing",
            "run timing",
            "local and installation paths",
        ],
        "cold_sha256": cold["canonical_output_sha256"],
        "warm_sha256": warm["canonical_output_sha256"],
        "passed": cold["canonical_output_sha256"] == warm["canonical_output_sha256"],
    }
    if not repeatability["passed"]:
        raise BenchmarkError(f"{scenario['id']} cold/warm canonical outputs differ")
    warm_evidence = json.loads(Path(warm["paths"]["evidence_json"]).read_text(encoding="utf-8"))

    def portable(run: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(run)
        value["paths"] = {
            name: str(Path(path).relative_to(workspace)) for name, path in value["paths"].items()
        }
        return value

    metrics = {
        "schema": "shiftory.benchmark-metrics/v1",
        "scenario": {key: scenario[key] for key in ("id", "name", "repository", "base", "head")},
        "acquisition": acquisition,
        "implementation": implementation,
        "execution": execution,
        "evidence_markdown_renderer": renderer_identity,
        "environment": environment_facts(implementation, execution),
        "input": {
            **local_inventory,
            "spans": warm_evidence["metrics"]["spans"],
            "changed_lines": warm_evidence["metrics"]["changed_lines"],
            "raw_hunk_patch_bytes": sum(
                hunk["raw_patch_bytes"] for file in warm_evidence["files"] for hunk in file["hunks"]
            ),
        },
        "runs": [portable(cold), portable(warm)],
        "repeatability": repeatability,
        "limitations": [
            "Coverage is accounting, not a semantic-correctness score.",
            "Golden assertions verify selected observable facts, not complete behavior.",
            "Wall times describe only the recorded machine and environment.",
        ],
    }
    run_root = workspace / "runs" / scenario["id"]
    shiftory_report = Path(warm["paths"]["report_markdown"]).read_text(encoding="utf-8")
    report = benchmark_report(metrics, shiftory_report)
    validate_public_artifacts(metrics, report, roots=(ROOT, workspace, cache_dir, Path.home()))
    atomic_write(run_root / "metrics-v1.json", pretty_json(metrics))
    atomic_write(run_root / "report.md", report)
    return metrics, report


def publish(results: dict[str, tuple[dict[str, Any], str]]) -> None:
    implementation = require_publishable_source()
    execution = require_bound_execution(implementation)
    renderer_identity = evidence_renderer_identity(execution)
    expected = set(load_scenarios())
    if set(results) != expected:
        raise BenchmarkError("Publication requires successful results for all three scenarios")
    for scenario_id, (metrics, _) in sorted(results.items()):
        validate_public_artifacts(metrics, results[scenario_id][1], roots=(ROOT, Path.home()))
        if metrics.get("implementation") != implementation:
            raise BenchmarkError(
                f"{scenario_id} result is not bound to the current clean committed implementation"
            )
        if metrics.get("execution") != execution:
            raise BenchmarkError(
                f"{scenario_id} result executed code other than the current ROOT/src implementation"
            )
        runs = metrics.get("runs")
        if not isinstance(runs, list) or len(runs) != 2:
            raise BenchmarkError(f"{scenario_id} result does not contain both benchmark runs")
        if [run.get("label") if isinstance(run, dict) else None for run in runs] != [
            "cold",
            "warm",
        ]:
            raise BenchmarkError(f"{scenario_id} result does not identify cold and warm runs")
        if runs[0].get("cache", {}).get("before") != {
            "exists": False,
            "files": [],
            "file_count": 0,
        }:
            raise BenchmarkError(f"{scenario_id} cold run did not start with an empty cache")
        for run in runs:
            if not isinstance(run, dict):
                raise BenchmarkError(f"{scenario_id} result contains an invalid benchmark run")
            cache = run.get("cache")
            if not isinstance(cache, dict) or cache.get("graph_status") != "available":
                raise BenchmarkError(
                    f"{scenario_id} publication requires Graphora enrichment in every run"
                )
            assertions = run.get("assertions")
            counts = assertions.get("counts") if isinstance(assertions, dict) else None
            if (
                not isinstance(counts, dict)
                or assertions.get("passed") is not True
                or counts.get("fail") != 0
                or counts.get("skip") != 0
            ):
                raise BenchmarkError(
                    f"{scenario_id} publication requires zero failed or skipped assertions"
                )
        if metrics.get("evidence_markdown_renderer") != renderer_identity or any(
            not isinstance(run, dict) or run.get("evidence_markdown_renderer") != renderer_identity
            for run in runs
        ):
            raise BenchmarkError(
                f"{scenario_id} evidence renderer is not bound to the recorded implementation"
            )
    for scenario_id, (metrics, report) in sorted(results.items()):
        destination = ROOT / "docs" / "benchmarks" / scenario_id
        atomic_write(destination / "metrics-v1.json", pretty_json(metrics))
        atomic_write(destination / "report.md", report)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    commands = value.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("acquire", help="clone/fetch and verify immutable inputs")
    acquire.add_argument("scenario", nargs="*", help="scenario ids; defaults to all")
    run = commands.add_parser("run", help="run one previously acquired scenario")
    run.add_argument("scenario")
    run.add_argument("--acquire", action="store_true", help="fetch before analysis")
    run.add_argument("--graphora", choices=("auto", "off", "required"), default="auto")
    suite = commands.add_parser("suite", help="acquire and run all public scenarios")
    suite.add_argument("--no-fetch", action="store_true")
    suite.add_argument("--graphora", choices=("auto", "off", "required"), default="auto")
    suite.add_argument("--publish", action="store_true", help="publish only after all pass")
    commands.add_parser("offline", help="run the committed fixture without a network")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "suite" and args.publish:
        require_publishable_source()
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    scenarios = load_scenarios()
    if args.command == "acquire":
        selected = args.scenario or sorted(scenarios)
        unknown = sorted(set(selected) - set(scenarios))
        if unknown:
            raise BenchmarkError(f"Unknown scenario ids: {', '.join(unknown)}")
        for scenario_id in selected:
            acquired = acquire_scenario(scenarios[scenario_id], workspace)
            print(f"Acquired and verified {scenario_id}: {acquired['repository']}")
        return 0
    if args.command == "run":
        if args.scenario not in scenarios:
            raise BenchmarkError(f"Unknown scenario id: {args.scenario}")
        acquired = acquire_scenario(scenarios[args.scenario], workspace, fetch=args.acquire)
        run_benchmark(
            scenarios[args.scenario],
            acquired["repository"],
            acquired["manifest"],
            workspace,
            graphora=args.graphora,
        )
        print(f"Benchmark passed: {args.scenario}")
        return 0
    if args.command == "suite":
        results = {}
        for scenario_id in sorted(scenarios):
            acquired = acquire_scenario(scenarios[scenario_id], workspace, fetch=not args.no_fetch)
            results[scenario_id] = run_benchmark(
                scenarios[scenario_id],
                acquired["repository"],
                acquired["manifest"],
                workspace,
                graphora=args.graphora,
            )
            print(f"Benchmark passed: {scenario_id}")
        if args.publish:
            publish(results)
            print("Published all three verified benchmark reports.")
        return 0
    scenario, repository, acquisition = reconstruct_offline_fixture(workspace)
    run_benchmark(scenario, repository, acquisition, workspace, graphora="off")
    print("Offline complete-path benchmark passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as error:
        print(f"benchmark error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
