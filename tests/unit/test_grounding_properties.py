"""Mutation and determinism properties of the grounding engine."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from typing import Any

import pytest

from shiftory.errors import ValidationError
from shiftory.explain import grounding, validator
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


def sliced_manifest(line_ids: list[str], items: int, claims: int, support: str) -> dict[str, Any]:
    """`items` items owning disjoint slices, each citing the same coarse support."""
    per_item = len(line_ids) // items
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "Many values are added.",
        "items": [
            {
                "id": f"slice-{index}",
                "kind": "behavioral",
                "title": f"Slice {index}",
                "before": None,
                "after": "Values are added.",
                "absence": "before",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": f"claim-{number}",
                            "type": "addition",
                            "support_level": "verified",
                            "support": [support],
                            "literal": f"value_{index * per_item + (number % per_item)} = 1",
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


def counted_validation(
    packet: dict[str, Any], manifest: dict[str, Any], monkeypatch: Any
) -> tuple[Any, int, int]:
    """Validate while counting coarse binding work and residual candidates."""
    bind_work = 0
    residual_work = 0
    original_bound = grounding._is_bound
    original_residual = grounding._residual_lines

    def counting_bound(support: Any, scope: Any, index: Any) -> Any:
        nonlocal bind_work
        bind_work += (
            len(scope.owned_lines)
            if support.kind in {"hunk", "unit"}
            else max(len(support.line_ids), 1)
        )
        return original_bound(support, scope, index)

    def counting_residual(regions: Any, index: Any, other: str, literal: str) -> Any:
        nonlocal residual_work
        files = {
            index.spans[region.span_id].file_index
            for region in regions
            if region.span_id in index.spans
        }
        residual_work += sum(
            len(index.lines_by_file_side.get((file_index, other), ())) for file_index in files
        )
        return original_residual(regions, index, other, literal)

    monkeypatch.setattr(grounding, "_is_bound", counting_bound)
    monkeypatch.setattr(grounding, "_residual_lines", counting_residual)
    result = validate_explanation(packet, manifest, require_grounding=True)
    return result, bind_work, residual_work


@pytest.mark.parametrize(("spans", "items"), [(1000, 5), (2000, 10), (4000, 20)])
def test_many_items_and_claims_stay_bounded(spans: int, items: int, monkeypatch: Any) -> None:
    """Work must scale with the comparison, not with items times claims."""
    claims = 32
    packet, line_ids = wide_packet(spans)
    manifest = sliced_manifest(line_ids, items, claims, "u")

    result, bind_work, residual_work = counted_validation(packet, manifest, monkeypatch)

    assert result.grounding is not None
    assert result.grounding.claim_total == items * claims
    # One coarse binding decision per item, each reading only that item's lines.
    assert bind_work == spans
    # No before-side changed lines exist, so no residual candidate is inspected.
    assert residual_work == 0


def test_one_item_per_changed_line_stays_bounded(monkeypatch: Any) -> None:
    spans, claims = 400, 32
    packet, line_ids = wide_packet(spans)
    manifest = sliced_manifest(line_ids, spans, claims, "u")

    result, bind_work, residual_work = counted_validation(packet, manifest, monkeypatch)

    assert result.grounding is not None
    assert result.grounding.claim_total == spans * claims
    assert bind_work == spans
    assert residual_work == 0


def test_residual_checks_read_only_opposite_side_candidates(monkeypatch: Any) -> None:
    """A real deletion candidate set is inspected; unrelated files are not."""
    packet, line_ids = wide_packet(6)
    packet["files"].append(
        {
            "old_path": "other.py",
            "new_path": "other.py",
            "units": [{"id": "u2", "kind": "text", "hunk_ids": ["h2"], "metadata": {}}],
            "hunks": [
                {
                    "id": "h2",
                    "span_ids": ["s100"],
                    "lines": [{"id": "l100", "side": "before", "content": "    value_0 = 1"}],
                }
            ],
            "spans": [
                {
                    "id": "s100",
                    "side": "before",
                    "start_line": 1,
                    "end_line": 1,
                    "line_ids": ["l100"],
                    "replacement_span_id": None,
                }
            ],
            "citations": [],
        }
    )
    manifest = sliced_manifest(line_ids, 1, 1, "u")
    manifest["items"].append(
        {
            "id": "other",
            "kind": "behavioral",
            "title": "Other",
            "before": "The other module set the value.",
            "after": None,
            "absence": "after",
            "confidence": "extracted",
            "citations": [],
            "grounding": {
                "claims": [
                    {
                        "id": "gone",
                        "type": "deletion",
                        "support_level": "verified",
                        "support": ["s100"],
                        "literal": "value_0 = 1",
                    }
                ]
            },
        }
    )
    manifest["coverage_owners"].append({"evidence_id": "l100", "owner_id": "other"})

    result, _, residual_work = counted_validation(packet, manifest, monkeypatch)

    assert result.grounding is not None
    # Only `other.py` has an after side to inspect for the deletion, and it has none;
    # the addition claim in `wide.py` finds no before-side candidates either.
    assert residual_work == 0


def test_repeated_policy_values_scan_the_source_corpus_once() -> None:
    """A flagged value is looked up once per validation, however many claims use it."""
    packet, line_ids = wide_packet(8)
    manifest = sliced_manifest(line_ids, 1, 4, "u")
    for claim in manifest["items"][0]["grounding"]["claims"]:
        claim.update(
            type="text_absence",
            side="after",
            literal="I recommend reverting this change.",
        )
        claim.pop("literal", None)
        claim["literal"] = "I recommend reverting this change."
    policy = validator._ClaimTextPolicy(validator._source_corpus(packet))
    errors: list[dict[str, Any]] = []
    for claim in manifest["items"][0]["grounding"]["claims"]:
        policy.scan(str(claim["literal"]), "$.x", errors)
    assert len(errors) == 4
    assert policy.corpus_lookups == 1


def test_clean_policy_values_never_touch_the_source_corpus() -> None:
    policy = validator._ClaimTextPolicy("    value_0 = 1")
    errors: list[dict[str, Any]] = []
    for index in range(500):
        policy.scan(f"value_{index} = 1", "$.x", errors)
    assert errors == []
    assert policy.corpus_lookups == 0


def test_distinct_flagged_values_each_resolve_against_the_corpus() -> None:
    policy = validator._ClaimTextPolicy("I recommend reverting change 3.")
    errors: list[dict[str, Any]] = []
    for index in range(5):
        policy.scan(f"I recommend reverting change {index}.", "$.x", errors)
    assert len(errors) == 4
    assert policy.corpus_lookups == 5


def paired_packet(pairs: int) -> tuple[dict[str, Any], list[str]]:
    """`pairs` before lines and `pairs` after lines in one hunk."""
    lines: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    span_ids: list[str] = []
    for index in range(pairs):
        lines.append({"id": f"b{index}", "side": "before", "content": f"    old_{index} = 0"})
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
        lines.append({"id": f"a{index}", "side": "after", "content": f"    new_{index} = 1"})
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
                "old_path": "paired.py",
                "new_path": "paired.py",
                "units": [{"id": "u", "kind": "text", "hunk_ids": ["h1"], "metadata": {}}],
                "hunks": [{"id": "h1", "span_ids": span_ids, "lines": lines}],
                "spans": spans,
                "citations": [],
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }
    return packet, [line["id"] for line in lines]


def test_residual_checks_reject_absent_literals_without_reading_records(
    monkeypatch: Any,
) -> None:
    """Both sides exist, so the old scan read every opposite-side record."""
    pairs, items, claims = 400, 20, 8
    packet, _ = paired_packet(pairs)
    per_item = pairs // items
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Values change.",
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
                            "type": "addition",
                            "support_level": "verified",
                            "support": [f"sa{index * per_item + (number % per_item)}"],
                            "literal": f"new_{index * per_item + (number % per_item)} = 1",
                        }
                        for number in range(claims)
                    ]
                },
            }
            for index in range(items)
        ],
        "coverage_owners": [
            {"evidence_id": f"a{position}", "owner_id": f"slice-{position // per_item}"}
            for position in range(pairs)
        ]
        + [
            {"evidence_id": f"b{position}", "owner_id": f"slice-{position // per_item}"}
            for position in range(pairs)
        ],
    }

    inspected = 0
    original = grounding._residual_lines

    def counting(regions: Any, index: Any, other: str, literal: str) -> Any:
        nonlocal inspected
        files = {
            index.spans[region.span_id].file_index
            for region in regions
            if region.span_id in index.spans
        }
        for file_index in files:
            key = (file_index, other)
            if literal in index.text_by_file_side.get(key, ""):
                inspected += len(index.lines_by_file_side.get(key, ()))
        return original(regions, index, other, literal)

    monkeypatch.setattr(grounding, "_residual_lines", counting)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == items * claims
    # Every added literal is absent from the before side, so no record is read.
    assert inspected == 0


def test_residual_checks_still_find_a_real_move() -> None:
    packet, _ = paired_packet(50)
    packet["files"][0]["hunks"][0]["lines"][0]["content"] = "    new_0 = 1"
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Values change.",
        "items": [
            {
                "id": "solo",
                "kind": "structural",
                "title": "Solo",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "moved",
                            "type": "addition",
                            "support_level": "verified",
                            "support": ["sa0"],
                            "literal": "new_0 = 1",
                        }
                    ]
                },
            }
        ],
        "coverage_owners": [{"evidence_id": f"a{index}", "owner_id": "solo"} for index in range(50)]
        + [{"evidence_id": f"b{index}", "owner_id": "solo"} for index in range(50)],
    }
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.absence_violated"
    ]


def test_many_distinct_coarse_supports_read_the_smaller_side(monkeypatch: Any) -> None:
    """An item owning most of a comparison must not rescan itself per support."""
    spans, supports = 640, 32
    packet, line_ids = wide_packet(spans)
    per_hunk = spans // supports
    file = packet["files"][0]
    file["hunks"] = [
        {
            "id": f"h{index}",
            "span_ids": [
                f"s{position}" for position in range(index * per_hunk, (index + 1) * per_hunk)
            ],
            "lines": [
                {"id": f"l{position}", "side": "after", "content": f"    value_{position} = 1"}
                for position in range(index * per_hunk, (index + 1) * per_hunk)
            ],
        }
        for index in range(supports)
    ]
    file["units"][0]["hunk_ids"] = [f"h{index}" for index in range(supports)]

    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Values are added.",
        "items": [
            {
                "id": "solo",
                "kind": "structural",
                "title": "Solo",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": f"claim-{index}",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": [f"h{index}"],
                            "side": "after",
                            "literal": f"value_{index * per_hunk} = 1",
                        }
                        for index in range(supports)
                    ]
                },
            }
        ],
        "coverage_owners": [{"evidence_id": line_id, "owner_id": "solo"} for line_id in line_ids],
    }

    reads = 0
    original = grounding._is_bound

    def counting(support: Any, scope: Any, index: Any) -> Any:
        nonlocal reads
        reads += (
            min(len(scope.owned_lines), len(support.line_set))
            if support.kind in {"hunk", "unit"}
            else max(len(support.line_ids), 1)
        )
        return original(support, scope, index)

    monkeypatch.setattr(grounding, "_is_bound", counting)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == supports
    # Each hunk is read at most once, never the item's whole owned set.
    assert reads <= spans


def test_shared_support_binding_reuses_the_item_cache(monkeypatch: Any) -> None:
    """A shared entry must not re-decide binding for every citing claim."""
    packet, line_ids = wide_packet(20)
    claims = 8
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
                            "id": f"claim-{number}",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": ["s0"],
                            "shared_support": [
                                {
                                    "evidence_id": "s19",
                                    "owner_id": "tail",
                                    "reason": "The tail value completes the same group.",
                                }
                            ],
                            "side": "after",
                            "literal": "value_19 = 1",
                        }
                        for number in range(claims)
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
                            "id": "own",
                            "type": "addition",
                            "support_level": "verified",
                            "support": ["s19"],
                            "literal": "value_19 = 1",
                        }
                    ]
                },
            },
        ],
        "coverage_owners": [
            {"evidence_id": line_id, "owner_id": "tail" if line_id == "l19" else "head"}
            for line_id in line_ids
        ],
    }

    calls = 0
    original = grounding._is_bound

    def counting(support: Any, scope: Any, index: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(support, scope, index)

    monkeypatch.setattr(grounding, "_is_bound", counting)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == claims + 1
    # Two supports for `head` plus one for `tail`, decided once each.
    assert calls == 3


def test_a_unit_orders_its_lines_by_evidence_position() -> None:
    """Declared hunk order must not decide a coarse support's reading order."""
    packet, _ = wide_packet(2)
    file = packet["files"][0]
    file["hunks"] = [
        {"id": "h0", "span_ids": ["s0"], "lines": [file["hunks"][0]["lines"][0]]},
        {"id": "h1", "span_ids": ["s1"], "lines": [file["hunks"][0]["lines"][1]]},
    ]
    file["units"][0]["hunk_ids"] = ["h1", "h0"]
    index = grounding.index_evidence(packet)
    assert index.supports["u"].line_ids == ("l0", "l1")
    assert [region.span_id for region in index.supports["u"].regions] == ["s0", "s1"]


def shared_prefix_packet(pairs: int) -> dict[str, Any]:
    """Both sides share an eight-character prefix, as ordinary source does."""
    lines: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    span_ids: list[str] = []
    for index in range(pairs):
        lines.append(
            {
                "id": f"b{index}",
                "side": "before",
                "content": f"        self.logger.debug('legacy %s', ctx_{index})",
            }
        )
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
        lines.append(
            {
                "id": f"a{index}",
                "side": "after",
                "content": f"        self.logger.info('current %s', ctx_{index})",
            }
        )
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
    return {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "svc.py",
                "new_path": "svc.py",
                "units": [{"id": "u", "kind": "text", "hunk_ids": ["h1"], "metadata": {}}],
                "hunks": [{"id": "h1", "span_ids": span_ids, "lines": lines}],
                "spans": spans,
                "citations": [],
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }


def shared_prefix_manifest(pairs: int, items: int, claims: int) -> dict[str, Any]:
    per_item = pairs // items
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "Logging changes.",
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
                            "type": "addition",
                            "support_level": "verified",
                            "support": [f"sa{index * per_item + (number % per_item)}"],
                            "literal": (
                                "self.logger.info('current %s', ctx_"
                                f"{index * per_item + (number % per_item)})"
                            ),
                        }
                        for number in range(claims)
                    ]
                },
            }
            for index in range(items)
        ],
        "coverage_owners": [
            {"evidence_id": f"a{position}", "owner_id": f"slice-{position // per_item}"}
            for position in range(pairs)
        ]
        + [
            {"evidence_id": f"b{position}", "owner_id": f"slice-{position // per_item}"}
            for position in range(pairs)
        ],
    }


def test_residual_filter_rejects_literals_that_share_a_prefix(monkeypatch: Any) -> None:
    """Checking every window, not just the first, is what makes the filter work."""
    pairs, items, claims = 200, 10, 8
    packet = shared_prefix_packet(pairs)
    manifest = shared_prefix_manifest(pairs, items, claims)

    rejected = 0
    scanned = 0
    original = grounding._literal_may_occur

    def counting(index: Any, key: tuple[int, str], literal: str) -> bool:
        nonlocal rejected, scanned
        outcome = original(index, key, literal)
        if outcome:
            scanned += len(index.text_by_file_side.get(key, ""))
        else:
            rejected += 1
        return outcome

    monkeypatch.setattr(grounding, "_literal_may_occur", counting)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == items * claims
    # Every literal shares its first window with the before side, so a first-window
    # filter would reject none of them and scan the whole side for each claim.
    assert rejected == items * claims
    assert scanned == 0


def test_residual_answers_are_remembered_within_one_validation(monkeypatch: Any) -> None:
    pairs, items, claims = 40, 2, 8
    packet = shared_prefix_packet(pairs)
    manifest = shared_prefix_manifest(pairs, items, claims)
    for item in manifest["items"]:
        for claim in item["grounding"]["claims"]:
            claim["literal"] = "self.logger.info('current %s', ctx_0)"
    manifest["items"] = manifest["items"][:1]
    manifest["coverage_owners"] = [
        {"evidence_id": entry["evidence_id"], "owner_id": "slice-0"}
        for entry in manifest["coverage_owners"]
    ]
    for claim in manifest["items"][0]["grounding"]["claims"]:
        claim["support"] = ["sa0"]

    probes = 0
    original = grounding._literal_may_occur

    def counting(index: Any, key: tuple[int, str], literal: str) -> bool:
        nonlocal probes
        probes += 1
        return original(index, key, literal)

    monkeypatch.setattr(grounding, "_literal_may_occur", counting)
    result = validate_explanation(packet, manifest, require_grounding=True)

    assert result.grounding is not None
    assert result.grounding.claim_total == claims
    # One distinct literal and one file side, so one probe however many claims.
    assert probes == 1


def test_grams_are_built_only_when_a_residual_check_needs_them() -> None:
    packet = shared_prefix_packet(20)
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Logging changes.",
        "items": [
            {
                "id": "solo",
                "kind": "structural",
                "title": "Solo",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "present",
                            "type": "text_presence",
                            "support_level": "verified",
                            "support": ["sa0"],
                            "side": "after",
                            "literal": "self.logger.info",
                        }
                    ]
                },
            }
        ],
        "coverage_owners": [{"evidence_id": f"a{index}", "owner_id": "solo"} for index in range(20)]
        + [{"evidence_id": f"b{index}", "owner_id": "solo"} for index in range(20)],
    }
    index = grounding.index_evidence(packet)
    assert index.grams_by_file_side == {}
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.claim_total == 1


def repeated_line_packet(pairs: int) -> dict[str, Any]:
    """Every changed line is identical, as in a lockfile diff."""
    lines: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    span_ids: list[str] = []
    for side, prefix in (("before", "b"), ("after", "a")):
        for index in range(pairs):
            lines.append({"id": f"{prefix}{index}", "side": side, "content": '      "dev": true,'})
            spans.append(
                {
                    "id": f"s{prefix}{index}",
                    "side": side,
                    "start_line": index + 1,
                    "end_line": index + 1,
                    "line_ids": [f"{prefix}{index}"],
                    "replacement_span_id": None,
                }
            )
            span_ids.append(f"s{prefix}{index}")
    return {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "lock.json",
                "new_path": "lock.json",
                "units": [{"id": "u", "kind": "text", "hunk_ids": ["h1"], "metadata": {}}],
                "hunks": [{"id": "h1", "span_ids": span_ids, "lines": lines}],
                "spans": spans,
                "citations": [],
            }
        ],
        "graph": {"status": "disabled", "facts": []},
    }


def test_a_rejected_residual_names_a_bounded_number_of_lines() -> None:
    """A file of identical lines must not produce a diagnostic per line."""
    pairs = 200
    packet = repeated_line_packet(pairs)
    manifest: dict[str, Any] = {
        "schema": "shiftory.explanation/v1",
        "summary": "Lock entries change.",
        "items": [
            {
                "id": "solo",
                "kind": "structural",
                "title": "Solo",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "added",
                            "type": "addition",
                            "support_level": "verified",
                            "support": ["sa0"],
                            "literal": '"dev": true,',
                        }
                    ]
                },
            }
        ],
        "coverage_owners": [
            {"evidence_id": f"a{index}", "owner_id": "solo"} for index in range(pairs)
        ]
        + [{"evidence_id": f"b{index}", "owner_id": "solo"} for index in range(pairs)],
    }
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    entries = error.value.details["errors"]
    assert [entry["code"] for entry in entries] == ["grounding.absence_violated"]
    message = entries[0]["message"]
    assert message.endswith("so it is moved or retained rather than added")
    assert message.count("b") >= 1
    assert " and more)" in message
    # Five named ids plus the marker, never one per matching line.
    assert len(message) < 300


def test_residual_collection_stops_at_the_reporting_limit() -> None:
    packet = repeated_line_packet(50)
    index = grounding.index_evidence(packet)
    regions = index.supports["sa0"].regions
    matches = grounding._residual_lines(regions, index, "before", '"dev": true,')
    assert len(matches) == grounding.RESIDUAL_REPORT_LIMIT + 1


def test_a_small_residual_is_reported_in_full() -> None:
    packet = repeated_line_packet(2)
    index = grounding.index_evidence(packet)
    regions = index.supports["sa0"].regions
    matches = grounding._residual_lines(regions, index, "before", '"dev": true,')
    assert matches == ["b0", "b1"]
