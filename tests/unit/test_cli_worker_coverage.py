from __future__ import annotations

import io
import json
import os
import subprocess
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


def _write_chunk_explanations(descriptor: dict[str, Any]) -> None:
    for entry in descriptor["chunks"]:
        chunk = json.loads(Path(entry["payload"]).read_text(encoding="utf-8"))
        owner_id = f"chunk-{chunk['index']}-change"
        targets = [
            target["evidence_id"]
            for work_item in chunk["work_items"]
            for target in work_item["ownership_targets"]
        ]
        citations = [
            context["citation_id"]
            for work_item in chunk["work_items"]
            for context in work_item["contexts"]
        ]
        explanation = {
            "schema": "shiftory.chunk-explanation/v1",
            "chunk_id": chunk["id"],
            "comparison_identity": chunk["comparison_identity"],
            "ledger_sha256": chunk["ledger_sha256"],
            "summary": f"Chunk {chunk['index']} changes source assignments.",
            "items": [
                {
                    "id": owner_id,
                    "kind": "behavioral",
                    "title": f"Change assignments in chunk {chunk['index']}",
                    "before": "The assignments used their previous values.",
                    "after": "The assignments use their new values.",
                    "confidence": "extracted",
                    "citations": citations,
                }
            ],
            "coverage_owners": [
                {"evidence_id": target, "owner_id": owner_id} for target in targets
            ],
        }
        path = Path(entry["explanation"])
        path.write_text(json.dumps(explanation), encoding="utf-8")
        path.chmod(0o600)


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


def test_chunked_finalization_uses_authorized_composed_snapshot(
    repo_factory, monkeypatch, capsys
) -> None:
    repository = repo_factory()
    source = repository / "large.py"
    source.write_text(
        "\n".join(f"old_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add large source"], cwd=repository, check=True)
    source.write_text(
        "\n".join(f"new_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    run_root = repository.parent / "runs"
    monkeypatch.chdir(repository)
    monkeypatch.setenv("SHIFTORY_RUN_DIR", str(run_root))

    assert (
        cli.main(
            [
                "explain",
                "--graphora",
                "off",
                "--max-evidence-bytes",
                "3000",
            ]
        )
        == 0
    )
    descriptor = json.loads(capsys.readouterr().out)
    _write_chunk_explanations(descriptor)
    ledger = json.loads(Path(descriptor["ledger"]).read_text(encoding="utf-8"))
    replacement = _explanation_for(ledger)
    replacement["summary"] = "UNAUTHORIZED REPLACEMENT"
    replacement["items"][0]["title"] = "UNAUTHORIZED REPLACEMENT"
    real_write = cli._write_private
    swapped = False

    def swap_after_final_write(path, payload, **kwargs):
        nonlocal swapped
        real_write(path, payload, **kwargs)
        if path == Path(descriptor["final_explanation"]):
            temporary = path.with_name("attacker-explanation.json")
            temporary.write_text(canonical_json(replacement), encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, path)
            swapped = True

    monkeypatch.setattr(cli, "_write_private", swap_after_final_write)
    assert (
        cli.main(
            [
                *descriptor["resume_command"][1:],
                "--keep-artifacts",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert swapped
    assert "UNAUTHORIZED REPLACEMENT" not in output
    assert "UNAUTHORIZED REPLACEMENT" not in Path(descriptor["report"]).read_text(encoding="utf-8")
    assert (
        json.loads(Path(descriptor["final_explanation"]).read_text(encoding="utf-8"))["summary"]
        == "UNAUTHORIZED REPLACEMENT"
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory descriptor semantics")
def test_private_write_pins_swapped_run_directory(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    hidden = tmp_path / "hidden-run"
    run.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    sentinel = outside / "report.md"
    sentinel.write_text("outside sentinel", encoding="utf-8")
    sentinel.chmod(0o600)
    directory = cli._open_private_directory(run, "Run directory")
    real_verify = cli._PrivateDirectory.verify
    swapped = False

    def swap_after_verify(self):
        nonlocal swapped
        real_verify(self)
        if self is directory and not swapped:
            run.rename(hidden)
            run.symlink_to(outside, target_is_directory=True)
            swapped = True

    monkeypatch.setattr(cli._PrivateDirectory, "verify", swap_after_verify)
    try:
        with pytest.raises(cli.ValidationError, match=r"path was replaced|symbolic link"):
            cli._write_private(run / "report.md", "generated", directory=directory)
        assert sentinel.read_text(encoding="utf-8") == "outside sentinel"
        assert not (hidden / "report.md").exists()
    finally:
        directory.close()
        run.unlink(missing_ok=True)
        hidden.rename(run)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory descriptor semantics")
def test_private_read_pins_swapped_run_directory(tmp_path, monkeypatch) -> None:
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    hidden = tmp_path / "hidden-run"
    run.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    inside_artifact = run / "ledger.json"
    outside_artifact = outside / "ledger.json"
    inside_artifact.write_text("inside", encoding="utf-8")
    outside_artifact.write_text("outside", encoding="utf-8")
    inside_artifact.chmod(0o600)
    outside_artifact.chmod(0o600)
    directory = cli._open_private_directory(run, "Run directory")
    real_verify = cli._PrivateDirectory.verify
    swapped = False

    def swap_after_verify(self):
        nonlocal swapped
        real_verify(self)
        if self is directory and not swapped:
            run.rename(hidden)
            run.symlink_to(outside, target_is_directory=True)
            swapped = True

    monkeypatch.setattr(cli._PrivateDirectory, "verify", swap_after_verify)
    try:
        with pytest.raises(cli.ValidationError, match=r"path was replaced|symbolic link"):
            cli._read_private_bytes(
                run / "ledger.json",
                "Ledger",
                directory=directory,
            )
        assert outside_artifact.read_text(encoding="utf-8") == "outside"
        assert inside_artifact.read_text(encoding="utf-8") == "outside"
        assert (hidden / "ledger.json").read_text(encoding="utf-8") == "inside"
    finally:
        directory.close()
        run.unlink(missing_ok=True)
        hidden.rename(run)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory descriptor semantics")
def test_private_write_and_cleanup_pin_swapped_chunks_and_run_directories(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "runs"
    run_path = root / ("a" * 32)
    chunks_path = run_path / "chunks"
    outside = tmp_path / "outside"
    hidden_chunks = run_path / "hidden-chunks"
    hidden_run = root / "hidden-run"
    root.mkdir(mode=0o700)
    run_path.mkdir(mode=0o700)
    chunks_path.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    sentinel = outside / "chunk-0001.json"
    sentinel.write_text("outside sentinel", encoding="utf-8")
    sentinel.chmod(0o600)
    run = cli._open_private_directory(run_path, "Run directory")
    chunks = cli._open_private_directory(chunks_path, "Chunk directory", parent=run)
    real_verify = cli._PrivateDirectory.verify
    swapped_chunks = False

    def swap_chunks_after_verify(self):
        nonlocal swapped_chunks
        real_verify(self)
        if self is chunks and not swapped_chunks:
            chunks_path.rename(hidden_chunks)
            chunks_path.symlink_to(outside, target_is_directory=True)
            swapped_chunks = True

    monkeypatch.setattr(cli._PrivateDirectory, "verify", swap_chunks_after_verify)
    try:
        with pytest.raises(cli.ValidationError, match=r"path was replaced|symbolic link"):
            cli._write_private(
                chunks_path / "chunk-0001.json",
                "generated",
                directory=chunks,
            )
        assert sentinel.read_text(encoding="utf-8") == "outside sentinel"
        assert not (hidden_chunks / "chunk-0001.json").exists()
    finally:
        chunks.close()
        chunks_path.unlink(missing_ok=True)
        hidden_chunks.rename(chunks_path)

    swapped_run = False

    def swap_run_after_verify(self):
        nonlocal swapped_run
        real_verify(self)
        if self is run and not swapped_run:
            run_path.rename(hidden_run)
            run_path.symlink_to(outside, target_is_directory=True)
            swapped_run = True

    monkeypatch.setattr(cli._PrivateDirectory, "verify", swap_run_after_verify)
    try:
        with pytest.raises(cli.ValidationError, match=r"path was replaced|symbolic link"):
            cli._remove_private_run(run)
        assert sentinel.read_text(encoding="utf-8") == "outside sentinel"
        assert hidden_run.is_dir()
    finally:
        run.close()
        run_path.unlink(missing_ok=True)
        hidden_run.rename(run_path)


def test_private_run_io_fails_closed_without_posix_directory_fds(monkeypatch) -> None:
    monkeypatch.setattr(cli.os, "name", "nt")

    with pytest.raises(cli.ValidationError, match="POSIX no-follow directory descriptor"):
        cli._require_private_directory_fd_support()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory descriptor semantics")
def test_unsafe_private_directory_paths_fail_as_validation_errors(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    regular.chmod(0o600)

    # A no-follow open of a symlinked or non-directory run path must fail closed as a
    # validation error rather than leaking a raw OSError out as an internal error.
    for candidate in (linked, regular, tmp_path / "missing"):
        with pytest.raises(cli.ValidationError, match="could not be opened safely"):
            cli._open_private_directory(candidate, "Run directory")

    parent = cli._open_private_directory(tmp_path, "Run root")
    try:
        with pytest.raises(cli.ValidationError, match="could not be opened safely"):
            cli._open_private_directory(linked, "Run directory", parent=parent)
    finally:
        parent.close()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX directory descriptor semantics")
def test_private_directory_creation_closes_descriptors_when_the_parent_is_swapped(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("SHIFTORY_RUN_DIR", str(root))
    real_verify = cli._PrivateDirectory.verify
    real_open = cli._open_private_directory
    opened: list[cli._PrivateDirectory] = []
    child_labels = {"Run directory", "Chunk directory"}
    created = {"child": False}

    def recording_open(path, label, *, parent=None):
        directory = real_open(path, label, parent=parent)
        opened.append(directory)
        if label in child_labels:
            created["child"] = True
        return directory

    def swapped_verify(self):
        real_verify(self)
        # Fail only the parent re-verification that happens *after* the child opened.
        if created["child"] and self.label in {"Run root", "Run directory"}:
            raise cli.ValidationError(f"{self.label} path was replaced: {self.path}")

    monkeypatch.setattr(cli, "_open_private_directory", recording_open)
    monkeypatch.setattr(cli._PrivateDirectory, "verify", swapped_verify)

    for _ in range(5):
        created["child"] = False
        with pytest.raises(cli.ValidationError, match=r"path was replaced"):
            cli._new_run(None)

    monkeypatch.setattr(cli._PrivateDirectory, "verify", real_verify)
    run = cli._new_run(None)
    try:
        monkeypatch.setattr(cli._PrivateDirectory, "verify", swapped_verify)
        for index in range(5):
            created["child"] = False
            with pytest.raises(cli.ValidationError, match=r"path was replaced"):
                cli._private_subdirectory(run, f"chunks{index}", "Chunk directory")
    finally:
        monkeypatch.setattr(cli._PrivateDirectory, "verify", real_verify)
        run.close()

    # Every descriptor opened during the failed creations must already be closed.
    leaked = [directory for directory in opened if directory.descriptor >= 0]
    assert not leaked, f"{len(leaked)} directory descriptor(s) leaked on the failure path"


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
