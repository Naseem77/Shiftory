"""Independently authored adversarial grounding manifests over real CLI evidence.

The manifests in ``tests/fixtures/grounding`` are written by hand against the
published contract. Only the evidence identifiers are substituted here, by a
resolver that reads the evidence packet directly and shares no code with the
validator or the grounding engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "grounding"

SCENARIOS: dict[str, dict[str, dict[str, str]]] = {
    "reorder": {
        "base": {
            "service.py": (
                "def save(record):\n"
                "    database.commit()\n"
                "    cache.invalidate(record)\n"
                '    return "ok"\n'
            )
        },
        "head": {
            "service.py": (
                "def save(record):\n"
                "    cache.invalidate(record)\n"
                "    database.commit()\n"
                '    return "done"\n'
            )
        },
    },
    "split": {
        "base": {"app.py": "TIMEOUT = 30\n", "util.py": "RETRIES = 1\n"},
        "head": {"app.py": "TIMEOUT = 60\n", "util.py": "RETRIES = 3\n"},
    },
    "addition": {
        "base": {"base.py": 'VERSION = "1"\n'},
        "head": {"base.py": 'VERSION = "1"\n', "feature.py": 'def feature():\n    return "new"\n'},
    },
}


def build_repository(root: Path, scenario: str) -> Path:
    repository = root / scenario
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Shiftory Test"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "shiftory@example.invalid"], cwd=repository, check=True
    )
    for name, content in SCENARIOS[scenario]["base"].items():
        (repository / name).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    for name in SCENARIOS[scenario]["base"]:
        if name not in SCENARIOS[scenario]["head"]:
            (repository / name).unlink()
    for name, content in SCENARIOS[scenario]["head"].items():
        (repository / name).write_text(content, encoding="utf-8")
    return repository


def analyze(repository: Path, destination: Path) -> dict[str, Any]:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "shiftory.cli",
            "analyze",
            "--graphora",
            "off",
            "--no-cache",
            "--output",
            str(destination),
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(destination.read_text(encoding="utf-8"))


def file_for(evidence: dict[str, Any], path: str) -> dict[str, Any]:
    for file in evidence["files"]:
        if path in {file["new_path"], file["old_path"]}:
            return file
    raise AssertionError(f"The comparison does not contain {path}")


def lookup(reference: str, evidence: dict[str, Any]) -> str:
    """Resolve one `{kind:path:...}` placeholder straight from the evidence."""
    kind, path, *rest = reference.split(":")
    file = file_for(evidence, path)
    if kind == "unit":
        for unit in file["units"]:
            if unit["kind"] == rest[0]:
                return str(unit["id"])
        raise AssertionError(f"No {rest[0]} unit for {path}")
    side, number = rest[0], int(rest[1])
    if kind == "line":
        coordinate = "old_line" if side == "before" else "new_line"
        for hunk in file["hunks"]:
            for line in hunk["lines"]:
                if line["side"] == side and line[coordinate] == number:
                    return str(line["id"])
    if kind == "span":
        for span in file["spans"]:
            if span["side"] == side and span["start_line"] == number:
                return str(span["id"])
    if kind == "citation":
        for citation in file["citations"]:
            if citation["side"] == side and citation["start_line"] == number:
                return str(citation["id"])
    raise AssertionError(f"Unresolved evidence placeholder {reference}")


def substitute(value: Any, evidence: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        return lookup(value[1:-1], evidence)
    if isinstance(value, dict):
        return {key: substitute(item, evidence) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, evidence) for item in value]
    return value


def run_verify(repository: Path, evidence_path: Path, explanation_path: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "shiftory.cli",
            "verify",
            "--evidence",
            str(evidence_path),
            "--explanation",
            str(explanation_path),
            "--grounding",
            "required",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ},
    )
    return completed.returncode, completed.stdout or completed.stderr


def fixtures() -> list[str]:
    return sorted(path.name for path in FIXTURES.glob("*.json"))


@pytest.mark.parametrize("name", fixtures())
def test_authored_manifest_behaves_exactly_as_declared(name: str, tmp_path: Path) -> None:
    fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert fixture["fixture_schema"] == "shiftory.grounding-adversarial/v1"
    repository = build_repository(tmp_path, fixture["scenario"])
    evidence_path = tmp_path / f"{name}-evidence.json"
    evidence = analyze(repository, evidence_path)

    explanation_path = tmp_path / f"{name}-explanation.json"
    explanation_path.write_text(
        json.dumps(substitute(fixture["manifest"], evidence), indent=2), encoding="utf-8"
    )

    code, output = run_verify(repository, evidence_path, explanation_path)
    payload = json.loads(output)
    if fixture["expect"] == "accept":
        assert code == 0, output
        assert payload["valid"] is True
        assert payload["grounding"]["claim_total"] >= 1
        return
    assert code != 0, output
    reported = [entry.get("code") for entry in payload["details"]["errors"]]
    assert reported == fixture["expected_codes"], output


def test_every_scenario_is_exercised_by_a_fixture() -> None:
    scenarios = {
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))["scenario"] for name in fixtures()
    }
    assert scenarios == set(SCENARIOS)


def test_accepted_and_rejected_fixtures_both_exist() -> None:
    expectations = [
        json.loads((FIXTURES / name).read_text(encoding="utf-8"))["expect"] for name in fixtures()
    ]
    assert expectations.count("accept") >= 3
    assert expectations.count("reject") >= 4
