"""Mutation and determinism properties of the grounding engine."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from typing import Any

import pytest

from shiftory.errors import ValidationError
from shiftory.explain import grounding
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


def wide_packet(spans: int) -> tuple[dict[str, Any], list[str]]:
    """One hunk holding `spans` single-line spans, the pathological coarse shape."""
    lines, span_records, citations, span_ids = [], [], [], []
    for index in range(spans):
        line_id, span_id = f"l{index}", f"s{index}"
        span_ids.append(span_id)
        lines.append({"id": line_id, "side": "after", "content": f"    value_{index} = 1"})
        span_records.append(
            {
                "id": span_id,
                "side": "after",
                "start_line": index + 1,
                "end_line": index + 1,
                "line_ids": [line_id],
                "replacement_span_id": None,
            }
        )
        citations.append(
            {
                "id": f"c{index}",
                "path": "wide.py",
                "side": "after",
                "start_line": index + 1,
                "end_line": index + 1,
                "text": f"    value_{index} = 1",
                "omitted": False,
            }
        )
    packet = {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "wide.py",
                "new_path": "wide.py",
                "units": [{"id": "u", "kind": "text", "hunk_ids": ["h"], "metadata": {}}],
                "hunks": [{"id": "h", "span_ids": span_ids, "lines": lines}],
                "spans": span_records,
                "citations": citations,
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }
    return packet, [line["id"] for line in lines]


def wide_manifest(line_ids: list[str], claims: int, support: str) -> dict[str, Any]:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "Many values are added.",
        "items": [
            {
                "id": "bulk",
                "kind": "behavioral",
                "title": "Bulk addition",
                "before": None,
                "after": "Many values are added.",
                "absence": "before",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": f"claim-{index}",
                            "type": "addition",
                            "support_level": "verified",
                            "support": [support],
                            "literal": f"value_{index} = 1",
                        }
                        for index in range(claims)
                    ]
                },
            }
        ],
        "coverage_owners": [{"evidence_id": line_id, "owner_id": "bulk"} for line_id in line_ids],
    }


def test_coarse_support_resolution_stays_near_linear(monkeypatch: Any) -> None:
    """Resolution work must not grow with claims times spans."""
    spans, claims = 2_000, 32
    packet, line_ids = wide_packet(spans)
    manifest = wide_manifest(line_ids, claims, "h")

    regions_built = 0
    original_region = grounding.Region

    class CountingRegion(original_region):  # type: ignore[misc, valid-type]
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            nonlocal regions_built
            regions_built += 1
            return original_region(*args, **kwargs)

    resolved = 0
    original_resolve = grounding._resolve_one

    def counting_resolve(value: str, index: Any) -> Any:
        nonlocal resolved
        resolved += 1
        return original_resolve(value, index)

    monkeypatch.setattr(grounding, "Region", CountingRegion)
    monkeypatch.setattr(grounding, "_resolve_one", counting_resolve)

    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == claims
    # One region per span plus one per citation, built once with the index.
    assert regions_built == 2 * spans
    # One resolution per support reference, never per span.
    assert resolved == claims


def test_coarse_and_narrow_support_agree_on_the_same_claims() -> None:
    spans, claims = 200, 8
    packet, line_ids = wide_packet(spans)
    coarse = validate_explanation(
        packet, wide_manifest(line_ids, claims, "h"), require_grounding=True
    )
    narrow = validate_explanation(
        packet, wide_manifest(line_ids, claims, "u"), require_grounding=True
    )
    assert coarse.grounding is not None
    assert narrow.grounding is not None
    assert coarse.grounding.to_dict() == narrow.grounding.to_dict()


def test_coarse_support_narrows_to_the_owning_item() -> None:
    packet, line_ids = wide_packet(6)
    manifest = wide_manifest(line_ids, 1, "h")
    manifest["items"].append(
        {
            "id": "tail",
            "kind": "structural",
            "title": "Trailing value",
            "confidence": "extracted",
            "citations": [],
            "grounding": {
                "claims": [
                    {
                        "id": "tail-value",
                        "type": "addition",
                        "support_level": "verified",
                        "support": ["s5"],
                        "literal": "value_5 = 1",
                    }
                ]
            },
        }
    )
    manifest["coverage_owners"] = [
        {"evidence_id": line_id, "owner_id": "tail" if line_id == "l5" else "bulk"}
        for line_id in line_ids
    ]
    manifest["items"][0]["grounding"]["claims"][0]["literal"] = "value_5 = 1"
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.operand_missing"
    ]


def linked_pair_packet(pairs: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """One hunk holding `pairs` before spans and `pairs` after spans."""
    lines: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    span_ids: list[str] = []
    for index in range(pairs):
        lines.append({"id": f"b{index}", "side": "before", "content": f"old_{index}"})
        spans.append(
            {
                "id": f"sb{index}",
                "side": "before",
                "start_line": index + 1,
                "end_line": index + 1,
                "line_ids": [f"b{index}"],
                "replacement_span_id": None,
            }
        )
        span_ids.append(f"sb{index}")
    for index in range(pairs):
        lines.append({"id": f"a{index}", "side": "after", "content": f"new_{index}"})
        spans.append(
            {
                "id": f"sa{index}",
                "side": "after",
                "start_line": index + 1,
                "end_line": index + 1,
                "line_ids": [f"a{index}"],
                "replacement_span_id": None,
            }
        )
        span_ids.append(f"sa{index}")
    packet = {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "pairs.py",
                "new_path": "pairs.py",
                "units": [{"id": "u", "kind": "text", "hunk_ids": ["h1"], "metadata": {}}],
                "hunks": [{"id": "h1", "span_ids": span_ids, "lines": lines}],
                "spans": spans,
                "citations": [],
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }
    value = {
        "schema": "shiftory.explanation/v1",
        "summary": "Values change.",
        "items": [
            {
                "id": "values",
                "kind": "behavioral",
                "title": "Values",
                "before": "The old values were used.",
                "after": "The new values are used.",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "vc",
                            "type": "value_change",
                            "support_level": "verified",
                            "support": ["h1"],
                            "before_literal": "old_0",
                            "after_literal": "new_0",
                        }
                    ]
                },
            }
        ],
        "coverage_owners": [{"evidence_id": line["id"], "owner_id": "values"} for line in lines],
    }
    return packet, value


def test_value_change_does_not_enumerate_every_region_pair(monkeypatch: Any) -> None:
    """The replacement link is a function, so candidates are looked up, not scanned."""
    pairs = 400
    packet, manifest = linked_pair_packet(pairs)
    joins = 0
    original = grounding.Region.joined

    def counting_join(self: Any) -> str:
        nonlocal joins
        joins += 1
        return original(self)

    monkeypatch.setattr(grounding.Region, "joined", counting_join)
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.replacement_link_missing"
    ]
    # Two obligation scans over both sides, never a before-by-after product.
    assert joins <= 4 * pairs


def test_value_change_still_finds_a_linked_pair_through_coarse_support() -> None:
    packet, manifest = linked_pair_packet(50)
    packet["files"][0]["spans"][0]["replacement_span_id"] = "sa0"
    packet["files"][0]["spans"][50]["replacement_span_id"] = "sb0"
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.outcomes[0].proof == (
        "replacement span sb0 -> sa0 changes 'old_0' to 'new_0'"
    )


def test_coarse_narrowing_is_computed_once_per_item_and_support(monkeypatch: Any) -> None:
    """Narrowing depends on the item, not the claim, so claims must not repeat it."""
    spans, claims, items = 400, 16, 4
    packet, line_ids = wide_packet(spans)
    per_item = spans // items
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Many values are added.",
        "items": [
            {
                "id": f"slice-{index}",
                "kind": "structural",
                "title": f"Slice {index}",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": f"claim-{number}",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": ["u"],
                            "side": "after",
                            "literal": f"value_{index * per_item + number} = 1",
                        }
                        for number in range(claims)
                    ]
                },
            }
            for index in range(items)
        ],
        "coverage_owners": [
            {"evidence_id": line_id, "owner_id": f"slice-{position // per_item}"}
            for position, line_id in enumerate(line_ids)
        ],
    }

    narrowings = 0
    original = grounding._narrowed

    def counting_narrow(support: Any, scope: Any, index: Any) -> Any:
        nonlocal narrowings
        narrowings += 1
        return original(support, scope, index)

    monkeypatch.setattr(grounding, "_narrowed", counting_narrow)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == claims * items
    # One narrowing per item and distinct support id, never one per claim.
    assert narrowings == items


def test_narrowing_cache_does_not_leak_between_items() -> None:
    """Each item must narrow the same coarse support against its own lines."""
    packet, line_ids = wide_packet(4)
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Values are added.",
        "items": [
            {
                "id": "head",
                "kind": "structural",
                "title": "Head",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "head-value",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": ["u"],
                            "side": "after",
                            "literal": "value_3 = 1",
                        }
                    ]
                },
            },
            {
                "id": "tail",
                "kind": "structural",
                "title": "Tail",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "tail-value",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": ["u"],
                            "side": "after",
                            "literal": "value_3 = 1",
                        }
                    ]
                },
            },
        ],
        "coverage_owners": [
            {"evidence_id": line_id, "owner_id": "head" if position < 2 else "tail"}
            for position, line_id in enumerate(line_ids)
        ],
    }
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.operand_missing"
    ]
