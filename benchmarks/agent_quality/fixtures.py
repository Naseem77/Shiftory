"""Offline fast-import fixture reconstruction for agent-quality benchmark cases.

Deliberately reuses ``benchmarks.runner``'s existing, hardened primitives
(``git``, ``command``, ``shiftory_process``, ``input_inventory``) instead of
duplicating them, so this layer can never drift from -- or risk destabilizing
-- the existing public benchmark's isolated-subprocess execution guarantees.
Nothing in ``benchmarks/runner.py`` is modified by this module.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any, cast

from benchmarks.agent_quality import validation as v
from benchmarks.runner import BenchmarkError, command, git, input_inventory, shiftory_process


def reconstruct_fixture(
    case_dir: Path, workspace: Path, case_id: str
) -> tuple[Path, dict[str, str]]:
    """Reconstruct one case's offline git fixture under ``workspace/<case_id>``.

    Fails closed unless the reconstructed history resolves to exactly the pinned
    commit SHAs and diff inventory recorded in the case's ``metadata.json``.
    Returns ``(repository_path, resolved_commits)``.
    """
    metadata = v.load_json_strict(case_dir / "metadata.json")
    history_path = case_dir / metadata["history"]
    v.check_file_size(history_path, v.MAX_HISTORY_BYTES, "fixture history")

    repository = v.safe_case_dir(workspace, case_id)
    shutil.rmtree(repository, ignore_errors=True)
    repository.mkdir(parents=True)
    command(["git", "init", "--quiet"], cwd=repository)
    history_bytes = history_path.read_bytes()
    command(["git", "fast-import", "--quiet"], cwd=repository, text=False, input_data=history_bytes)
    command(["git", "checkout", "--quiet", metadata["import_ref"]], cwd=repository)

    resolved = {
        side: str(git(repository, "rev-parse", "--verify", f"{sha}^{{commit}}")).strip()
        for side, sha in metadata["expected_commits"].items()
    }
    if resolved != metadata["expected_commits"]:
        raise BenchmarkError(f"{case_id} fixture commit mismatch: {resolved}")

    inventory = input_inventory(repository, resolved["base"], resolved["head"])
    expected_inventory = metadata["expected_inventory"]
    actual = {key: inventory[key] for key in expected_inventory}
    if actual != expected_inventory:
        raise BenchmarkError(
            f"{case_id} Git inventory mismatch: expected {expected_inventory}, got {actual}"
        )

    return repository, resolved


def run_analyze(repository: Path, base: str, head: str, output: Path) -> dict[str, Any]:
    """Run the real, isolated ``shiftory analyze`` and load its evidence JSON.

    Uses ``--graphora off`` for full determinism/offline behavior (no
    tree-sitter/Graphora dependency in this benchmark layer).
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    process = shiftory_process(
        [
            "analyze",
            "--repo",
            str(repository),
            "--range",
            f"{base}..{head}",
            "--graphora",
            "off",
            "--output",
            str(output),
        ]
    )
    if process.returncode != 0:
        raise BenchmarkError(f"shiftory analyze failed for {repository}: {process.stderr}")
    if not output.is_file():
        raise BenchmarkError(f"shiftory analyze did not produce evidence at {output}")
    return cast(dict[str, Any], v.load_json_strict(output, max_bytes=5_000_000))


def actual_hunk_map(evidence: dict[str, Any]) -> dict[str, str]:
    """Map ``conceptual-hunk:<path>#<n>`` placeholders to real evidence hunk ids.

    Mirrors ``benchmarks.runner.actual_hunk_map`` so case fixtures can be
    authored against stable, human-readable placeholders instead of hashed ids
    that would change if Shiftory's internal id scheme changes.
    """
    result: dict[str, str] = {}
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        for index, hunk in enumerate(file["hunks"], 1):
            result[f"conceptual-hunk:{path}#{index}"] = hunk["id"]
    return result


def actual_unit_map(evidence: dict[str, Any]) -> dict[str, str]:
    """Map ``conceptual-unit:<path>#<n>`` placeholders to real evidence unit ids."""
    result: dict[str, str] = {}
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        for index, unit in enumerate(file["units"], 1):
            result[f"conceptual-unit:{path}#{index}"] = unit["id"]
    return result


def instantiate_explanation(manifest: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Expand a hunk/unit-granularity authored explanation into the fully
    resolved, per-line-owned ``explanation.json`` that
    ``shiftory.explain.validator`` requires.

    Case authors write ``coverage_owners`` and ``citations`` against stable
    ``conceptual-hunk:<path>#<n>``/``conceptual-unit:<path>#<n>`` placeholders
    (one owner per hunk/non-text-unit) instead of Shiftory's internal per-line
    ids, which would be brittle to any change in Shiftory's id scheme. This
    mirrors ``benchmarks.runner.instantiate_manifest``'s approach without
    depending on its golden-template wrapper schema.
    """
    hunks = actual_hunk_map(evidence)
    units = actual_unit_map(evidence)
    concepts = {**hunks, **units}

    manifest = copy.deepcopy(manifest)
    owner_by_concept: dict[str, str] = {}
    for owner in manifest["coverage_owners"]:
        concept = owner["evidence_id"]
        if concept in concepts:
            if concept in owner_by_concept:
                raise BenchmarkError(f"Duplicate owner declared for {concept}")
            owner_by_concept[concept] = owner["owner_id"]

    declared_hunks = {key for key in owner_by_concept if key.startswith("conceptual-hunk:")}
    if declared_hunks != set(hunks):
        missing = sorted(set(hunks) - declared_hunks)
        extra = sorted(declared_hunks - set(hunks))
        raise BenchmarkError(f"Hunk ownership mismatch; missing={missing}, extra={extra}")

    for item in manifest["items"]:
        resolved_citations: list[Any] = []
        for citation in item.get("citations", []):
            identity = citation["id"] if isinstance(citation, dict) else citation
            replacement = concepts.get(identity, identity)
            if isinstance(citation, dict):
                resolved_citations.append({**citation, "id": replacement})
            else:
                resolved_citations.append(replacement)
        item["citations"] = resolved_citations

    owners: list[dict[str, str]] = []
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        hunk_owner = {
            hunk["id"]: owner_by_concept[f"conceptual-hunk:{path}#{index}"]
            for index, hunk in enumerate(file["hunks"], 1)
        }
        for hunk in file["hunks"]:
            owner_id = hunk_owner[hunk["id"]]
            for line in hunk["lines"]:
                owners.append({"evidence_id": line["id"], "owner_id": owner_id})
        for index, unit in enumerate(file["units"], 1):
            if unit["kind"] == "text":
                continue
            concept = f"conceptual-unit:{path}#{index}"
            unit_owner_id: str | None = owner_by_concept.get(concept)
            if unit_owner_id is None:
                file_owners = set(hunk_owner.values())
                if len(file_owners) != 1:
                    raise BenchmarkError(f"Non-text unit lacks an unambiguous owner: {concept}")
                unit_owner_id = next(iter(file_owners))
            owners.append({"evidence_id": unit["id"], "owner_id": unit_owner_id})

    manifest["coverage_owners"] = sorted(
        owners, key=lambda value: (value["evidence_id"], value["owner_id"])
    )
    return manifest


def canonical_explanation_bytes(explanation: dict[str, Any]) -> bytes:
    """The one canonical byte form used to hash a resolved (non-template)
    candidate explanation for identity comparisons throughout this benchmark
    layer: sorted keys, 2-space indent, trailing newline, UTF-8."""
    return (json.dumps(explanation, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def explanation_sha256(explanation: dict[str, Any]) -> str:
    return v.sha256_bytes(canonical_explanation_bytes(explanation))
