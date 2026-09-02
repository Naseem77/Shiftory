"""CLI behavior of the grounding gate across the two-phase explain workflow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_cli(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "SHIFTORY_RUN_DIR": str(repository.parent / f"{repository.name}-runs"),
        "SHIFTORY_CACHE_DIR": str(repository.parent / f"{repository.name}-cache"),
    }
    return subprocess.run(
        [sys.executable, "-m", "shiftory.cli", *args],
        cwd=repository,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def ungrounded_manifest(evidence: dict[str, Any]) -> dict[str, Any]:
    line_ids = [
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    ]
    unit_ids = [
        unit["id"] for file in evidence["files"] for unit in file["units"] if unit["kind"] != "text"
    ]
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "The return value changes.",
        "items": [
            {
                "id": "change",
                "kind": "behavioral",
                "title": "Return value",
                "before": "The function returned one.",
                "after": "The function returns two.",
                "confidence": "extracted",
                "citations": [
                    citation["id"] for file in evidence["files"] for citation in file["citations"]
                ],
            }
        ],
        "coverage_owners": [
            {"evidence_id": identity, "owner_id": "change"} for identity in [*line_ids, *unit_ids]
        ],
    }


def grounded_manifest(evidence: dict[str, Any]) -> dict[str, Any]:
    manifest = ungrounded_manifest(evidence)
    for file in evidence["files"]:
        spans = {span["id"]: span for span in file["spans"]}
        for span in file["spans"]:
            replacement = spans.get(span.get("replacement_span_id") or "")
            if span["side"] != "before" or replacement is None:
                continue
            manifest["items"][0]["grounding"] = {
                "claims": [
                    {
                        "id": "return-value",
                        "type": "value_change",
                        "support_level": "verified",
                        "support": [span["id"], replacement["id"]],
                        "before_literal": "return 1",
                        "after_literal": "return 2",
                    }
                ]
            }
            return manifest
    raise AssertionError("The fixture comparison has no replacement-linked span pair")


def write_manifest(descriptor: dict[str, Any], manifest: dict[str, Any]) -> Path:
    path = Path(descriptor["explanation"])
    path.write_text(json.dumps(manifest), encoding="utf-8")
    path.chmod(0o600)
    return path


def modified(repo_factory) -> Path:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    return repository


def evidence_of(descriptor: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(descriptor["evidence"]).read_text(encoding="utf-8"))


def test_descriptor_publishes_the_grounding_contract(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)

    grounding = descriptor["grounding"]
    assert grounding["mode"] == "required"
    assert "value_change" in grounding["claim_types"]
    assert "source_order" in grounding["claim_types"]
    assert grounding["support_levels"][0] == "verified"
    assert descriptor["verify_command"][-2:] == ["--grounding", "required"]
    assert descriptor["render_command"][-2:] == ["--grounding", "required"]


def test_resume_rejects_an_ungrounded_manifest_with_a_typed_diagnostic(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    write_manifest(descriptor, ungrounded_manifest(evidence_of(descriptor)))

    failed = run_cli(repository, *descriptor["resume_command"][1:], check=False)

    assert failed.returncode == 2
    error = json.loads(failed.stderr)
    assert [entry["code"] for entry in error["details"]["errors"]] == ["grounding.missing"]


def test_resume_rejects_a_claim_bound_to_the_wrong_evidence(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    evidence = evidence_of(descriptor)
    manifest = grounded_manifest(evidence)
    manifest["items"][0]["grounding"]["claims"][0]["after_literal"] = "return 3"
    write_manifest(descriptor, manifest)

    failed = run_cli(repository, *descriptor["resume_command"][1:], check=False)

    assert failed.returncode == 2
    error = json.loads(failed.stderr)
    assert [entry["code"] for entry in error["details"]["errors"]] == [
        "grounding.operand_missing",
        "grounding.replacement_link_missing",
    ]


def test_resume_renders_the_grounding_section_and_guarantee(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    write_manifest(descriptor, grounded_manifest(evidence_of(descriptor)))

    final = run_cli(repository, *descriptor["resume_command"][1:])

    assert "## Grounded claims" in final.stdout
    assert "Grounding mode: **required**." in final.stdout
    assert "- Verified against bound evidence: 1" in final.stdout
    assert "every declared grounding claim" in final.stdout


def test_optional_mode_keeps_the_historical_v1_workflow(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(
        run_cli(repository, "explain", "--graphora", "off", "--grounding", "optional").stdout
    )
    assert descriptor["grounding"]["mode"] == "optional"
    write_manifest(descriptor, ungrounded_manifest(evidence_of(descriptor)))

    final = run_cli(repository, *descriptor["resume_command"][1:])

    assert "# Shiftory explanation" in final.stdout
    assert "## Grounded claims" not in final.stdout
    assert "it does not verify semantic correctness." in final.stdout


def test_resume_cannot_weaken_the_recorded_grounding_mode(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    write_manifest(descriptor, ungrounded_manifest(evidence_of(descriptor)))

    failed = run_cli(
        repository,
        *descriptor["resume_command"][1:],
        "--grounding",
        "optional",
        check=False,
    )

    assert failed.returncode == 2
    assert "cannot be combined with" in json.loads(failed.stderr)["message"]


def test_verify_defaults_to_optional_for_manifests_from_other_sources(repo_factory) -> None:
    repository = modified(repo_factory)
    evidence_path = repository / "evidence.json"
    run_cli(repository, "analyze", "--graphora", "off", "--output", str(evidence_path))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    explanation_path = repository / "explanation.json"
    explanation_path.write_text(json.dumps(ungrounded_manifest(evidence)), encoding="utf-8")

    verified = run_cli(
        repository,
        "verify",
        "--evidence",
        str(evidence_path),
        "--explanation",
        str(explanation_path),
    )

    payload = json.loads(verified.stdout)
    assert payload["valid"] is True
    assert "grounding" not in payload


def test_verify_reports_grounding_when_a_manifest_declares_it(repo_factory) -> None:
    repository = modified(repo_factory)
    evidence_path = repository / "evidence.json"
    run_cli(repository, "analyze", "--graphora", "off", "--output", str(evidence_path))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    explanation_path = repository / "explanation.json"
    explanation_path.write_text(json.dumps(grounded_manifest(evidence)), encoding="utf-8")

    verified = run_cli(
        repository,
        "verify",
        "--evidence",
        str(evidence_path),
        "--explanation",
        str(explanation_path),
    )

    grounding = json.loads(verified.stdout)["grounding"]
    assert grounding["mode"] == "optional"
    assert grounding["verified"] == 1
    assert grounding["items"][0]["claims"][0]["type"] == "value_change"


def test_resume_rejects_a_descriptor_without_a_grounding_block(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    write_manifest(descriptor, ungrounded_manifest(evidence_of(descriptor)))
    path = Path(descriptor["descriptor"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    del stored["grounding"]
    path.write_text(json.dumps(stored), encoding="utf-8")

    failed = run_cli(repository, *descriptor["resume_command"][1:], check=False)

    assert failed.returncode == 2
    assert "missing its grounding block" in json.loads(failed.stderr)["message"]


def test_required_workflow_rejects_review_prose_in_an_absence_literal(repo_factory) -> None:
    """Nothing forces an absence literal to be source text, so it is scanned."""
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    evidence = evidence_of(descriptor)
    manifest = grounded_manifest(evidence)
    citation = next(
        citation["id"]
        for file in evidence["files"]
        for citation in file["citations"]
        if citation["side"] == "after"
    )
    manifest["items"][0]["grounding"]["claims"].append(
        {
            "id": "smuggled",
            "type": "text_absence",
            "support_level": "verified",
            "support": [citation],
            "side": "after",
            "literal": "I recommend reverting this change.",
        }
    )
    write_manifest(descriptor, manifest)

    failed = run_cli(repository, *descriptor["resume_command"][1:], check=False)

    assert failed.returncode == 2
    error = json.loads(failed.stderr)
    assert error["details"]["errors"] == [
        {
            "path": "$.items[0].grounding.claims[1].literal",
            "message": "Disallowed recommendation; describe before-to-after behavior instead",
        }
    ]


def test_required_workflow_keeps_a_source_derived_absence_literal(repo_factory) -> None:
    repository = modified(repo_factory)
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    evidence = evidence_of(descriptor)
    manifest = grounded_manifest(evidence)
    citation = next(
        citation["id"]
        for file in evidence["files"]
        for citation in file["citations"]
        if citation["side"] == "after"
    )
    manifest["items"][0]["grounding"]["claims"].append(
        {
            "id": "no-old-value",
            "type": "text_absence",
            "support_level": "verified",
            "support": [citation],
            "side": "after",
            "literal": "return 1",
        }
    )
    write_manifest(descriptor, manifest)

    final = run_cli(repository, *descriptor["resume_command"][1:])

    assert "- Verified against bound evidence: 2" in final.stdout
    assert "'return 1' is absent from the cited after source" in final.stdout
