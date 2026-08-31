from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from shiftory import cli
from shiftory.graph import worker
from shiftory.models.core import GraphResult
from shiftory.models.json import canonical_json


def _explanation_for(evidence: dict[str, Any]) -> dict[str, Any]:
    files = evidence["files"]
    line_ids = [line["id"] for file in files for hunk in file["hunks"] for line in hunk["lines"]]
    unit_ids = [unit["id"] for file in files for unit in file["units"] if unit["kind"] != "text"]
    citation_ids = [citation["id"] for file in files for citation in file["citations"]]
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
                "citations": citation_ids,
            }
        ],
        "coverage_owners": [
            {"evidence_id": identity, "owner_id": "change"} for identity in [*line_ids, *unit_ids]
        ],
    }


def test_cli_main_exercises_real_workflow_in_process(repo_factory, monkeypatch, capsys) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    run_root = repository.parent / "runs"
    cache_root = repository.parent / "cache"
    monkeypatch.chdir(repository)
    monkeypatch.setenv("SHIFTORY_RUN_DIR", str(run_root))
    monkeypatch.setenv("SHIFTORY_CACHE_DIR", str(cache_root))

    evidence_path = repository / "evidence.json"
    assert (
        cli.main(
            [
                "analyze",
                "--graphora",
                "off",
                "--format",
                "markdown",
                "--output",
                str(repository / "evidence.md"),
            ]
        )
        == 0
    )
    assert (
        (repository / "evidence.md").read_text(encoding="utf-8").startswith("# Shiftory evidence")
    )
    assert cli.main(["analyze", "--graphora", "off", "--output", str(evidence_path)]) == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    explanation_path = repository / "explanation.json"
    explanation_path.write_text(json.dumps(_explanation_for(evidence)), encoding="utf-8")

    assert (
        cli.main(
            [
                "verify",
                "--evidence",
                str(evidence_path),
                "--explanation",
                str(explanation_path),
            ]
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified["coverage"]["line_coverage_ratio"] == 1

    report_path = repository / "report.json"
    assert (
        cli.main(
            [
                "render",
                "--format",
                "json",
                "--output",
                str(report_path),
                "--evidence",
                str(evidence_path),
                "--explanation",
                str(explanation_path),
            ]
        )
        == 0
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["schema"] == "shiftory.report/v1"

    assert cli.main(["schema", "evidence"]) == 0
    assert json.loads(capsys.readouterr().out)["$id"].endswith("/evidence-v1.json")
    assert cli.main(["cache", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["exists"] is False
    assert cli.main(["cache", "clear"]) == 0
    assert "cleared" in json.loads(capsys.readouterr().out)

    skill_directory = repository / "custom-skill"
    assert (
        cli.main(
            [
                "install-skill",
                "--target",
                "generic",
                "--directory",
                str(skill_directory),
            ]
        )
        == 0
    )
    assert (skill_directory / "SKILL.md").is_file()
    capsys.readouterr()

    with pytest.raises(SystemExit) as caught:
        cli.main(["analyze", "--graphora", "off", "--max-evidence-bytes", "-1"])
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "validation_error"

    with pytest.raises(SystemExit) as caught:
        cli.main(["explain", "--graphora", "off", "--max-evidence-tokens", "-1"])
    assert caught.value.code == 2
    assert json.loads(capsys.readouterr().err)["error"] == "validation_error"


def test_cli_main_explain_resume_in_process(repo_factory, monkeypatch, capsys) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    run_root = repository.parent / "runs"
    monkeypatch.chdir(repository)
    monkeypatch.setenv("SHIFTORY_RUN_DIR", str(run_root))

    assert cli.main(["explain", "--graphora", "off"]) == 0
    descriptor = json.loads(capsys.readouterr().out)
    evidence = json.loads(Path(descriptor["evidence"]).read_text(encoding="utf-8"))
    explanation_path = Path(descriptor["explanation"])
    explanation_path.write_text(json.dumps(_explanation_for(evidence)), encoding="utf-8")
    explanation_path.chmod(0o600)

    assert (
        cli.main(
            [
                "explain",
                "--resume",
                descriptor["descriptor"],
                "--explanation",
                str(explanation_path),
            ]
        )
        == 0
    )
    assert "# Shiftory explanation" in capsys.readouterr().out
    assert not Path(descriptor["descriptor"]).parent.exists()


def _worker_request(root: Path, operation: str = "probe") -> dict[str, Any]:
    return {
        "schema": worker.GRAPH_WORKER_REQUEST_SCHEMA,
        "operation": operation,
        "snapshot": str(root.resolve()),
        "project": "project",
        "data_dir": str((root / "graph").resolve()),
        "changed_paths": ["app.py"],
        "side": "after",
        "changed_lines": {"app.py": [1]},
        "expected_provenance_sha256": None,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda request: request.update(schema="wrong"), "invalid worker request fields"),
        (lambda request: request.update(changed_paths=["../app.py"]), "invalid or unordered"),
        (lambda request: request.update(changed_lines={"other.py": [1]}), "unordered changed-line"),
        (lambda request: request.update(changed_lines={"app.py": [True]}), "invalid changed lines"),
    ],
)
def test_worker_request_rejects_invalid_fields(
    tmp_path, monkeypatch, capsys, mutate, message: str
) -> None:
    request = _worker_request(tmp_path)
    mutate(request)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps(request)))
    with pytest.raises(SystemExit) as caught:
        worker._request()
    assert caught.value.code == 0
    assert message in json.loads(capsys.readouterr().out)["error"]["message"]


def test_worker_request_rejects_invalid_json_and_envelope(monkeypatch, capsys) -> None:
    for payload, message in [
        ("{", "invalid worker request JSON"),
        ('{"schema":"only"}', "invalid worker request envelope"),
    ]:
        monkeypatch.setattr(worker.sys, "stdin", io.StringIO(payload))
        with pytest.raises(SystemExit):
            worker._request()
        assert message in json.loads(capsys.readouterr().out)["error"]["message"]


def test_worker_request_and_installed_provider_provenance(tmp_path, monkeypatch) -> None:
    request = _worker_request(tmp_path)
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps(request)))
    assert worker._request() == request

    provenance = worker._provider_provenance()
    assert provenance["schema"] == "shiftory.graphora-provider-provenance/v1"
    assert provenance["distribution"] == "graphora-kg"
    assert provenance["distribution_version"] == "0.2.1"
    assert len(provenance["package_code_sha256"]) == 64
    assert isinstance(provenance["artifact_errors"], list)


def test_worker_direct_url_variants() -> None:
    assert worker._direct_url(SimpleNamespace(read_text=lambda name: None)) is None
    assert worker._direct_url(SimpleNamespace(read_text=lambda name: "{")) == {"invalid": True}
    assert worker._direct_url(SimpleNamespace(read_text=lambda name: "[]")) == {"invalid": True}
    assert worker._direct_url(SimpleNamespace(read_text=lambda name: '{"archive_info":{}}')) == {
        "archive_info": {}
    }


def test_worker_main_probe_enrich_and_error(tmp_path, monkeypatch, capsys) -> None:
    provenance = {"artifact_verified": True}
    probe = _worker_request(tmp_path)
    monkeypatch.setattr(worker, "_request", lambda: probe)
    monkeypatch.setattr(worker, "_provider_provenance", lambda: provenance)
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out)["provenance"] == provenance

    enrich = _worker_request(tmp_path, "enrich")
    enrich["expected_provenance_sha256"] = worker.hashlib.sha256(
        canonical_json(provenance).encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(worker, "_request", lambda: enrich)

    class Engine:
        def enrich(self, *args, **kwargs) -> GraphResult:
            return GraphResult("available", "graphora", "0.2.1")

    monkeypatch.setattr(worker, "_GraphoraWorkerEngine", Engine)
    assert worker.main() == 0
    assert json.loads(capsys.readouterr().out)["result"]["status"] == "available"

    class FailingEngine:
        def enrich(self, *args, **kwargs) -> GraphResult:
            raise RuntimeError("native failure")

    monkeypatch.setattr(worker, "_GraphoraWorkerEngine", FailingEngine)
    with pytest.raises(SystemExit) as caught:
        worker.main()
    assert caught.value.code == 0
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["exception"] == "RuntimeError"
    assert error["message"] == "RuntimeError: native failure"
