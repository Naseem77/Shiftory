"""Suite orchestration and the manual publish flow for the agent-quality
benchmark: scores every committed candidate (synthetic baseline/adversarial
plus any real `captured/*` runs) for every case and, only via the explicit
``publish`` command, writes the canonical `scores-v1.json` snapshot documents
under `docs/benchmarks/agent-quality/`.

Mirrors `benchmarks/runner.py`'s `suite`/`suite --publish` split: `suite`
(or this module's `score_all_cases`) is safe to run against an uncommitted
worktree; only `publish` writes tracked files, and only after a maintainer
deliberately runs it. Required CI never calls `publish` -- see
`scripts/agent_quality_benchmark.py`, which only regenerates into a temporary
directory and diffs against the already-committed snapshots.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.agent_quality import aggregate
from benchmarks.agent_quality import fixtures as fx
from benchmarks.agent_quality import validation as v
from shiftory.explain.validator import validate_explanation

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "benchmarks" / "agent_quality" / "cases"
AUDITOR_DIR = ROOT / "benchmarks" / "agent_quality" / "auditor"
DOCS_DIR = ROOT / "docs" / "benchmarks" / "agent-quality"


class AgentQualityRunnerError(RuntimeError):
    """Raised for any suite/publish-level failure."""


def case_ids(cases_dir: Path = CASES_DIR) -> list[str]:
    return sorted(path.name for path in cases_dir.iterdir() if path.is_dir())


def load_case_and_rubric(
    case_id: str, cases_dir: Path, auditor_dir: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = v.load_json_strict(cases_dir / case_id / "case.json")
    v.validate_against_schema(case, "case-v1")
    rubric = v.load_json_strict(auditor_dir / case_id / "rubric.json")
    v.validate_against_schema(rubric, "rubric-v1")
    if rubric["case_id"] != case_id:
        raise AgentQualityRunnerError(f"{case_id}: rubric.case_id does not match")
    return case, rubric


def synthetic_candidates(case_dir: Path) -> list[tuple[str, str, Path]]:
    pairs = []
    for kind in ("baseline", "adversarial"):
        directory = case_dir / "synthetic" / kind
        if directory.is_dir():
            pairs.append((f"synthetic_{kind}", f"synthetic_{kind}", directory))
    return pairs


def captured_candidates(case_dir: Path) -> list[tuple[str, str, Path]]:
    captured_root = case_dir / "captured"
    if not captured_root.is_dir():
        return []
    pairs = []
    for directory in sorted(captured_root.iterdir()):
        if directory.is_dir():
            candidate_id = f"captured_{directory.name.replace('-', '_')}"
            pairs.append((candidate_id, "captured_real_run", directory))
    return pairs


def score_case(
    case_id: str,
    *,
    cases_dir: Path = CASES_DIR,
    auditor_dir: Path = AUDITOR_DIR,
) -> dict[str, Any]:
    """Score every committed candidate for one case; returns a
    ``scores-v1``-shaped document (schema, case_id, candidates[])."""
    case_dir = cases_dir / case_id
    _, rubric = load_case_and_rubric(case_id, cases_dir, auditor_dir)

    candidates: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"score-{case_id}-") as tmp:
        tmp_path = Path(tmp)
        repository, resolved = fx.reconstruct_fixture(case_dir, tmp_path, case_id)
        evidence = fx.run_analyze(
            repository, resolved["base"], resolved["head"], tmp_path / "evidence.json"
        )

        for candidate_id, candidate_kind, directory in synthetic_candidates(case_dir):
            template = v.load_json_strict(directory / "explanation.json")
            resolved_explanation = fx.instantiate_explanation(template, evidence)
            accounting = validate_explanation(evidence, resolved_explanation).to_dict()
            explanation_sha = fx.explanation_sha256(resolved_explanation)
            evaluation = v.load_json_strict(
                auditor_dir / case_id / "evaluations" / f"{candidate_id}.json"
            )
            if evaluation["explanation_sha256"] != explanation_sha:
                raise AgentQualityRunnerError(
                    f"{case_id}/{candidate_id}: committed explanation_sha256 does not match "
                    "the fixture's actual instantiated explanation bytes"
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
            candidates.append(score)

        for candidate_id, candidate_kind, directory in captured_candidates(case_dir):
            evaluation = v.load_json_strict(
                auditor_dir / case_id / "evaluations" / f"{candidate_id}.json"
            )
            if not (directory / "explanation.json").is_file():
                v.validate_candidate_evaluation(None, evaluation, rubric)
                score = aggregate.aggregate_score(
                    case_id=case_id,
                    candidate_id=candidate_id,
                    candidate_kind=candidate_kind,
                    explanation_sha256=None,
                    evaluation=evaluation,
                    rubric=rubric,
                )
            else:
                explanation = v.load_json_strict(directory / "explanation.json")
                accounting = validate_explanation(evidence, explanation).to_dict()
                explanation_sha = v.sha256_file(directory / "explanation.json")
                if evaluation["explanation_sha256"] != explanation_sha:
                    raise AgentQualityRunnerError(
                        f"{case_id}/{candidate_id}: committed explanation_sha256 does not match "
                        "the captured explanation.json bytes"
                    )
                v.validate_candidate_evaluation(explanation, evaluation, rubric)
                score = aggregate.aggregate_score(
                    case_id=case_id,
                    candidate_id=candidate_id,
                    candidate_kind=candidate_kind,
                    explanation_sha256=explanation_sha,
                    evaluation=evaluation,
                    rubric=rubric,
                    accounting=accounting,
                    item_count=len(explanation["items"]),
                )
            v.validate_against_schema(score, "score-v1")
            candidates.append(score)

    if not candidates:
        raise AgentQualityRunnerError(f"{case_id}: no candidates to score")
    document = {
        "schema": "shiftory.benchmark-agent-quality-scores/v1",
        "case_id": case_id,
        "candidates": candidates,
    }
    v.validate_against_schema(document, "scores-v1")
    return document


def score_all_cases(
    *, cases_dir: Path = CASES_DIR, auditor_dir: Path = AUDITOR_DIR
) -> dict[str, dict[str, Any]]:
    return {
        case_id: score_case(case_id, cases_dir=cases_dir, auditor_dir=auditor_dir)
        for case_id in case_ids(cases_dir)
    }


def _snapshot_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def publish(*, docs_dir: Path = DOCS_DIR) -> None:
    """Write docs/benchmarks/agent-quality/<case>/scores-v1.json for every
    case. Manual only -- never called by required CI."""
    scores = score_all_cases()
    for case_id, document in scores.items():
        target = docs_dir / case_id / "scores-v1.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_snapshot_bytes(document))
        print(f"published {target.relative_to(ROOT)}")


def regenerate_and_diff(*, docs_dir: Path = DOCS_DIR) -> list[str]:
    """Regenerate every case's scores into a temp dir and byte-diff against
    the committed snapshot. Returns a list of human-readable mismatch
    descriptions (empty means fully reproducible). Never writes to docs_dir."""
    mismatches: list[str] = []
    scores = score_all_cases()
    for case_id, document in scores.items():
        committed_path = docs_dir / case_id / "scores-v1.json"
        regenerated = _snapshot_bytes(document)
        if not committed_path.is_file():
            mismatches.append(f"{case_id}: no committed snapshot at {committed_path}")
            continue
        committed = committed_path.read_bytes()
        if committed != regenerated:
            mismatches.append(
                f"{case_id}: regenerated scores-v1.json differs from the committed snapshot "
                f"({len(regenerated)} vs {len(committed)} bytes)"
            )
    return mismatches


def synthetic_discrimination() -> list[str]:
    """For every case, assert its synthetic_baseline gate passes and its
    synthetic_adversarial gate fails. Returns a list of failure descriptions
    (empty means every case discriminates as designed). This proves the
    aggregator's arithmetic, not any real agent's behavior."""
    failures: list[str] = []
    for case_id, document in score_all_cases().items():
        by_id = {candidate["candidate_id"]: candidate for candidate in document["candidates"]}
        baseline = by_id.get("synthetic_baseline")
        adversarial = by_id.get("synthetic_adversarial")
        if baseline is None or adversarial is None:
            failures.append(f"{case_id}: missing synthetic baseline/adversarial candidate")
            continue
        if baseline["gate"]["pass"] is not True:
            failures.append(
                f"{case_id}: synthetic_baseline did not pass the gate: {baseline['gate']}"
            )
        if adversarial["gate"]["pass"] is not False:
            failures.append(
                f"{case_id}: synthetic_adversarial unexpectedly passed the gate: "
                f"{adversarial['gate']}"
            )
    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_quality.runner")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("suite", help="score every case and print the results")
    commands.add_parser(
        "publish", help="write docs/benchmarks/agent-quality snapshots (manual only)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "suite":
        scores = score_all_cases()
        print(json.dumps(scores, indent=2, sort_keys=True))
        return 0
    if args.command == "publish":
        publish()
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
