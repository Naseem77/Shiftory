"""Tests for the opt-in agent capture harness: prompt-package isolation
(trust boundary), the strict raw-response protocol, and bounded/streamed
subprocess execution with timeout and output-cap enforcement."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from benchmarks.agent_quality import agent_harness as ah
from benchmarks.agent_quality import validation as v

CASES_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
AUDITOR_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "auditor"
CASE_ID = "reordering-guard-clause"


def test_prepare_never_copies_auditor_content(tmp_path: Path) -> None:
    rubric = v.load_json_strict(AUDITOR_DIR / CASE_ID / "rubric.json")
    leak_strings = [fact["description"] for fact in rubric["required_facts"]]

    out_dir = tmp_path / "prompt"
    ah.prepare_prompt_package(CASES_DIR / CASE_ID, out_dir, CASE_ID)

    all_paths = [path for path in out_dir.rglob("*") if path.is_file() and ".git" not in path.parts]
    assert all("auditor" not in path.parts for path in all_paths)
    assert not any(path.name == "rubric.json" for path in all_paths)

    combined_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in all_paths
    )
    for leaked in leak_strings:
        assert leaked not in combined_text, (
            f"rubric fact description leaked into prompt package: {leaked!r}"
        )


def test_prepare_manifest_matches_disk(tmp_path: Path) -> None:
    out_dir = tmp_path / "prompt"
    result = ah.prepare_prompt_package(CASES_DIR / CASE_ID, out_dir, CASE_ID)
    for entry in result["prompt_package_manifest"]:
        path = out_dir / entry["path"]
        assert path.is_file()
        assert v.sha256_file(path) == entry["sha256"]


def _explanation_document() -> dict:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "s",
        "items": [],
        "coverage_owners": [],
    }


def _base_capture_kwargs(prompt_dir: Path, out_dir: Path) -> dict:
    return dict(
        prompt_dir=prompt_dir,
        out_dir=out_dir,
        case_id=CASE_ID,
        candidate_id="captured-a",
        provider="test-provider",
        model_name="test-model",
        model_version="1.0",
        agent_tool_version="1.0.0",
        evaluation_protocol="manual test invocation",
        generator_access_profile={
            "repository_access": True,
            "filesystem_scope": "prompt package directory only (by instruction, not sandbox)",
            "network_access": False,
            "tool_access": ["bash", "view", "edit"],
        },
        prompt_package_manifest=[],
        prompt_package_digest="a" * 64,
        evidence_sha256="b" * 64,
        shiftory_commit="c" * 40,
    )


def test_capture_result_writes_explanation_for_valid_raw_response(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    document = _explanation_document()
    raw_bytes = json.dumps(document).encode("utf-8")
    (prompt_dir / "RAW_RESPONSE").write_bytes(raw_bytes)

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    assert result["protocol_violation"] is None
    assert (out_dir / "explanation.json").is_file()
    assert (out_dir / "raw-response.txt").read_bytes() == raw_bytes
    assert v.sha256_file(out_dir / "explanation.json") == v.sha256_file(
        out_dir / "raw-response.txt"
    )
    agent_run = json.loads((out_dir / "agent-run.json").read_text())
    v.validate_against_schema(agent_run, "agent-run-v1")
    assert agent_run["isolation_method"] == "protocol"
    assert agent_run["raw_response_sha256"] == v.sha256_bytes(raw_bytes)


def test_capture_result_marks_invalid_candidate_for_non_json_response(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    (prompt_dir / "RAW_RESPONSE").write_text("Sure, here's my answer: it looks fine to me!")

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    assert result["protocol_violation"] is not None
    assert "invalid_candidate" in result
    assert not (out_dir / "explanation.json").exists()
    assert (out_dir / "raw-response.txt").is_file()


def test_capture_result_rejects_markdown_fenced_response_without_repair(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    fenced = "```json\n" + json.dumps(_explanation_document()) + "\n```"
    (prompt_dir / "RAW_RESPONSE").write_text(fenced)

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    assert result["protocol_violation"] is not None
    assert not (out_dir / "explanation.json").exists()


def test_capture_result_rejects_duplicate_keys(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    (prompt_dir / "RAW_RESPONSE").write_text('{"schema": "a", "schema": "b"}')

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    assert result["protocol_violation"] is not None
    assert not (out_dir / "explanation.json").exists()


def test_capture_result_rejects_non_explanation_shaped_json(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    (prompt_dir / "RAW_RESPONSE").write_text(json.dumps({"unrelated": True}))

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    assert result["protocol_violation"] is not None
    assert not (out_dir / "explanation.json").exists()


def test_capture_result_records_missing_response_as_invalid(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    # No RAW_RESPONSE written and no command executed: raw is empty bytes.
    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))
    assert result["protocol_violation"] is not None


def test_capture_result_caps_an_oversized_raw_response_file_without_full_read(
    tmp_path: Path, monkeypatch
) -> None:
    """A RAW_RESPONSE file larger than max_output_bytes must never be fully
    read into memory before the cap is enforced -- this is the untrusted
    input a real capture actually produces, unlike the harness's own
    subprocess stdout/stderr streams."""
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"

    read_sizes: list[int] = []
    real_open = Path.open

    def _tracking_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        handle = real_open(self, *args, **kwargs)
        if self.name == "RAW_RESPONSE":
            original_read = handle.read

            def _tracked_read(size=-1):  # type: ignore[no-untyped-def]
                read_sizes.append(size)
                return original_read(size)

            handle.read = _tracked_read
        return handle

    monkeypatch.setattr(Path, "open", _tracking_open)

    oversized = b"x" * (v.MAX_RAW_RESPONSE_BYTES * 4)
    (prompt_dir / "RAW_RESPONSE").write_bytes(oversized)

    kwargs = _base_capture_kwargs(prompt_dir, out_dir)
    kwargs["max_output_bytes"] = 100
    result = ah.capture_result(**kwargs)

    assert result["agent_run"]["truncated"] is True
    assert result["agent_run"]["raw_response_bytes"] == 100
    # The read call requested at most max_output_bytes + 1 bytes; it never
    # asked for the file's full (400x larger) size.
    assert read_sizes == [101]


def test_build_allowlisted_env_only_includes_allowed_names(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "do-not-leak")
    env = ah.build_allowlisted_env()
    assert "PATH" in env
    assert "SUPER_SECRET_TOKEN" not in env
    assert set(env) <= set(ah.DEFAULT_ENV_ALLOWLIST)


def test_run_capped_subprocess_enforces_timeout_and_kills_process_group(tmp_path: Path) -> None:
    started = time.monotonic()
    result = ah.run_capped_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env=ah.build_allowlisted_env(),
        timeout=1.0,
        max_bytes=1024,
    )
    elapsed = time.monotonic() - started
    assert result["timed_out"] is True
    assert elapsed < 10  # killed promptly, not left running for the full 30s


def test_run_capped_subprocess_enforces_output_cap(tmp_path: Path) -> None:
    result = ah.run_capped_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 2_000_000)"],
        cwd=tmp_path,
        env=ah.build_allowlisted_env(),
        timeout=10.0,
        max_bytes=1000,
    )
    assert result["truncated"] is True
    assert len(result["stdout"]) == 1000


def test_run_capped_subprocess_reports_exit_status(tmp_path: Path) -> None:
    result = ah.run_capped_subprocess(
        [sys.executable, "-c", "print('hi')"],
        cwd=tmp_path,
        env=ah.build_allowlisted_env(),
        timeout=10.0,
        max_bytes=1024,
    )
    assert result["exit_status"] == 0
    assert result["stdout"].strip() == b"hi"
    assert result["timed_out"] is False
    assert result["truncated"] is False
