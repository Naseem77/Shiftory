#!/usr/bin/env python3
"""Required-CI entry point for the agent-quality benchmark layer.

Mandatory CI only ever calls this script, and only for structural/reproducibility
properties -- never for a real captured agent's quality score (see
`benchmarks/agent_quality/runner.py`'s module docstring and Delta 7 in
`docs/agent-quality-benchmark-methodology.md`). Three checks:

- ``validate``: every committed case/rubric/candidate-evaluation/agent-run JSON
  document is schema-valid and internally consistent (excerpt anchors,
  audit-coverage exhaustiveness, invalid-candidate exclusivity); every fixture
  reconstructs to its pinned commit SHAs and diff inventory.
- ``regenerate-and-diff``: every case's scores are regenerated into a temporary
  directory and byte-compared against the committed
  ``docs/benchmarks/agent-quality/<case>/scores-v1.json`` snapshot. This script
  never writes into that directory itself -- only the separate, manual
  ``python -m benchmarks.agent_quality.runner publish`` command does.
- ``synthetic-discrimination``: the aggregator's arithmetic gives every case's
  claim-perfect synthetic baseline a passing gate and its deliberately
  defective synthetic adversarial a failing gate. This is a scorer-arithmetic
  property test, not evidence about any real agent.

Run with no arguments to execute all three in sequence (used by CI).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.agent_quality import fixtures as fx  # noqa: E402
from benchmarks.agent_quality import runner  # noqa: E402
from benchmarks.agent_quality import validation as v  # noqa: E402


def run_validate() -> list[str]:
    errors: list[str] = []
    for case_id in runner.case_ids():
        try:
            runner.load_case_and_rubric(case_id, runner.CASES_DIR, runner.AUDITOR_DIR)
        except v.AgentQualityError as error:
            errors.append(f"{case_id}: {error}")
            continue

        case_dir = runner.CASES_DIR / case_id
        try:
            fx.reconstruct_fixture(
                case_dir, Path(tempfile.mkdtemp(prefix=f"validate-{case_id}-")), case_id
            )
        except (v.AgentQualityError, RuntimeError, OSError) as error:
            errors.append(f"{case_id}: fixture reconstruction failed: {error}")
            continue

        candidates = [*runner.synthetic_candidates(case_dir), *runner.captured_candidates(case_dir)]
        for candidate_id, _kind, _directory in candidates:
            evaluation_path = runner.AUDITOR_DIR / case_id / "evaluations" / f"{candidate_id}.json"
            if not evaluation_path.is_file():
                errors.append(f"{case_id}/{candidate_id}: missing evaluation record")
                continue
            evaluation = v.load_json_strict(evaluation_path)
            try:
                v.validate_against_schema(evaluation, "candidate-evaluation-v1")
                v.check_invalid_candidate_exclusivity(evaluation)
            except v.AgentQualityError as error:
                errors.append(f"{case_id}/{candidate_id}: {error}")
    return errors


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = argv or ["validate", "regenerate-and-diff", "synthetic-discrimination"]

    all_failures: list[str] = []
    for command in commands:
        print(f"== agent_quality_benchmark: {command} ==")
        if command == "validate":
            failures = run_validate()
        elif command == "regenerate-and-diff":
            failures = runner.regenerate_and_diff()
        elif command == "synthetic-discrimination":
            failures = runner.synthetic_discrimination()
        else:
            print(f"unknown command: {command}", file=sys.stderr)
            return 2
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}")
            all_failures.extend(failures)
        else:
            print("ok")

    if all_failures:
        print(f"\n{len(all_failures)} failure(s)", file=sys.stderr)
        return 1
    print("\nall agent-quality benchmark checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
