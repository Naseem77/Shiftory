"""Tests for the opt-in agent capture harness: prompt-package isolation
(trust boundary), the strict raw-response protocol, and bounded/streamed
subprocess execution with timeout and output-cap enforcement.

The leakage-regression tests below are a real, bounded, but fundamentally
limited safety net -- see ``test_prepare_never_copies_auditor_content``'s
docstring for exactly what they can and cannot prove. A prior review found
one real leak this net did not catch (a paraphrased, not verbatim, rubric
conclusion embedded in a case's own ``description``/``category`` fields,
which are legitimately part of the materialized prompt package and so
cannot be caught by "auditor content never appears" checks alone). Catching
paraphrased or conceptual leakage is not fully automatable; the manual,
complete-package review performed alongside this benchmark's independent
review rounds remains load-bearing, not these tests.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from benchmarks.agent_quality import agent_harness as ah
from benchmarks.agent_quality import validation as v
from benchmarks.runner import git

CASES_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
AUDITOR_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "auditor"
CASE_ID = "reordering-guard-clause"
ALL_CASE_IDS = sorted(path.name for path in CASES_DIR.iterdir() if path.is_dir())


def _materialize_and_collect_text(case_id: str, out_dir: Path) -> str:
    """Materialize case_id's prompt package and return every byte an agent
    reading it could see as text: every non-.git file, plus the fixture
    repository's reconstructed commit log (subjects and bodies), since a
    generating agent is expected to run ``git log`` against ``repository/``
    and that text is genuinely part of what it can read."""
    ah.prepare_prompt_package(CASES_DIR / case_id, out_dir, case_id)
    all_paths = [path for path in out_dir.rglob("*") if path.is_file() and ".git" not in path.parts]
    assert all("auditor" not in path.parts for path in all_paths)
    assert not any(path.name == "rubric.json" for path in all_paths)
    file_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in all_paths)
    log_text = str(git(out_dir / "repository", "log", "--all", "--format=%B"))
    return file_text + "\n" + log_text


@pytest.mark.parametrize("case_id", ALL_CASE_IDS)
def test_prepare_never_copies_auditor_content(case_id: str, tmp_path: Path) -> None:
    """No file in the materialized prompt package (nor the reconstructed
    fixture repository's commit log) ever contains an exact copy of one of
    its own rubric's required-fact descriptions. This is a real, useful, but
    strictly partial check: it catches only VERBATIM copies of a full fact
    sentence. It cannot catch a paraphrase, a single revealing keyword, or a
    leak that spans a fact's conclusion split across multiple sentences --
    those require a human/agent to actually read case.json against the
    rubric and judge whether an agent could infer the answer from it, which
    is exactly what missed the delete-add-not-a-rename leak fixed in this
    revision (see benchmarks/agent_quality/invalidated/) until an
    independent review caught it by manual reading, not by this test."""
    rubric = v.load_json_strict(AUDITOR_DIR / case_id / "rubric.json")
    leak_strings = [fact["description"] for fact in rubric["required_facts"]]

    combined_text = _materialize_and_collect_text(case_id, tmp_path / "prompt")
    for leaked in leak_strings:
        assert leaked not in combined_text, (
            f"{case_id}: rubric fact description leaked into prompt package: {leaked!r}"
        )


# A reviewed, deliberately-neutral snapshot of every case's public-input
# category/description. Committed here as an explicit allowlist so that any
# future edit to these fields -- the exact class of change that caused the
# delete-add-not-a-rename leak -- fails this test and forces a fresh manual
# leak review rather than silently shipping. This snapshot proves the fields
# are UNCHANGED from what was last manually reviewed; it does not and cannot
# prove the reviewed text itself is semantically safe for a case not yet
# invented -- that judgment happens in the review that updates this dict.
REVIEWED_NEUTRAL_PUBLIC_FIELDS = {
    "binary-asset-replacement": {
        "category": "non-text-unsupported",
        "description": (
            "A small binary asset file's raw bytes are replaced between base and head; "
            "there is no textual diff to describe what changed within the file."
        ),
    },
    "context-limited-helper-call": {
        "category": "ambiguous-context-limited",
        "description": (
            "A call to a helper function named validate() changes one of its keyword "
            "arguments from False to True."
        ),
    },
    "cross-file-validation-edit": {
        "category": "cross-file-definition-change",
        "description": (
            "The function validate_email is removed from validators.py. A different "
            "file, utils.py, gains a new function definition also named validate_email."
        ),
    },
    "error-swallow-to-raise": {
        "category": "state-error-handling",
        "description": (
            "A config-loading function's except clause changes from swallowing a parse "
            "error to re-raising it."
        ),
    },
    "reordering-guard-clause": {
        "category": "ordering-control-flow",
        "description": (
            "A single Python function's two leading statements -- an early-return "
            "None-check and a lookup call -- are reordered between base and head."
        ),
    },
    "threshold-value-replacement": {
        "category": "value-replacement-with-implications",
        "description": (
            "Two numeric constants controlling a network retry loop's timeout and "
            "attempt count both change between base and head."
        ),
    },
}


def test_public_fields_match_the_reviewed_neutral_allowlist() -> None:
    """Pins every case's materialized category/description to the exact text
    that was last manually reviewed for answer-key leakage. Any edit to
    case.json's category/description -- including the kind of paraphrased
    leak this revision found and fixed -- must update this allowlist
    deliberately, which is the trigger for a fresh manual leak review, not an
    automated proof that the new text is safe."""
    assert set(ALL_CASE_IDS) == set(REVIEWED_NEUTRAL_PUBLIC_FIELDS), (
        "every case needs a reviewed-neutral allowlist entry (add/remove one deliberately, "
        "as part of a manual leak review, when a case is added/removed/renamed)"
    )
    for case_id in ALL_CASE_IDS:
        case = v.load_json_strict(CASES_DIR / case_id / "case.json")
        expected = REVIEWED_NEUTRAL_PUBLIC_FIELDS[case_id]
        assert case["category"] == expected["category"], (
            f"{case_id}: category changed from the last reviewed value -- "
            "re-review for answer-key leakage before updating this allowlist"
        )
        assert case["description"] == expected["description"], (
            f"{case_id}: description changed from the last reviewed value -- "
            "re-review for answer-key leakage before updating this allowlist"
        )


def test_cross_file_validation_edit_no_longer_leaks_the_prior_answer_key(
    tmp_path: Path,
) -> None:
    """Regression test for the specific leak found and fixed in this
    revision: the case formerly named delete-add-not-a-rename disclosed both
    of its importance-5 rubric conclusions through its category
    (deletion-addition-confused-with-move) and description ('behaviorally
    weaker'), and its fixture's own commit messages ('delete-add-not-a-rename
    base'/'head') repeated the old, answer-bearing id. Pins that none of
    these contaminated strings -- nor the bare words a paraphrase of the
    conclusion would need -- appear anywhere in the corrected case's
    materialized prompt package or its reconstructed repository's commit
    log."""
    case_id = "cross-file-validation-edit"
    combined_text = _materialize_and_collect_text(case_id, tmp_path / "prompt").lower()

    forbidden = [
        "delete-add-not-a-rename",
        "deletion-addition-confused-with-move",
        "behaviorally weaker",
        "not a behavior-preserving move",
        "not-a-behavior-preserving-move",
        "confused with move",
        "confused-with-move",
    ]
    for phrase in forbidden:
        assert phrase not in combined_text, (
            f"{case_id}: previously-leaked phrase reappeared in the prompt package: {phrase!r}"
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
        engine_identity={
            "component": "shiftory",
            "verification_method": "unknown",
            "value": "unknown",
        },
        benchmark_protocol_commit={
            "commit": None,
            "verification_method": "unverified",
            "verified": False,
            "note": "test fixture, no real protocol commit to verify against",
        },
        copilot_task_info={
            "agent_type": "general-purpose",
            "configured_model": "test-model",
            "tool": "copilot-cli",
            "tool_version": "1.0.0",
            "task_id": None,
            "generation_started_at_utc": None,
            "generation_finished_at_utc": None,
            "generation_timing_unavailable_reason": (
                "test fixture: no real sub-agent invocation to time"
            ),
        },
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
    v.validate_against_schema(agent_run, "agent-run-v2")
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


# -- agent-run-v2 tagged invocation invariants -------------------------------


def test_capture_result_requires_copilot_task_info_without_command_argv(
    tmp_path: Path,
) -> None:
    """A capture_result call with no command_argv and no copilot_task_info
    would otherwise silently produce a record with an undefined invocation
    shape; this must fail loudly instead."""
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    (prompt_dir / "RAW_RESPONSE").write_bytes(json.dumps(_explanation_document()).encode())

    kwargs = _base_capture_kwargs(prompt_dir, out_dir)
    kwargs["copilot_task_info"] = None
    with pytest.raises(v.AgentQualityError, match="copilot_task_info"):
        ah.capture_result(**kwargs)


def test_capture_result_copilot_task_never_fabricates_generation_timing(
    tmp_path: Path,
) -> None:
    """When the caller cannot honestly report the generating sub-agent's real
    start/end time, capture_result must record null timing with a reason --
    never the time this bookkeeping function itself happened to run, which
    would misrepresent when generation actually occurred (the exact defect
    v1's started_at_utc/finished_at_utc had for every copilot_task capture
    in this repository: they were always mere microseconds apart)."""
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    (prompt_dir / "RAW_RESPONSE").write_bytes(json.dumps(_explanation_document()).encode())

    result = ah.capture_result(**_base_capture_kwargs(prompt_dir, out_dir))

    invocation = result["agent_run"]["invocation"]
    assert invocation["kind"] == "copilot_task"
    assert invocation["generation_started_at_utc"] is None
    assert invocation["generation_finished_at_utc"] is None
    assert invocation["generation_timing_unavailable_reason"]
    # capture_ingested_at_utc is always present and distinct in meaning from
    # (unset) generation timing -- it must never be silently substituted in.
    assert result["agent_run"]["capture_ingested_at_utc"]


def test_capture_result_local_process_records_real_observed_timing(tmp_path: Path) -> None:
    """A local_process invocation is one capture_result itself starts and
    waits on, so real generation timing must be recorded, non-null, and
    command_argv must be the actual non-empty argv used -- never an empty
    list masquerading as 'a local process ran with no arguments'."""
    prompt_dir = tmp_path / "prompt"
    prompt_dir.mkdir()
    out_dir = tmp_path / "out"
    document = _explanation_document()
    argv = [
        sys.executable,
        "-c",
        f"open('RAW_RESPONSE', 'w').write({json.dumps(json.dumps(document))})",
    ]

    kwargs = _base_capture_kwargs(prompt_dir, out_dir)
    kwargs.pop("copilot_task_info")
    kwargs["command_argv"] = argv
    kwargs["env"] = ah.build_allowlisted_env()
    result = ah.capture_result(**kwargs)

    invocation = result["agent_run"]["invocation"]
    assert invocation["kind"] == "local_process"
    assert invocation["command_argv"] == argv
    assert invocation["command_argv"] != []
    assert invocation["generation_started_at_utc"] is not None
    assert invocation["generation_finished_at_utc"] is not None
    assert invocation["exit_status"] == 0
    assert result["agent_run"]["capture_ingested_at_utc"] is not None


def test_agent_run_v2_schema_rejects_copilot_task_with_missing_timing_reason() -> None:
    """The schema itself, not just capture_result's Python logic, must
    require generation_timing_unavailable_reason whenever
    generation_started_at_utc is null for a copilot_task invocation."""
    base = _base_capture_kwargs(Path("/x"), Path("/y"))
    agent_run = {
        "schema": "shiftory.benchmark-agent-quality-agent-run/v2",
        "case_id": CASE_ID,
        "candidate_id": "captured-a",
        "provider": "test-provider",
        "model": {"name": "test-model", "version": "1.0"},
        "agent_tool_version": "1.0.0",
        "evaluation_protocol": "manual test invocation",
        "invocation": {
            "kind": "copilot_task",
            "agent_type": "general-purpose",
            "configured_model": "test-model",
            "tool": "copilot-cli",
            "tool_version": "1.0.0",
            "task_id": None,
            "generation_started_at_utc": None,
            "generation_finished_at_utc": None,
            # generation_timing_unavailable_reason deliberately omitted
        },
        "capture_ingested_at_utc": "2024-01-01T00:00:00Z",
        "prompt_package_manifest": [],
        "prompt_package_digest": "a" * 64,
        "evidence_sha256": "b" * 64,
        "engine_identity": base["engine_identity"],
        "benchmark_protocol_commit": base["benchmark_protocol_commit"],
        "generator_access_profile": {
            "repository_access": True,
            "filesystem_scope": "x",
            "network_access": False,
            "tool_access": [],
        },
        "isolation_method": "protocol",
        "raw_response_sha256": "d" * 64,
        "raw_response_bytes": 0,
        "truncated": False,
        "migrated_from_v1_sha256": None,
    }
    with pytest.raises(v.AgentQualityError, match="does not match its schema"):
        v.validate_against_schema(agent_run, "agent-run-v2")


# -- full prompt-package reconstruction and protocol-commit verification -----


def test_reconstruct_full_prompt_manifest_at_commit_matches_real_capture() -> None:
    """Reconstructing reordering-guard-clause's ENTIRE prompt package (case.json,
    SKILL.md, INSTRUCTIONS.md, and the reconstructed repository) using ONLY
    files committed at its recorded benchmark_protocol_commit must reproduce a
    manifest byte-identical to what that case's real capture actually
    recorded -- proving the full committed protocol at that commit, not just
    case.json, produced this content. The protocol commit is read from the
    capture's own agent-run.json (not hardcoded) so this test stays correct
    across recaptures under a newer frozen protocol revision."""
    agent_run_path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "agent_quality"
        / "cases"
        / CASE_ID
        / "captured"
        / "config-a"
        / "agent-run.json"
    )
    agent_run = json.loads(agent_run_path.read_text())
    protocol_commit = agent_run["benchmark_protocol_commit"]["commit"]

    manifest = ah.reconstruct_full_prompt_manifest_at_commit(protocol_commit, CASE_ID)
    assert manifest is not None

    recorded = sorted(agent_run["prompt_package_manifest"], key=lambda entry: entry["path"])
    reconstructed = sorted(manifest, key=lambda entry: entry["path"])
    assert recorded == reconstructed


def test_reconstruct_full_prompt_manifest_at_commit_rejects_missing_commit() -> None:
    assert ah.reconstruct_full_prompt_manifest_at_commit("f" * 40, CASE_ID) is None


def test_reconstruct_full_prompt_manifest_at_commit_rejects_missing_case() -> None:
    """A real commit that exists, but never had this case at all, must fail
    reconstruction rather than silently returning an empty/partial manifest."""
    assert (
        ah.reconstruct_full_prompt_manifest_at_commit(
            "0aaeb5c149aaa5a5d3a8e04a1858235c9cafe646", "nonexistent-case-xyz"
        )
        is None
    )


def test_recompute_benchmark_protocol_commit_verification_confirms_real_captures() -> None:
    """Every official capture's benchmark_protocol_commit.verified: true claim
    must hold up under independent full-manifest recomputation against this
    repository's real git history, not merely be trusted as self-reported
    data."""
    cases_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
    for case_dir in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        for config in ("config-a", "config-b"):
            agent_run_path = case_dir / "captured" / config / "agent-run.json"
            if not agent_run_path.is_file():
                continue
            agent_run = json.loads(agent_run_path.read_text())
            assert agent_run["benchmark_protocol_commit"]["verified"] is True
            assert ah.recompute_benchmark_protocol_commit_verification(agent_run) is True, (
                f"{case_dir.name}/{config}"
            )


def test_recompute_benchmark_protocol_commit_verification_rejects_wrong_manifest() -> None:
    """A benchmark_protocol_commit whose recorded manifest no longer matches
    the referenced commit's reconstructable protocol must fail recomputation
    -- proving this is a real comparison, not a rubber stamp."""
    case_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
    agent_run = json.loads(
        (case_dir / CASE_ID / "captured" / "config-a" / "agent-run.json").read_text()
    )
    manifest = agent_run["prompt_package_manifest"]
    for entry in manifest:
        if entry["path"] == "case.json":
            entry["sha256"] = "0" * 64
    assert ah.recompute_benchmark_protocol_commit_verification(agent_run) is False


def test_recompute_benchmark_protocol_commit_verification_rejects_missing_commit() -> None:
    case_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
    agent_run = json.loads(
        (case_dir / CASE_ID / "captured" / "config-a" / "agent-run.json").read_text()
    )
    agent_run["benchmark_protocol_commit"] = dict(
        agent_run["benchmark_protocol_commit"], commit=None
    )
    assert ah.recompute_benchmark_protocol_commit_verification(agent_run) is False


def test_recompute_benchmark_protocol_commit_verification_rejects_nonexistent_commit() -> None:
    case_dir = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
    agent_run = json.loads(
        (case_dir / CASE_ID / "captured" / "config-a" / "agent-run.json").read_text()
    )
    agent_run["benchmark_protocol_commit"] = dict(
        agent_run["benchmark_protocol_commit"], commit="f" * 40
    )
    assert ah.recompute_benchmark_protocol_commit_verification(agent_run) is False
