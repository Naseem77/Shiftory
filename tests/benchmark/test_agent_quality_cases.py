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

import tempfile
from pathlib import Path

import pytest

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
    future capture silently reusing/swapping models) from passing again."""
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
        v.validate_against_schema(agent_run, "agent-run-v1")
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
            v.validate_against_schema(agent_run, "agent-run-v1")
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
INVALIDATED_CASE_IDS = (
    sorted(path.name for path in INVALIDATED_DIR.iterdir() if path.is_dir())
    if INVALIDATED_DIR.is_dir()
    else []
)


@pytest.mark.parametrize("case_id", INVALIDATED_CASE_IDS)
def test_withdrawn_captures_are_hash_verified_and_never_officially_enumerated(
    case_id: str,
) -> None:
    """A withdrawn (answer-key-leak) capture must carry a schema-valid
    invalidated-capture-v1 record whose every referenced digest matches the
    actual archived bytes (proving the archive is an honest, unmodified copy
    of what was withdrawn -- see validation.py's validate_invalidated_capture),
    and the archive directory this lives under must never be reachable by
    runner.py's official case/candidate enumeration, so it can never be
    accidentally scored or published as a real result."""
    case_root = INVALIDATED_DIR / case_id
    configs = sorted(
        path.name
        for path in case_root.iterdir()
        if path.is_dir() and (path / "invalidation.json").is_file()
    )
    assert configs == ["config-a", "config-b"], (
        f"invalidated/{case_id} must contain exactly config-a and config-b, found {configs}"
    )
    for config in configs:
        record = v.validate_invalidated_capture(case_root, config)
        assert record["status"] == "invalidated-answer-leak-v1"
        replacement = record["replacement_capture"]
        # The replacement must be a currently-official case/config, never
        # pointing back at another withdrawn archive.
        assert replacement["case_id"] in CASE_IDS, (
            f"invalidated/{case_id}/{config}'s replacement_capture.case_id "
            f"{replacement['case_id']!r} is not an official case"
        )
        replacement_path = CASES_DIR.parent.parent.parent / replacement["path"]
        assert replacement_path.is_dir(), (
            f"invalidated/{case_id}/{config}'s replacement_capture.path does not exist on disk"
        )
        # The withdrawn raw response must not be byte-identical to the
        # official replacement's explanation -- if it were, withdrawal would
        # have accomplished nothing (this also incidentally proves the
        # replacement was actually regenerated, not just copied back).
        official_path = (
            CASES_DIR
            / replacement["case_id"]
            / "captured"
            / replacement["config_id"]
            / "explanation.json"
        )
        if official_path.is_file():
            assert v.sha256_file(official_path) != record["archived_raw_response_sha256"], (
                f"invalidated/{case_id}/{config}: official replacement is byte-identical "
                "to the withdrawn capture"
            )

        # The original, leaked case id must never also be a live, officially
        # scored case -- proving withdrawal actually removed it from
        # circulation rather than merely renaming a still-reachable duplicate.
        original_case_id = record["original_case_id"]
        assert original_case_id not in CASE_IDS, (
            f"the withdrawn, leaked case id {original_case_id!r} must never also be a live, "
            "officially-scored case"
        )

    # The archive must never live where runner.py's official enumeration
    # would find it: case_ids() only scans CASES_DIR (benchmarks/agent_quality/cases),
    # and captured_candidates() only scans <case_dir>/captured/ beneath it.
    assert INVALIDATED_DIR.parent == CASES_DIR.parent
    assert INVALIDATED_DIR != CASES_DIR
    assert not str(INVALIDATED_DIR).startswith(str(CASES_DIR) + "/")
