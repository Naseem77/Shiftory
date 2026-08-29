from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(repository, *args, check=True, extra_env=None):
    run_root = repository.parent / f"{repository.name}-runs"
    cache_root = repository.parent / f"{repository.name}-cache"
    environment = {
        **os.environ,
        "SHIFTORY_RUN_DIR": str(run_root),
        "SHIFTORY_CACHE_DIR": str(cache_root),
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "shiftory.cli", *args],
        cwd=repository,
        env=environment,
        check=check,
        capture_output=True,
        text=True,
    )


def write_explanation(descriptor: dict, summary: str = "The return value changes.") -> Path:
    evidence = json.loads(Path(descriptor["evidence"]).read_text(encoding="utf-8"))
    line_ids = [
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    ]
    unit_ids = [
        unit["id"] for file in evidence["files"] for unit in file["units"] if unit["kind"] != "text"
    ]
    citations = [citation["id"] for file in evidence["files"] for citation in file["citations"]]
    explanation = {
        "schema": "shiftory.explanation/v1",
        "summary": summary,
        "items": [
            {
                "id": "change",
                "kind": "behavioral",
                "title": "Return value",
                "before": "The function returned the old value.",
                "after": "The function returns the new value.",
                "confidence": "extracted",
                "citations": citations,
            }
        ],
        "coverage_owners": [
            {"evidence_id": identity, "owner_id": "change"} for identity in [*line_ids, *unit_ids]
        ],
    }
    path = Path(descriptor["explanation"])
    path.write_text(json.dumps(explanation), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_analyze_verify_render_and_repeated_citation(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    evidence_path = repository / "evidence.json"
    run_cli(
        repository,
        "analyze",
        "--graphora",
        "off",
        "--output",
        str(evidence_path),
    )
    evidence = json.loads(evidence_path.read_text())
    line_ids = [
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    ]
    span_ids = [span["id"] for file in evidence["files"] for span in file["spans"]]
    citation = evidence["files"][0]["citations"][0]["id"]
    explanation = {
        "schema": "shiftory.explanation/v1",
        "summary": "The return value changes.",
        "items": [
            {
                "id": "return",
                "kind": "behavioral",
                "title": "Return value",
                "before": "The function returned one.",
                "after": "The function returns two.",
                "confidence": "extracted",
                "citations": [citation, citation],
            }
        ],
        "coverage_owners": [
            {"evidence_id": identity, "owner_id": "return"} for identity in [*line_ids, *span_ids]
        ],
    }
    explanation_path = repository / "explanation.json"
    explanation_path.write_text(json.dumps(explanation), encoding="utf-8")
    verified = run_cli(
        repository,
        "verify",
        "--evidence",
        str(evidence_path),
        "--explanation",
        str(explanation_path),
    )
    assert json.loads(verified.stdout)["coverage"]["line_coverage_ratio"] == 1
    rendered = run_cli(
        repository,
        "render",
        "--evidence",
        str(evidence_path),
        "--explanation",
        str(explanation_path),
    )
    assert "Behavioral before to after" in rendered.stdout
    assert "Changed lines: 2/2 (100%)" in rendered.stdout


def test_default_explain_creates_private_recoverable_descriptor(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    run_root = repository.parent / f"{repository.name}-runs"
    result = run_cli(
        repository,
        "explain",
        "--graphora",
        "off",
        check=True,
    )
    descriptor = json.loads(result.stdout)
    assert descriptor["status"] == "awaiting_explanation"
    assert os.stat(descriptor["descriptor"]).st_mode & 0o077 == 0
    assert os.stat(Path(descriptor["descriptor"]).parent).st_mode & 0o077 == 0
    assert str(run_root) in descriptor["descriptor"]
    assert descriptor["resume_command"][:2] == ["shiftory", "explain"]
    assert descriptor["evidence_budget"]["actual_bytes"] <= 1_000_000


def test_explain_resume_verifies_renders_and_cleans_up(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    phase_one = run_cli(repository, "explain", "--graphora", "off")
    descriptor = json.loads(phase_one.stdout)
    write_explanation(descriptor)
    run = Path(descriptor["descriptor"]).parent

    final = run_cli(repository, *descriptor["resume_command"][1:])

    assert "# Shiftory explanation" in final.stdout
    assert "Changed lines: 2/2 (100%)" in final.stdout
    assert not run.exists()


def test_explain_retention_flag_and_environment_keep_complete_artifacts(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    for extra_args, extra_env in [
        (("--keep-artifacts",), {}),
        ((), {"SHIFTORY_KEEP_ARTIFACTS": "1"}),
    ]:
        descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
        write_explanation(descriptor)
        run = Path(descriptor["descriptor"]).parent
        final = run_cli(
            repository,
            *descriptor["resume_command"][1:],
            *extra_args,
            extra_env=extra_env,
        )

        assert run.is_dir()
        assert (run / "verification.json").is_file()
        assert (run / "report.md").read_text(encoding="utf-8") == final.stdout
        assert str(run) in final.stderr


def test_explain_failures_retain_diagnostic_with_exact_path(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)

    failed = run_cli(
        repository,
        *descriptor["resume_command"][1:],
        check=False,
    )

    error = json.loads(failed.stderr)
    diagnostic = Path(error["details"]["diagnostic"])
    assert failed.returncode == 2
    assert diagnostic == Path(descriptor["descriptor"]).parent / "diagnostic.json"
    assert diagnostic.is_file()
    assert json.loads(diagnostic.read_text(encoding="utf-8"))["details"]["diagnostic"] == str(
        diagnostic
    )


def test_resume_rejects_escape_and_tampered_run_paths(repo_factory) -> None:
    repository = repo_factory()
    outside = repository / "run.json"
    outside.write_text('{"schema":"shiftory.run/v1"}', encoding="utf-8")
    outside.chmod(0o600)

    escaped = run_cli(
        repository,
        "explain",
        "--resume",
        str(outside),
        "--explanation",
        str(repository / "explanation.json"),
        check=False,
    )
    escaped_error = json.loads(escaped.stderr)
    assert escaped.returncode == 2
    assert "outside the Shiftory run root" in escaped_error["message"]
    assert Path(escaped_error["details"]["diagnostic"]).is_file()

    descriptor = json.loads(run_cli(repository, "explain", "--graphora", "off").stdout)
    descriptor_path = Path(descriptor["descriptor"])
    stored = json.loads(descriptor_path.read_text(encoding="utf-8"))
    stored["evidence"] = str(outside)
    descriptor_path.write_text(json.dumps(stored), encoding="utf-8")
    descriptor_path.chmod(0o600)
    tampered = run_cli(
        repository,
        "explain",
        "--resume",
        str(descriptor_path),
        "--explanation",
        descriptor["explanation"],
        check=False,
    )
    tampered_error = json.loads(tampered.stderr)
    assert tampered.returncode == 2
    assert "field 'evidence' must target" in tampered_error["message"]
    assert Path(tampered_error["details"]["diagnostic"]).parent == descriptor_path.parent


def test_cli_scope_options_and_invalid_combinations(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
    (repository / "new.py").write_text("new = True\n", encoding="utf-8")

    staged = json.loads(run_cli(repository, "analyze", "--staged", "--graphora", "off").stdout)
    unstaged = json.loads(run_cli(repository, "analyze", "--unstaged", "--graphora", "off").stdout)
    working = json.loads(run_cli(repository, "analyze", "--graphora", "off").stdout)
    assert staged["comparison"]["mode"] == "staged"
    assert unstaged["comparison"]["mode"] == "unstaged"
    assert {file["new_path"] for file in working["files"]} == {"app.py", "new.py"}

    bad_remote = run_cli(
        repository,
        "analyze",
        "--remote",
        "upstream",
        "--graphora",
        "off",
        check=False,
    )
    bad_parent = run_cli(
        repository,
        "analyze",
        "--parent",
        "1",
        "--graphora",
        "off",
        check=False,
    )
    bad_pr = run_cli(
        repository,
        "analyze",
        "--pr",
        "0",
        "--graphora",
        "off",
        check=False,
    )
    assert json.loads(bad_remote.stderr)["error"] == "invalid_scope"
    assert json.loads(bad_parent.stderr)["error"] == "invalid_scope"
    assert json.loads(bad_pr.stderr)["error"] == "invalid_scope"


def test_commit_range_branch_and_scope_conflict_reach_cli(repo_factory) -> None:
    repository = repo_factory()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

    cases = [
        (("--commit", head), "commit"),
        (("--range", f"{base}..{head}"), "range-.."),
        (("--branch", base), "branch"),
    ]
    for scope_args, expected_mode in cases:
        result = run_cli(repository, "analyze", *scope_args, "--graphora", "off")
        assert json.loads(result.stdout)["comparison"]["mode"] == expected_mode

    conflict = run_cli(
        repository,
        "analyze",
        "--staged",
        "--unstaged",
        "--graphora",
        "off",
        check=False,
    )
    assert conflict.returncode == 2
    assert "not allowed with argument" in conflict.stderr


def test_pr_scope_uses_explicit_gh_path_only(repo_factory) -> None:
    repository = repo_factory()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
        cwd=repository,
        check=True,
    )
    binary_directory = repository.parent / f"{repository.name}-bin"
    binary_directory.mkdir()
    fake_gh = binary_directory / "gh"
    fake_gh.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' \'{{"baseRefOid":"{base}","headRefOid":"{head}"}}\'\n',
        encoding="utf-8",
    )
    fake_gh.chmod(0o700)

    result = run_cli(
        repository,
        "analyze",
        "--pr",
        "1",
        "--remote",
        "origin",
        "--graphora",
        "off",
        extra_env={"PATH": f"{binary_directory}{os.pathsep}{os.environ['PATH']}"},
    )

    evidence = json.loads(result.stdout)
    assert evidence["comparison"]["mode"] == "pr"
    assert evidence["comparison"]["base_sha"] == base
    assert evidence["comparison"]["head_sha"] == head


def test_schema_cache_and_skill_install_commands(repo_factory) -> None:
    repository = repo_factory()
    schema = json.loads(run_cli(repository, "schema", "explanation").stdout)
    assert schema["$id"].endswith("/explanation-v1.json")

    status = json.loads(run_cli(repository, "cache", "status").stdout)
    cleared = json.loads(run_cli(repository, "cache", "clear").stdout)
    assert Path(status["path"]).name == Path(cleared["cleared"]).name

    installed = json.loads(run_cli(repository, "install-skill", "--target", "copilot").stdout)
    target = Path(installed["installed"])
    bundled = Path(__file__).parents[2] / "skills" / "shiftory" / "SKILL.md"
    assert target.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")
    repeated = json.loads(run_cli(repository, "install-skill", "--target", "copilot").stdout)
    assert repeated == installed

    for target_name, relative in [
        ("claude", ".claude/skills/shiftory/SKILL.md"),
        ("generic", "skills/shiftory/SKILL.md"),
    ]:
        result = json.loads(run_cli(repository, "install-skill", "--target", target_name).stdout)
        assert Path(result["installed"]) == (repository / relative).resolve()
        assert Path(result["installed"]).read_text(encoding="utf-8") == bundled.read_text(
            encoding="utf-8"
        )

    custom = repository / "custom-skill"
    result = json.loads(
        run_cli(
            repository,
            "install-skill",
            "--target",
            "generic",
            "--directory",
            str(custom),
        ).stdout
    )
    assert Path(result["installed"]) == custom / "SKILL.md"
