"""End-to-end tests over every committed agent-quality case: schema
validation, fixture integrity, structural validity of both synthetic
candidates against the real shiftory.explain.validator, and the synthetic
discrimination property (Delta 7 / point 7): a claim-perfect baseline passes
the aggregator's generic gate and a deliberately defective adversarial
candidate does not. This is a scorer-arithmetic property test, not evidence
that any real agent performs this way -- see auditor rubric provenance
disclaimers for what remains a provisional, single-pass audit.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from benchmarks.agent_quality import agent_harness as ah
from benchmarks.agent_quality import aggregate
from benchmarks.agent_quality import fixtures as fx
from benchmarks.agent_quality import validation as v
from shiftory.explain.validator import validate_explanation

CASES_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "cases"
AUDITOR_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "agent_quality" / "auditor"

CASE_IDS = sorted(path.name for path in CASES_DIR.iterdir() if path.is_dir())


def _load_case(case_id: str) -> tuple[dict, dict]:
    case_dir = CASES_DIR / case_id
    case = v.load_json_strict(case_dir / "case.json")
    v.validate_against_schema(case, "case-v1")
    rubric = v.load_json_strict(AUDITOR_DIR / case_id / "rubric.json")
    v.validate_against_schema(rubric, "rubric-v1")
    assert rubric["case_id"] == case_id
    return case, rubric


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_and_rubric_are_schema_valid(case_id: str) -> None:
    case, rubric = _load_case(case_id)
    assert case["id"] == case_id
    assert len(rubric["required_facts"]) >= 1


# A reviewed pin of every rubric's ground-truth required_facts, keyed by a
# stable hash of their sorted-key JSON content (not the whole rubric file, so
# unrelated provenance/version bumps don't trip this). Any edit to a
# required_fact -- the actual answer key a real capture is graded against --
# must update this pin deliberately, which is the trigger for a fresh manual
# review of whether that edit could have been influenced by an already-seen
# real capture's output, rather than a silent change nothing here would catch
# otherwise (benchmark_protocol_commit verification intentionally covers only
# the generation-side prompt package, never the rubric -- see the
# methodology doc's "Provenance" section).
REVIEWED_REQUIRED_FACTS_DIGESTS = {
    "binary-asset-replacement": (
        "b1f396c4cac5696e230c9cef4aabac9c4d48c92f4b9e34cb2ca1aa4eb25bc4d1"
    ),
    "context-limited-helper-call": (
        "8b849a7c8ec82665b189fa5998c48909950995e5d1a0b5e18dc6432dbc652d1d"
    ),
    "cross-file-validation-edit": (
        "ce1ac813cc1002d55983c18069c1ae7390c2f880c58f0d595f7dc925464188b8"
    ),
    "error-swallow-to-raise": "89c064201e8c81e93d503fe03cc1b3f7c80fa624847b19640bd6968cfc7eeec8",
    "reordering-guard-clause": ("5af2947a3e1d2563b86c0d3c9a92230842658fc2b32e264c69098cf8a6ca9828"),
    "threshold-value-replacement": (
        "07b6bd781c07a792503ea17775d1541cade9261ceb196f75f45f67e10d97b71c"
    ),
}


def test_required_facts_match_the_reviewed_pin() -> None:
    """Pins every case's rubric required_facts (the actual ground truth real
    captures are graded against) to what was last manually reviewed. This is
    the concrete, code-enforced half of the disclosed rubric-provenance gap:
    it cannot prove a rubric was never adjusted after seeing a real capture's
    output in the past, but it does guarantee no case's required_facts can
    silently change going forward without a deliberate, reviewed pin update."""
    assert set(CASE_IDS) == set(REVIEWED_REQUIRED_FACTS_DIGESTS)
    for case_id in CASE_IDS:
        rubric = v.load_json_strict(AUDITOR_DIR / case_id / "rubric.json")
        digest = hashlib.sha256(
            json.dumps(rubric["required_facts"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        assert digest == REVIEWED_REQUIRED_FACTS_DIGESTS[case_id], (
            f"{case_id}: required_facts changed from the last reviewed pin -- "
            "re-review for retroactive-tampering risk before updating this pin"
        )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_fixture_reconstructs_to_pinned_commits_and_inventory(case_id: str) -> None:
    case_dir = CASES_DIR / case_id
    with tempfile.TemporaryDirectory(prefix=f"fixture-{case_id}-") as tmp:
        repository, resolved = fx.reconstruct_fixture(case_dir, Path(tmp), case_id)
        assert repository.is_dir()
        assert set(resolved) == {"base", "head"}


def _candidate_dirs(case_id: str) -> list[tuple[str, str, Path]]:
    """(candidate_id, candidate_kind, directory) for every synthetic candidate."""
    case_dir = CASES_DIR / case_id
    pairs = []
    for kind in ("baseline", "adversarial"):
        directory = case_dir / "synthetic" / kind
        if directory.is_dir():
            pairs.append((f"synthetic_{kind}", f"synthetic_{kind}", directory))
    return pairs


PREDECLARED_CONFIGS = ("config-a", "config-b")
PREDECLARED_MODELS = {"config-a": "gpt-5.3-codex", "config-b": "gemini-3.7-flash"}


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_case_has_exactly_the_two_predeclared_real_captures(case_id: str) -> None:
    """Enforces the full-suite invariant: every case must have exactly the two
    predeclared capture configurations (config-a, config-b) invoked exactly
    once each, using the two predeclared, distinct models, with a
    corresponding agent-run.json and either a materialized explanation.json +
    evaluation, or an invalid_candidate structural-failure evaluation.
    Prevents a partial-suite state (some cases missing real captures, or a
    future capture silently reusing/swapping models) from passing again.
    Also enforces every official capture's provenance is truthful agent-run-v2
    (never v1, which conflated ingestion time with generation time), that its
    benchmark_protocol_commit is cryptographically verified against a real
    committed case.json (not merely asserted), and that its tagged invocation
    never lets an empty local_process argv or a missing copilot_task timing
    reason pass silently."""
    captured_root = CASES_DIR / case_id / "captured"
    assert captured_root.is_dir(), f"{case_id} is missing its captured/ directory entirely"

    present = sorted(path.name for path in captured_root.iterdir() if path.is_dir())
    assert present == list(PREDECLARED_CONFIGS), (
        f"{case_id}/captured must contain exactly {PREDECLARED_CONFIGS}, found {present}"
    )

    for config in PREDECLARED_CONFIGS:
        directory = captured_root / config
        agent_run_path = directory / "agent-run.json"
        raw_response_path = directory / "raw-response.txt"
        assert agent_run_path.is_file(), f"{case_id}/{config} is missing agent-run.json"
        assert raw_response_path.is_file() or (directory / "raw-response.bin").is_file(), (
            f"{case_id}/{config} is missing its raw response bytes"
        )
        agent_run = v.load_json_strict(agent_run_path)
        v.validate_against_schema(agent_run, "agent-run-v2")
        assert agent_run["schema"] == "shiftory.benchmark-agent-quality-agent-run/v2", (
            f"{case_id}/{config}: every official capture must be agent-run-v2, never v1 "
            "(v1 is preserved only in withdrawn-capture archives)"
        )
        protocol = agent_run["benchmark_protocol_commit"]
        assert protocol["verified"] is True, (
            f"{case_id}/{config}: benchmark_protocol_commit must be cryptographically "
            f"verified (prompt_manifest_hash_match), found unverified: {protocol['note']!r}"
        )
        # Never trust the self-reported 'verified' flag alone -- independently
        # recompute it against this repository's real git history every time,
        # so a future edit to case.json or a hand-set true value can never
        # silently pass.
        assert ah.recompute_benchmark_protocol_commit_verification(agent_run), (
            f"{case_id}/{config}: benchmark_protocol_commit.verified is true, but "
            "independently recomputing git show <commit>:.../case.json's sha256 does "
            "not match this capture's own prompt_package_manifest -- the self-reported "
            "flag does not hold up under real verification"
        )
        invocation = agent_run["invocation"]
        if invocation["kind"] == "local_process":
            assert invocation["command_argv"], (
                f"{case_id}/{config}: a local_process invocation must never have empty "
                "command_argv masquerading as a real subprocess invocation"
            )
        else:
            assert invocation["kind"] == "copilot_task"
            if invocation["generation_started_at_utc"] is None:
                assert invocation.get("generation_timing_unavailable_reason"), (
                    f"{case_id}/{config}: a copilot_task invocation with unknown generation "
                    "timing must state why, never silently omit it"
                )
        assert agent_run["model"]["name"] == PREDECLARED_MODELS[config], (
            f"{case_id}/{config} must use the predeclared model "
            f"{PREDECLARED_MODELS[config]!r}, found {agent_run['model']['name']!r}"
        )

        candidate_id = f"captured_{config.replace('-', '_')}"
        evaluation_path = AUDITOR_DIR / case_id / "evaluations" / f"{candidate_id}.json"
        assert evaluation_path.is_file(), (
            f"{case_id}/{candidate_id} is missing its candidate-evaluation-v1 record "
            "(every predeclared capture needs one, even a structural-failure-only record)"
        )
        evaluation = v.load_json_strict(evaluation_path)
        v.validate_against_schema(evaluation, "candidate-evaluation-v1")
        has_explanation = (directory / "explanation.json").is_file()
        has_invalid = evaluation.get("invalid_candidate") is not None
        assert has_explanation != has_invalid, (
            f"{case_id}/{candidate_id}: exactly one of explanation.json presence or "
            "invalid_candidate must hold, never both or neither"
        )
        if has_explanation:
            # Every valid capture must show dual-audit or an explicit
            # provisional-single-audit status -- never silently unaudited.
            passes = evaluation.get("annotation_passes") or []
            assert len(passes) >= 1, f"{case_id}/{candidate_id} has no recorded annotation passes"
            for entry in passes:
                assert entry["annotation_provenance"]["actor_type"] in ("agent", "human")
            if len(passes) >= 2:
                assert evaluation.get("adjudication") is not None, (
                    f"{case_id}/{candidate_id} has 2+ annotation passes but no adjudication record"
                )


UNAFFECTED_FREEZE_COMMIT = "5c7289bd8e540317ce45d4407044c5c846698ecb"
V3_RECAPTURED_CASES = (
    "error-swallow-to-raise",
    "threshold-value-replacement",
    "binary-asset-replacement",
)


def test_all_official_captures_bind_to_a_verified_frozen_commit_with_unique_handles() -> None:
    """Every one of the 12 official captures must independently verify for
    both full-manifest-and-registry content-equality and chronological
    precommitment, and carry a non-null, globally-unique
    orchestrator_agent_handle. reordering-guard-clause,
    context-limited-helper-call, and cross-file-validation-edit -- unaffected
    by this round's registry_version 3 recapture -- must still reference the
    original protocol-freeze commit. error-swallow-to-raise,
    threshold-value-replacement, and binary-asset-replacement must reference
    a DIFFERENT commit whose committed protocol_registry.json is
    registry_version 3 or later (the registry_version 2 captures for these
    three cases were themselves second invocations after an unrecoverable
    shared-directory collision -- see the invalidated-generation-attempt-v1
    incident records and their retry-after-unrecoverable-shared-directory-collision
    archive records -- so registry_version 2 can never again be a valid
    official protocol commit for these three cases). A handle collision
    across different captures, or any capture silently bound to a different
    or unverified commit, would defeat the entire point of a single,
    precommitted protocol per case."""
    handles: dict[str, str] = {}
    for case_id in CASE_IDS:
        for config in PREDECLARED_CONFIGS:
            agent_run_path = CASES_DIR / case_id / "captured" / config / "agent-run.json"
            agent_run = v.load_json_strict(agent_run_path)
            protocol = agent_run["benchmark_protocol_commit"]
            assert ah.recompute_benchmark_protocol_commit_verification(agent_run), (
                f"{case_id}/{config}: benchmark_protocol_commit does not independently "
                "verify for full-manifest-and-registry content-equality"
            )
            assert ah.verify_protocol_precommitment(agent_run), (
                f"{case_id}/{config}: the referenced commit's committer date must strictly "
                "precede this capture's own capture_ingested_at_utc"
            )
            if case_id in V3_RECAPTURED_CASES:
                assert protocol["commit"] != UNAFFECTED_FREEZE_COMMIT, (
                    f"{case_id}/{config}: this case was recaptured under registry_version 3 "
                    "and must never again reference the registry_version 2 freeze commit"
                )
                registry = ah.reconstruct_protocol_registry_at_commit(protocol["commit"])
                assert registry is not None and registry["registry_version"] >= 3, (
                    f"{case_id}/{config}: expected a registry_version >= 3 commit, "
                    f"found registry_version {registry['registry_version'] if registry else None!r}"
                )
            else:
                assert protocol["commit"] == UNAFFECTED_FREEZE_COMMIT, (
                    f"{case_id}/{config}: expected the unaffected freeze commit "
                    f"{UNAFFECTED_FREEZE_COMMIT!r}, found {protocol['commit']!r}"
                )
            handle = agent_run["invocation"]["orchestrator_agent_handle"]
            assert handle["externally_verifiable"] is False, (
                f"{case_id}/{config}: orchestrator_agent_handle must never claim to be "
                "externally verifiable -- it is a caller-supplied label, not provider/"
                "platform attestation"
            )
            assert handle["value"], (
                f"{case_id}/{config}: every official capture must carry a non-null "
                "orchestrator_agent_handle.value (null is only for archived/legacy records)"
            )
            key = f"{case_id}/{config}"
            assert handle["value"] not in handles.values(), (
                f"{key}'s orchestrator_agent_handle {handle['value']!r} collides with "
                f"{[k for k, v_ in handles.items() if v_ == handle['value']]} -- handles "
                "must be unique across all official captures so this benchmark's own "
                "bookkeeping can distinguish invocations"
            )
            handles[key] = handle["value"]
    assert len(handles) == 12, f"expected 12 official captures, found {len(handles)}"


def _captured_candidate_dirs(case_id: str) -> list[Path]:
    case_dir = CASES_DIR / case_id
    captured_root = case_dir / "captured"
    if not captured_root.is_dir():
        return []
    return sorted(path for path in captured_root.iterdir() if path.is_dir())


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_captured_real_candidates_are_valid_and_never_gated(case_id: str) -> None:
    """Real captured-agent candidates (when present) are scored for structural
    validity and reported, but per Delta 7 / point 7 they are never subject to
    a pass/fail quality gate -- `gate` must always be null for them."""
    directories = _captured_candidate_dirs(case_id)
    if not directories:
        pytest.skip(f"{case_id} has no committed real captures yet")

    case_dir = CASES_DIR / case_id
    _, rubric = _load_case(case_id)

    with tempfile.TemporaryDirectory(prefix=f"captured-{case_id}-") as tmp:
        tmp_path = Path(tmp)
        repository, resolved = fx.reconstruct_fixture(case_dir, tmp_path, case_id)
        evidence = fx.run_analyze(
            repository, resolved["base"], resolved["head"], tmp_path / "evidence.json"
        )

        for directory in directories:
            candidate_id = f"captured_{directory.name.replace('-', '_')}"
            agent_run = v.load_json_strict(directory / "agent-run.json")
            v.validate_against_schema(agent_run, "agent-run-v2")
            assert agent_run["isolation_method"] == "protocol"

            evaluation = v.load_json_strict(
                AUDITOR_DIR / case_id / "evaluations" / f"{candidate_id}.json"
            )

            if not (directory / "explanation.json").is_file():
                assert evaluation.get("invalid_candidate") is not None
                v.validate_candidate_evaluation(None, evaluation, rubric)
                score = aggregate.aggregate_score(
                    case_id=case_id,
                    candidate_id=candidate_id,
                    candidate_kind="captured_real_run",
                    explanation_sha256=None,
                    evaluation=evaluation,
                    rubric=rubric,
                )
                assert score["structural_failure"] is not None
                assert score["gate"] is None
                continue

            # Captured real candidates are NOT templates: the agent cited real
            # evidence ids directly, so no conceptual-hunk instantiation applies.
            explanation = v.load_json_strict(directory / "explanation.json")
            accounting = validate_explanation(evidence, explanation).to_dict()
            explanation_sha = v.sha256_file(directory / "explanation.json")
            assert evaluation["explanation_sha256"] == explanation_sha
            assert agent_run["raw_response_sha256"] == explanation_sha

            v.validate_candidate_evaluation(explanation, evaluation, rubric)
            score = aggregate.aggregate_score(
                case_id=case_id,
                candidate_id=candidate_id,
                candidate_kind="captured_real_run",
                explanation_sha256=explanation_sha,
                evaluation=evaluation,
                rubric=rubric,
                accounting=accounting,
                item_count=len(explanation["items"]),
                audit_status=aggregate.derive_audit_status(evaluation),
            )
            v.validate_against_schema(score, "score-v1")
            # The core discipline this test enforces: real agent quality is
            # reported, never gated.
            assert score["gate"] is None


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_synthetic_candidates_are_valid_and_discriminate(case_id: str) -> None:
    case_dir = CASES_DIR / case_id
    _, rubric = _load_case(case_id)

    with tempfile.TemporaryDirectory(prefix=f"case-{case_id}-") as tmp:
        tmp_path = Path(tmp)
        repository, resolved = fx.reconstruct_fixture(case_dir, tmp_path, case_id)
        evidence = fx.run_analyze(
            repository, resolved["base"], resolved["head"], tmp_path / "evidence.json"
        )

        scores: dict[str, dict] = {}
        for candidate_id, candidate_kind, directory in _candidate_dirs(case_id):
            template = v.load_json_strict(directory / "explanation.json")
            resolved_explanation = fx.instantiate_explanation(template, evidence)

            # Structural validity: the real shiftory.explain.validator must accept
            # both candidates -- accounting/ownership/policy are not what this
            # benchmark's semantic scoring measures.
            accounting = validate_explanation(evidence, resolved_explanation).to_dict()
            assert accounting["line_coverage_ratio"] == 1.0
            assert accounting["hunk_coverage_ratio"] == 1.0
            assert accounting["unit_coverage_ratio"] == 1.0

            explanation_sha = fx.explanation_sha256(resolved_explanation)
            evaluation = v.load_json_strict(
                AUDITOR_DIR / case_id / "evaluations" / f"{candidate_id}.json"
            )
            assert evaluation["explanation_sha256"] == explanation_sha, (
                f"{case_id}/{candidate_id}: committed explanation_sha256 does not match "
                "the fixture's actual instantiated explanation bytes -- the evaluation "
                "was authored against stale/different fixture content."
            )
            v.validate_candidate_evaluation(resolved_explanation, evaluation, rubric)

            score = aggregate.aggregate_score(
                case_id=case_id,
                candidate_id=candidate_id,
                candidate_kind=candidate_kind,
                explanation_sha256=explanation_sha,
                evaluation=evaluation,
                rubric=rubric,
                accounting=accounting,
                item_count=len(resolved_explanation["items"]),
            )
            v.validate_against_schema(score, "score-v1")
            scores[candidate_id] = score

        assert "synthetic_baseline" in scores, f"{case_id} is missing a synthetic baseline"
        assert "synthetic_adversarial" in scores, f"{case_id} is missing a synthetic adversarial"

        baseline = scores["synthetic_baseline"]
        adversarial = scores["synthetic_adversarial"]

        # The baseline is authored to be claim-perfect; the adversarial is
        # authored to fail specifically on hallucination/omission/uncertainty.
        assert baseline["gate"]["pass"] is True, baseline["gate"]
        assert adversarial["gate"]["pass"] is False, adversarial["gate"]
        assert baseline["required_behavior_coverage"]["ratio"] == 1.0
        assert (adversarial["required_behavior_coverage"]["ratio"] or 0.0) < 1.0
        assert baseline["semantic_omissions"]["missed_count"] == 0
        assert adversarial["semantic_omissions"]["missed_count"] > 0


INVALIDATED_DIR = CASES_DIR.parent / "invalidated"


def _archive_groups() -> list[Path]:
    """Every directory that directly contains config-a/invalidation.json and
    config-b/invalidation.json -- either benchmarks/agent_quality/invalidated/
    <case>/ itself (the original answer-leak archive layout) or a named
    subdirectory of it, such as .../protocol-not-precommitted/ (used when a
    case has more than one archive group, so two withdrawal reasons for the
    same case can never share a directory or be confused with each other)."""
    if not INVALIDATED_DIR.is_dir():
        return []
    groups = []
    for path in INVALIDATED_DIR.rglob("*"):
        if path.is_dir() and (path / "config-a" / "invalidation.json").is_file():
            groups.append(path)
    return sorted(groups)


ARCHIVE_GROUPS = _archive_groups()


@pytest.mark.parametrize(
    "group_root", ARCHIVE_GROUPS, ids=lambda p: str(p.relative_to(INVALIDATED_DIR))
)
def test_withdrawn_captures_are_hash_verified_and_never_officially_enumerated(
    group_root: Path,
) -> None:
    """Every withdrawn-capture archive group must carry a schema-valid
    invalidated-capture-v1 record whose every referenced digest matches the
    actual archived bytes (proving the archive is an honest, unmodified copy
    of what was withdrawn -- see validation.py's validate_invalidated_capture),
    and the archive directory this lives under must never be reachable by
    runner.py's official case/candidate enumeration, so it can never be
    accidentally scored or published as a real result."""
    configs = sorted(
        path.name
        for path in group_root.iterdir()
        if path.is_dir() and (path / "invalidation.json").is_file()
    )
    assert configs == ["config-a", "config-b"], (
        f"{group_root} must contain exactly config-a and config-b, found {configs}"
    )
    for config in configs:
        record = v.validate_invalidated_capture(group_root, config)
        assert record["status"] in (
            "invalidated-answer-leak-v1",
            "invalidated-protocol-not-precommitted-v1",
        )
        replacement = record["replacement_capture"]
        # The replacement must be a currently-official case/config, never
        # pointing back at another withdrawn archive.
        assert replacement["case_id"] in CASE_IDS, (
            f"{group_root}/{config}'s replacement_capture.case_id "
            f"{replacement['case_id']!r} is not an official case"
        )
        replacement_path = CASES_DIR.parent.parent.parent / replacement["path"]
        assert replacement_path.is_dir(), (
            f"{group_root}/{config}'s replacement_capture.path does not exist on disk"
        )
        # The withdrawn raw response must not be byte-identical to the
        # official replacement's explanation -- if it were, withdrawal would
        # have accomplished nothing (this also incidentally proves the
        # replacement was actually regenerated, not just copied back). This
        # only applies when the replacement is itself a valid (non-structural-
        # failure) candidate -- a genuinely fresh capture is still a fresh
        # capture even if it happens to be a structural failure.
        official_path = (
            CASES_DIR
            / replacement["case_id"]
            / "captured"
            / replacement["config_id"]
            / "explanation.json"
        )
        if official_path.is_file():
            assert v.sha256_file(official_path) != record["archived_raw_response_sha256"], (
                f"{group_root}/{config}: official replacement is byte-identical "
                "to the withdrawn capture"
            )

        # The original, leaked/precommitment-violating case id must never also
        # be a live, officially-scored case -- proving withdrawal actually
        # removed it from circulation rather than merely renaming a
        # still-reachable duplicate. (For protocol-not-precommitted archives,
        # original_case_id equals corrected_case_id, which IS the live case --
        # what matters there is that the *specific captures* were removed from
        # cases/*/captured/, checked separately below.)
        record_a = v.validate_invalidated_capture(group_root, "config-a")
        if record_a["status"] == "invalidated-answer-leak-v1":
            original_case_id = record["original_case_id"]
            assert original_case_id not in CASE_IDS, (
                f"the withdrawn, leaked case id {original_case_id!r} must never also be a "
                "live, officially-scored case"
            )

    # The archive must never live where runner.py's official enumeration
    # would find it: case_ids() only scans CASES_DIR (benchmarks/agent_quality/cases),
    # and captured_candidates() only scans <case_dir>/captured/ beneath it.
    assert INVALIDATED_DIR.parent == CASES_DIR.parent
    assert INVALIDATED_DIR != CASES_DIR
    assert not str(INVALIDATED_DIR).startswith(str(CASES_DIR) + "/")


def test_exactly_twenty_archived_captures_with_distinct_reasons() -> None:
    """Enforces the full-archive invariant: exactly 20 withdrawn captures
    exist in total --
    2 answer-leak withdrawals (cross-file-validation-edit's original
    delete-add-not-a-rename captures) plus 18 protocol-not-precommitted
    withdrawals across five distinct reason codes: 2 each for
    reordering-guard-clause (protocol-not-predeclared-before-generation),
    context-limited-helper-call (prompt-fix-not-committed-before-generation),
    cross-file-validation-edit's first freeze-era replacement pair
    (neutral-prompt-not-committed-before-generation), 2 each for
    error-swallow-to-raise, threshold-value-replacement, and
    binary-asset-replacement's registry_version-2-but-registry-less captures
    (protocol-config-not-precommitted), and 2 more each for those same three
    cases' registry_version 2 replacement captures
    (retry-after-unrecoverable-shared-directory-collision -- these were
    themselves second invocations, since the true first invocation for each
    was silently lost to a shared-directory collision; see the six
    invalidated-generation-attempt-v1 incident records, which this test
    deliberately does NOT count here, since those have no recoverable bytes
    to hash-verify and are enumerated separately by
    test_exactly_six_lost_generation_attempt_incidents) -- never silently
    merged, deduplicated, or miscounted."""
    total = 0
    by_status: dict[str, int] = {}
    by_reason_code: dict[str, int] = {}
    for group_root in ARCHIVE_GROUPS:
        for config in ("config-a", "config-b"):
            record = v.validate_invalidated_capture(group_root, config)
            total += 1
            by_status[record["status"]] = by_status.get(record["status"], 0) + 1
            reason_code = record.get("reason_code")
            if reason_code is not None:
                by_reason_code[reason_code] = by_reason_code.get(reason_code, 0) + 1
    assert total == 20, f"expected exactly 20 archived captures, found {total}"
    assert by_status == {
        "invalidated-answer-leak-v1": 2,
        "invalidated-protocol-not-precommitted-v1": 18,
    }, by_status
    assert by_reason_code == {
        "protocol-not-predeclared-before-generation": 2,
        "prompt-fix-not-committed-before-generation": 2,
        "neutral-prompt-not-committed-before-generation": 2,
        "protocol-config-not-precommitted": 6,
        "retry-after-unrecoverable-shared-directory-collision": 6,
    }, by_reason_code


LOST_GENERATION_ATTEMPT_CASES = (
    "error-swallow-to-raise",
    "threshold-value-replacement",
    "binary-asset-replacement",
)


def test_exactly_six_lost_generation_attempt_incidents() -> None:
    """Pins the companion invariant to the archive count above: exactly six
    invalidated-generation-attempt-v1 incident records exist (one config-a
    and one config-b for each of the three cases affected by the
    shared-directory collision), all schema-valid, all correctly labeled
    with cause=shared-prompt-directory-raw-response-collision and
    raw_response_status=unrecoverable_overwritten, and all bound to the
    registry_version 2 protocol commit the lost invocation actually ran
    under -- never silently dropped, duplicated, or miscounted."""
    invalidated_dir = CASES_DIR.parent / "invalidated"
    total = 0
    for case_id in LOST_GENERATION_ATTEMPT_CASES:
        incident_dir = invalidated_dir / case_id / "lost-generation-attempts"
        assert incident_dir.is_dir(), f"{case_id} is missing its lost-generation-attempts/ dir"
        present = sorted(path.stem for path in incident_dir.glob("*.json"))
        assert present == ["config-a", "config-b"], (
            f"{case_id}/lost-generation-attempts must contain exactly config-a.json and "
            f"config-b.json, found {present}"
        )
        for config in present:
            record = v.validate_invalidated_generation_attempt(incident_dir / f"{config}.json")
            assert record["case_id"] == case_id
            assert record["cause"] == "shared-prompt-directory-raw-response-collision"
            assert record["raw_response_status"] == "unrecoverable_overwritten"
            assert (
                record["orchestrator_reported_metadata"]["provenance"]
                == "orchestrator_tool_reported_not_provider_attested"
            )
            total += 1
    assert total == 6, f"expected exactly 6 lost-generation-attempt incidents, found {total}"


PROTOCOL_REGISTRY_PATH = CASES_DIR.parent / "protocol_registry.json"


def test_protocol_registry_matches_committed_case_revisions() -> None:
    """The committed protocol-freeze registry must validate against its
    schema and its `case_revisions` map must exactly match the actual,
    currently-committed `case.json` `version` for every case it references
    -- this is the check that makes `protocol_registry.json` a real,
    machine-verifiable freeze rather than a document nobody actually reads.
    A hand-edited registry that drifts from committed case content, or a
    case-version bump that forgets to update the registry, must fail here,
    not go unnoticed."""
    registry = v.load_json_strict(PROTOCOL_REGISTRY_PATH)
    v.validate_protocol_registry(registry, CASES_DIR)


def test_protocol_registry_rejects_a_mismatched_case_revision() -> None:
    """A registry claiming the wrong case.json version for a real case must
    be rejected, not silently accepted -- proving the check above is a real
    cross-check, not a no-op that happens to pass on today's content."""
    registry = v.load_json_strict(PROTOCOL_REGISTRY_PATH)
    tampered = json.loads(json.dumps(registry))
    case_id = next(iter(tampered["case_revisions"]))
    tampered["case_revisions"][case_id] = tampered["case_revisions"][case_id] + 999
    with pytest.raises(v.AgentQualityError):
        v.validate_protocol_registry(tampered, CASES_DIR)
