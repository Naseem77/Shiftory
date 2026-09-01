"""Mutation and determinism properties of the grounding engine."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from typing import Any

import pytest

from shiftory.errors import ValidationError
from shiftory.explain.validator import validate_explanation
from shiftory.models.json import canonical_json
from shiftory.render.report import build_report


def packet() -> dict[str, Any]:
    return {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "service.py",
                "new_path": "service.py",
                "status": "modified",
                "units": [{"id": "unit", "kind": "text", "hunk_ids": ["hunk"], "metadata": {}}],
                "hunks": [
                    {
                        "id": "hunk",
                        "span_ids": ["before-span", "after-span"],
                        "lines": [
                            {"id": "old-line", "side": "before", "content": "    limit = 10"},
                            {"id": "new-line", "side": "after", "content": "    limit = 25"},
                            {"id": "extra-line", "side": "after", "content": "    audit(limit)"},
                        ],
                    }
                ],
                "spans": [
                    {
                        "id": "before-span",
                        "side": "before",
                        "start_line": 4,
                        "end_line": 4,
                        "line_ids": ["old-line"],
                        "replacement_span_id": "after-span",
                    },
                    {
                        "id": "after-span",
                        "side": "after",
                        "start_line": 4,
                        "end_line": 5,
                        "line_ids": ["new-line", "extra-line"],
                        "replacement_span_id": "before-span",
                    },
                ],
                "citations": [
                    {
                        "id": "before-citation",
                        "path": "service.py",
                        "side": "before",
                        "start_line": 4,
                        "end_line": 4,
                        "text": "    limit = 10",
                        "omitted": False,
                    },
                    {
                        "id": "after-citation",
                        "path": "service.py",
                        "side": "after",
                        "start_line": 4,
                        "end_line": 5,
                        "text": "    limit = 25\n    audit(limit)",
                        "omitted": False,
                    },
                ],
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }


def manifest() -> dict[str, Any]:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "The limit changes and the new value is audited.",
        "items": [
            {
                "id": "limit",
                "kind": "behavioral",
                "title": "Limit selection",
                "before": "The service allowed 10.",
                "after": "The service allows 25 and audits the value.",
                "confidence": "extracted",
                "citations": ["before-citation", "after-citation"],
                "grounding": {
                    "claims": [
                        {
                            "id": "replacement",
                            "type": "value_change",
                            "support_level": "verified",
                            "support": ["before-span", "after-span"],
                            "before_literal": "limit = 10",
                            "after_literal": "limit = 25",
                        },
                        {
                            "id": "order",
                            "type": "source_order",
                            "support_level": "verified",
                            "support": ["after-citation"],
                            "side": "after",
                            "first": "limit = 25",
                            "second": "audit(limit)",
                        },
                    ]
                },
            }
        ],
        "coverage_owners": [
            {"evidence_id": "old-line", "owner_id": "limit"},
            {"evidence_id": "new-line", "owner_id": "limit"},
            {"evidence_id": "extra-line", "owner_id": "limit"},
        ],
    }


def claims(value: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = value["items"][0]["grounding"]["claims"]
    return result


def test_the_baseline_manifest_is_fully_verified() -> None:
    result = validate_explanation(packet(), manifest(), require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == 2


@pytest.mark.parametrize("claim_index", [0, 1])
@pytest.mark.parametrize("support_index", [0, 1])
def test_removing_any_required_support_invalidates_the_claim(
    claim_index: int, support_index: int
) -> None:
    value = manifest()
    support = claims(value)[claim_index]["support"]
    if support_index >= len(support):
        pytest.skip("This claim binds fewer support references")
    del support[support_index]
    with pytest.raises(ValidationError):
        validate_explanation(packet(), value, require_grounding=True)


def test_swapping_value_change_operands_invalidates_the_claim() -> None:
    value = manifest()
    claim = claims(value)[0]
    claim["before_literal"], claim["after_literal"] = (
        claim["after_literal"],
        claim["before_literal"],
    )
    with pytest.raises(ValidationError):
        validate_explanation(packet(), value, require_grounding=True)


def test_swapping_order_operands_invalidates_the_claim() -> None:
    value = manifest()
    claim = claims(value)[1]
    claim["first"], claim["second"] = claim["second"], claim["first"]
    with pytest.raises(ValidationError):
        validate_explanation(packet(), value, require_grounding=True)


def test_swapping_the_declared_side_invalidates_the_claim() -> None:
    value = manifest()
    claims(value)[1]["side"] = "before"
    with pytest.raises(ValidationError):
        validate_explanation(packet(), value, require_grounding=True)


def test_swapping_support_for_the_other_side_invalidates_the_claim() -> None:
    value = manifest()
    claims(value)[1]["support"] = ["before-citation"]
    with pytest.raises(ValidationError):
        validate_explanation(packet(), value, require_grounding=True)


def test_breaking_the_replacement_link_invalidates_a_value_change() -> None:
    evidence = packet()
    for span in evidence["files"][0]["spans"]:
        span["replacement_span_id"] = None
    with pytest.raises(ValidationError) as error:
        validate_explanation(evidence, manifest(), require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.replacement_link_missing"
    ]


def test_editing_the_cited_source_invalidates_the_proven_order() -> None:
    evidence = packet()
    citation = evidence["files"][0]["citations"][1]
    citation["text"] = "    audit(limit)\n    limit = 25"
    with pytest.raises(ValidationError) as error:
        validate_explanation(evidence, manifest(), require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.order_unproven"
    ]


def test_removing_the_order_claim_leaves_a_valid_value_change() -> None:
    value = manifest()
    del claims(value)[1]
    result = validate_explanation(packet(), value, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.claim_total == 1


def test_removing_the_value_change_requires_a_replacement_before_side_claim() -> None:
    value = manifest()
    del claims(value)[0]
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet(), value, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == ["grounding.item_shape"]

    claims(value).append(
        {
            "id": "before",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["before-span"],
            "side": "before",
            "literal": "limit = 10",
        }
    )
    result = validate_explanation(packet(), value, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.claim_total == 2


def test_report_rendering_is_byte_identical_for_the_same_input() -> None:
    first = build_report(packet(), manifest(), require_grounding=True)
    second = build_report(packet(), copy.deepcopy(manifest()), require_grounding=True)
    assert canonical_json(first) == canonical_json(second)


def test_report_rendering_is_stable_across_hash_seeds() -> None:
    program = (
        "import json,sys;"
        "sys.path.insert(0, sys.argv[3]);"
        "from shiftory.models.json import canonical_json;"
        "from shiftory.render.report import build_report;"
        "evidence=json.loads(sys.argv[1]);"
        "explanation=json.loads(sys.argv[2]);"
        "sys.stdout.write("
        "canonical_json(build_report(evidence, explanation, require_grounding=True)))"
    )
    payloads = []
    for seed in ("0", "1", "12345"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                json.dumps(packet()),
                json.dumps(manifest()),
                _source_root(),
            ],
            check=True,
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        )
        payloads.append(completed.stdout)
    assert len(set(payloads)) == 1


def _source_root() -> str:
    import shiftory

    return str(__import__("pathlib").Path(shiftory.__file__).resolve().parents[1])
