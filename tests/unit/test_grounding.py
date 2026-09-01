"""Deterministic grounding predicates, binding rules, and typed diagnostics."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from shiftory.errors import ValidationError
from shiftory.explain.validator import validate_explanation
from shiftory.schemas import load_schema


def evidence() -> dict[str, Any]:
    return {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "identity"},
        "files": [
            {
                "old_path": "app.py",
                "new_path": "app.py",
                "status": "modified",
                "units": [{"id": "u1", "kind": "text", "hunk_ids": ["h1"], "metadata": {}}],
                "hunks": [
                    {
                        "id": "h1",
                        "span_ids": ["sb", "sa", "sa2"],
                        "lines": [
                            {"id": "lb", "side": "before", "content": "    timeout = 30"},
                            {"id": "la1", "side": "after", "content": "    timeout = 60"},
                            {"id": "la2", "side": "after", "content": "    retries = 2"},
                            {"id": "la3", "side": "after", "content": "    log('done')"},
                        ],
                    }
                ],
                "spans": [
                    {
                        "id": "sb",
                        "side": "before",
                        "start_line": 2,
                        "end_line": 2,
                        "line_ids": ["lb"],
                        "replacement_span_id": "sa",
                    },
                    {
                        "id": "sa",
                        "side": "after",
                        "start_line": 2,
                        "end_line": 3,
                        "line_ids": ["la1", "la2"],
                        "replacement_span_id": "sb",
                    },
                    {
                        "id": "sa2",
                        "side": "after",
                        "start_line": 7,
                        "end_line": 7,
                        "line_ids": ["la3"],
                        "replacement_span_id": None,
                    },
                ],
                "citations": [
                    {
                        "id": "cb",
                        "path": "app.py",
                        "side": "before",
                        "start_line": 2,
                        "end_line": 2,
                        "text": "    timeout = 30",
                        "omitted": False,
                    },
                    {
                        "id": "ca",
                        "path": "app.py",
                        "side": "after",
                        "start_line": 2,
                        "end_line": 3,
                        "text": "    timeout = 60\n    retries = 2",
                        "omitted": False,
                    },
                    {
                        "id": "ca2",
                        "path": "app.py",
                        "side": "after",
                        "start_line": 7,
                        "end_line": 7,
                        "text": "    log('done')",
                        "omitted": False,
                    },
                ],
            },
            {
                "old_path": "other.py",
                "new_path": "other.py",
                "status": "modified",
                "units": [{"id": "u2", "kind": "text", "hunk_ids": ["h2"], "metadata": {}}],
                "hunks": [
                    {
                        "id": "h2",
                        "span_ids": ["sc"],
                        "lines": [{"id": "lc", "side": "after", "content": "    timeout = 30"}],
                    }
                ],
                "spans": [
                    {
                        "id": "sc",
                        "side": "after",
                        "start_line": 1,
                        "end_line": 1,
                        "line_ids": ["lc"],
                        "replacement_span_id": None,
                    }
                ],
                "citations": [
                    {
                        "id": "cc",
                        "path": "other.py",
                        "side": "after",
                        "start_line": 1,
                        "end_line": 1,
                        "text": "    timeout = 30",
                        "omitted": False,
                    }
                ],
            },
            {
                "old_path": "old.py",
                "new_path": "new.py",
                "status": "renamed",
                "units": [
                    {
                        "id": "u3",
                        "kind": "rename",
                        "hunk_ids": [],
                        "metadata": {"old_path": "old.py", "new_path": "new.py"},
                    }
                ],
                "hunks": [],
                "spans": [],
                "citations": [],
            },
        ],
        "graph": {
            "status": "available",
            "facts": [
                {
                    "id": "fa",
                    "kind": "caller",
                    "side": "after",
                    "path": "app.py",
                    "line": 2,
                    "symbol": "connect",
                    "target": "retry",
                    "confidence": "extracted",
                    "provenance": "graphora:tree-sitter",
                },
                {
                    "id": "fb",
                    "kind": "caller",
                    "side": "before",
                    "path": "app.py",
                    "line": 2,
                    "symbol": "connect",
                    "target": "retry",
                    "confidence": "extracted",
                    "provenance": "graphora:tree-sitter",
                },
                {
                    "id": "fi",
                    "kind": "definition",
                    "side": "after",
                    "path": "app.py",
                    "line": 2,
                    "symbol": "connect",
                    "target": None,
                    "confidence": "inferred",
                    "provenance": "graphora:regex",
                },
                {
                    "id": "fo",
                    "kind": "caller",
                    "side": "after",
                    "path": "other.py",
                    "line": 1,
                    "symbol": "connect",
                    "target": "retry",
                    "confidence": "extracted",
                    "provenance": "graphora:tree-sitter",
                },
            ],
        },
    }


def value_change_claim(**overrides: Any) -> dict[str, Any]:
    claim = {
        "id": "value",
        "type": "value_change",
        "support_level": "verified",
        "support": ["sb", "sa"],
        "before_literal": "timeout = 30",
        "after_literal": "timeout = 60",
    }
    claim.update(overrides)
    return claim


def explanation(*claims: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "The timeout and retry configuration changes.",
        "items": [
            {
                "id": "main",
                "kind": "behavioral",
                "title": "Timeout selection",
                "before": "The client waited 30 units.",
                "after": "The client waits 60 units and retries twice.",
                "confidence": "extracted",
                "citations": ["cb", "ca"],
                "grounding": {"claims": list(claims) or [value_change_claim()]},
            },
            {
                "id": "second",
                "kind": "structural",
                "title": "Completion logging",
                "confidence": "extracted",
                "citations": ["ca2"],
                "grounding": {
                    "claims": [
                        {
                            "id": "log",
                            "type": "addition",
                            "support_level": "verified",
                            "support": ["sa2"],
                            "literal": "log('done')",
                        }
                    ]
                },
            },
            {
                "id": "other",
                "kind": "structural",
                "title": "Second module default",
                "confidence": "extracted",
                "citations": ["cc"],
                "grounding": {
                    "claims": [
                        {
                            "id": "other-default",
                            "type": "addition",
                            "support_level": "verified",
                            "support": ["sc"],
                            "literal": "timeout = 30",
                        }
                    ]
                },
            },
            {
                "id": "renamed",
                "kind": "structural",
                "title": "Module rename",
                "confidence": "extracted",
                "citations": [],
                "grounding": {
                    "claims": [
                        {
                            "id": "rename",
                            "type": "non_text_change",
                            "support_level": "verified",
                            "support": ["u3"],
                            "unit_kind": "rename",
                            "metadata": {"old_path": "old.py", "new_path": "new.py"},
                        }
                    ]
                },
            },
        ],
        "coverage_owners": [
            {"evidence_id": "lb", "owner_id": "main"},
            {"evidence_id": "la1", "owner_id": "main"},
            {"evidence_id": "la2", "owner_id": "main"},
            {"evidence_id": "la3", "owner_id": "second"},
            {"evidence_id": "lc", "owner_id": "other"},
            {"evidence_id": "u3", "owner_id": "renamed"},
        ],
    }


def codes(manifest: dict[str, Any], *, require: bool = True) -> list[str]:
    with pytest.raises(ValidationError) as error:
        validate_explanation(evidence(), manifest, require_grounding=require)
    return [entry.get("code", "-") for entry in error.value.details["errors"]]


def whole_file_explanation(*claims: dict[str, Any]) -> dict[str, Any]:
    """A manifest where one item owns every changed line in `app.py`."""
    manifest = explanation(*claims)
    manifest["items"] = [item for item in manifest["items"] if item["id"] != "second"]
    manifest["coverage_owners"] = [
        {"evidence_id": entry["evidence_id"], "owner_id": "main"}
        if entry["evidence_id"] == "la3"
        else entry
        for entry in manifest["coverage_owners"]
    ]
    return manifest


def accept(manifest: dict[str, Any], *, require: bool = True) -> Any:
    result = validate_explanation(evidence(), manifest, require_grounding=require)
    assert result.grounding is not None
    return result.grounding


def main_claims(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = manifest["items"][0]["grounding"]["claims"]
    return claims


def test_grounded_manifest_matches_the_published_schema() -> None:
    validator = Draft202012Validator(load_schema("explanation"))
    assert not list(validator.iter_errors(explanation()))


def test_schema_rejects_cross_type_claim_fields() -> None:
    validator = Draft202012Validator(load_schema("explanation"))
    manifest = explanation(value_change_claim(side="after"))
    assert list(validator.iter_errors(manifest))


def test_schema_requires_limits_for_unverified_support() -> None:
    validator = Draft202012Validator(load_schema("explanation"))
    manifest = explanation(value_change_claim(support_level="inferred"))
    assert list(validator.iter_errors(manifest))


def test_verified_value_change_reports_its_replacement_link() -> None:
    grounding = accept(explanation())
    assert grounding.claim_total == 4
    assert grounding.level_counts["verified"] == 4
    assert grounding.mode == "required"
    proof = grounding.outcomes[0].proof
    assert proof == "replacement span sb -> sa changes 'timeout = 30' to 'timeout = 60'"


def test_value_change_needs_a_replacement_link() -> None:
    manifest = whole_file_explanation(
        value_change_claim(support=["sb", "sa2"], after_literal="log('done')"),
    )
    assert codes(manifest) == ["grounding.replacement_link_missing"]


def test_value_change_rejects_a_literal_present_on_both_sides() -> None:
    manifest = explanation(value_change_claim(after_literal="timeout"))
    assert codes(manifest) == ["grounding.replacement_link_missing"]


def test_value_change_rejects_swapped_sides() -> None:
    manifest = explanation(
        value_change_claim(before_literal="timeout = 60", after_literal="timeout = 30")
    )
    assert codes(manifest) == [
        "grounding.operand_missing",
        "grounding.operand_missing",
        "grounding.replacement_link_missing",
    ]


def test_value_change_rejects_identical_literals() -> None:
    manifest = explanation(value_change_claim(after_literal="timeout = 30"))
    assert codes(manifest) == ["grounding.operand_ambiguous"]


def test_value_change_requires_support_on_both_sides() -> None:
    manifest = explanation(value_change_claim(support=["sa"]))
    assert codes(manifest) == ["grounding.side_mismatch"]


def test_unrelated_but_valid_citation_cannot_support_an_item() -> None:
    manifest = explanation(value_change_claim(support=["sb", "sa", "cc"]))
    assert codes(manifest) == ["grounding.support_unbound"]


def test_stale_support_reference_is_rejected() -> None:
    manifest = explanation(value_change_claim(support=["sb", "sa", "missing"]))
    assert codes(manifest) == ["grounding.unknown_support"]


def test_citation_outside_the_owned_change_is_rejected() -> None:
    manifest = explanation(value_change_claim(support=["sb", "sa", "ca2"]))
    assert codes(manifest) == ["grounding.support_unbound"]


def test_verified_source_order_proves_lexical_order_only() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "first": "timeout = 60",
            "second": "retries = 2",
        },
    )
    grounding = accept(manifest)
    assert "source order, not execution order" in grounding.outcomes[1].proof


def test_source_order_rejects_reversed_order() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "first": "retries = 2",
            "second": "timeout = 60",
        },
    )
    assert codes(manifest) == ["grounding.order_unproven"]


def test_source_order_rejects_evidence_for_only_one_operation() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "first": "cache.invalidate",
            "second": "retries = 2",
        },
    )
    assert codes(manifest) == ["grounding.operand_missing"]


def test_source_order_still_requires_both_operations_when_inferred() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "inferred",
            "limits": "Execution order is not established by source order.",
            "support": ["ca"],
            "side": "after",
            "first": "cache.invalidate",
            "second": "retries = 2",
        },
    )
    assert codes(manifest) == ["grounding.operand_missing"]


def test_inferred_order_keeps_both_operations_and_downgrades_confidence() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "inferred",
            "limits": "Execution order is not established by source order.",
            "support": ["ca"],
            "side": "after",
            "first": "timeout = 60",
            "second": "retries = 2",
        },
    )
    manifest["items"][0]["confidence"] = "inferred"
    grounding = accept(manifest)
    assert grounding.level_counts["inferred"] == 1
    assert grounding.outcomes[1].proof == (
        "'timeout = 60' and 'retries = 2' both appear in the bound after source"
    )


def test_verified_order_requires_a_single_contiguous_region() -> None:
    manifest = whole_file_explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca", "ca2"],
            "side": "after",
            "first": "timeout = 60",
            "second": "log('done')",
        },
    )
    assert codes(manifest) == ["grounding.region_required"]


def test_verified_order_accepts_repeated_references_to_one_region() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca", "la1", "sa"],
            "side": "after",
            "first": "timeout = 60",
            "second": "retries = 2",
        },
    )
    assert accept(manifest).claim_total == 5


def test_source_order_rejects_nested_operands() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "first": "timeout",
            "second": "timeout = 60",
        },
    )
    assert codes(manifest) == ["grounding.operand_ambiguous"]


def test_wrong_side_order_support_is_rejected() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["ca"],
            "side": "before",
            "first": "timeout = 60",
            "second": "retries = 2",
        },
    )
    assert codes(manifest) == ["grounding.side_mismatch"]


def test_addition_rejects_text_that_still_exists_on_the_before_side() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "kept",
            "type": "addition",
            "support_level": "verified",
            "support": ["sa"],
            "literal": "timeout",
        },
    )
    assert codes(manifest) == ["grounding.absence_violated"]


def test_deletion_cannot_be_verified_by_giving_the_moved_text_another_owner() -> None:
    packet, manifest = _moved_text_case()
    manifest["items"][0]["grounding"]["claims"].append(
        {
            "id": "removed",
            "type": "deletion",
            "support_level": "verified",
            "support": ["sb"],
            "literal": "timeout = 30",
        }
    )
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    codes_reported = [entry["code"] for entry in error.value.details["errors"]]
    assert codes_reported == ["grounding.absence_violated"]


def test_deletion_stays_available_as_an_honest_inferred_move() -> None:
    packet, manifest = _moved_text_case()
    manifest["items"][0]["grounding"]["claims"].append(
        {
            "id": "removed",
            "type": "deletion",
            "support_level": "inferred",
            "limits": "The same text is added elsewhere in this file, so this is a move.",
            "support": ["sb"],
            "literal": "timeout = 30",
        }
    )
    manifest["items"][0]["confidence"] = "inferred"
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["inferred"] == 1


def _moved_text_case() -> tuple[dict[str, Any], dict[str, Any]]:
    """`timeout = 30` is deleted from one position and added at another."""
    packet = evidence()
    packet["files"][0]["hunks"][0]["lines"][3]["content"] = "    timeout = 30"
    packet["files"][0]["citations"][2]["text"] = "    timeout = 30"
    manifest = explanation()
    manifest["items"][1]["grounding"]["claims"] = [
        {
            "id": "moved-in",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa2"],
            "side": "after",
            "literal": "timeout = 30",
        }
    ]
    return packet, manifest


def test_pure_addition_item_requires_an_addition_claim() -> None:
    manifest = explanation()
    item = manifest["items"][1]
    item["kind"] = "behavioral"
    item["before"] = None
    item["after"] = "The module logs completion."
    item["absence"] = "before"
    item["grounding"]["claims"] = [
        {
            "id": "log",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa2"],
            "side": "after",
            "literal": "log('done')",
        }
    ]
    assert codes(manifest) == ["grounding.item_shape"]


def test_pure_addition_item_accepts_an_addition_claim() -> None:
    manifest = explanation()
    item = manifest["items"][1]
    item["kind"] = "behavioral"
    item["before"] = None
    item["after"] = "The module logs completion."
    item["absence"] = "before"
    assert accept(manifest).claim_total == 4


def test_before_to_after_item_requires_both_sides() -> None:
    manifest = explanation(
        {
            "id": "after-only",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "side": "after",
            "literal": "timeout = 60",
        }
    )
    assert codes(manifest) == ["grounding.item_shape"]


def test_before_to_after_item_accepts_paired_sided_claims() -> None:
    manifest = explanation(
        {
            "id": "after-only",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "side": "after",
            "literal": "timeout = 60",
        },
        {
            "id": "before-only",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sb"],
            "side": "before",
            "literal": "timeout = 30",
        },
    )
    assert accept(manifest).claim_total == 5


def test_absence_requires_a_cited_region_on_that_side() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "no-legacy",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["sb"],
            "side": "after",
            "literal": "timeout = 30",
        },
    )
    assert codes(manifest) == ["grounding.absence_unscoped"]


def test_absence_fails_when_the_literal_is_present() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "no-retries",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "literal": "retries",
        },
    )
    assert codes(manifest) == ["grounding.absence_violated"]


def test_absence_succeeds_inside_the_cited_region() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "no-legacy",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "literal": "timeout = 30",
        },
    )
    grounding = accept(manifest)
    assert grounding.outcomes[1].proof == (
        "'timeout = 30' is absent from the cited after source (sa)"
    )


def test_graph_relation_rejects_the_wrong_side() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "verified",
            "support": ["fb"],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
        },
    )
    assert codes(manifest) == ["grounding.graph_fact_mismatch"]


def test_graph_relation_rejects_a_fact_from_another_path() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "verified",
            "support": ["fo"],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
        },
    )
    assert codes(manifest) == ["grounding.support_unbound"]


def test_graph_relation_accepts_an_extracted_fact_on_an_owned_path() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "verified",
            "support": ["fa"],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
            "target": "retry",
        },
    )
    grounding = accept(manifest)
    assert "static, not runtime" in grounding.outcomes[1].proof


def test_graph_relation_cannot_verify_an_inferred_fact() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "definition",
            "type": "graph_relation",
            "support_level": "verified",
            "support": ["fi"],
            "fact_kind": "definition",
            "side": "after",
            "symbol": "connect",
        },
    )
    assert codes(manifest) == ["grounding.graph_fact_mismatch"]


def test_graph_claim_requires_unavailable_support_when_the_graph_is_off() -> None:
    packet = evidence()
    packet["graph"] = {"status": "unavailable", "facts": []}
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "verified",
            "support": ["sa"],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
        },
    )
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.graph_unavailable"
    ]


def test_unavailable_graph_claim_is_accepted_as_an_honest_fallback() -> None:
    packet = evidence()
    packet["graph"] = {"status": "unavailable", "facts": []}
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "unavailable",
            "limits": "Graph enrichment is unavailable, so callers are not established.",
            "support": [],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
        },
    )
    manifest["items"][0]["confidence"] = "unavailable"
    manifest["items"][0]["kind"] = "unresolved"
    del manifest["items"][0]["before"]
    del manifest["items"][0]["after"]
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["unavailable"] == 1


def test_non_text_claim_rejects_a_mismatched_unit_kind() -> None:
    manifest = explanation()
    manifest["items"][3]["grounding"]["claims"][0]["unit_kind"] = "binary"
    assert codes(manifest) == ["grounding.non_text_mismatch"]


def test_non_text_claim_rejects_mismatched_metadata() -> None:
    manifest = explanation()
    claim = manifest["items"][3]["grounding"]["claims"][0]
    claim["metadata"] = {"old_path": "wrong.py"}
    assert codes(manifest) == ["grounding.non_text_mismatch"]


def test_confidence_cannot_exceed_the_weakest_support_level() -> None:
    manifest = explanation(
        value_change_claim(
            support_level="inferred", limits="The runtime effect is not established."
        )
    )
    assert codes(manifest) == ["grounding.confidence_overstated"]


def test_confidence_may_match_the_weakest_support_level() -> None:
    manifest = explanation(
        value_change_claim(
            support_level="inferred", limits="The runtime effect is not established."
        )
    )
    manifest["items"][0]["confidence"] = "inferred"
    assert accept(manifest).level_counts["inferred"] == 1


def test_duplicate_claim_ids_are_rejected() -> None:
    manifest = explanation(value_change_claim(), value_change_claim())
    assert codes(manifest) == ["grounding.duplicate_claim_id"]


def test_unknown_claim_type_is_rejected() -> None:
    manifest = explanation(value_change_claim(type="vibes"))
    assert codes(manifest) == ["grounding.claim_shape"]


def test_unverified_support_requires_limits() -> None:
    manifest = explanation(value_change_claim(support_level="ambiguous"))
    assert codes(manifest) == ["grounding.claim_shape"]


def test_required_mode_rejects_a_missing_grounding_block() -> None:
    manifest = explanation()
    del manifest["items"][0]["grounding"]
    assert codes(manifest) == ["grounding.missing"]


def test_optional_mode_accepts_an_ungrounded_v1_manifest() -> None:
    manifest = explanation()
    for item in manifest["items"]:
        del item["grounding"]
    result = validate_explanation(evidence(), manifest, require_grounding=False)
    assert result.grounding is not None
    assert result.grounding.claim_total == 0
    assert result.grounding.mode == "optional"


def test_optional_mode_still_validates_declared_grounding() -> None:
    manifest = explanation(value_change_claim(support=["sb", "sa", "cc"]))
    assert codes(manifest, require=False) == ["grounding.support_unbound"]


def test_shared_support_must_be_declared_with_an_owner_and_reason() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "observer",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "shared_support": [
                {"evidence_id": "sa2", "owner_id": "main", "reason": "same statement group"}
            ],
            "side": "after",
            "literal": "log('done')",
        },
    )
    assert codes(manifest) == ["grounding.shared_support_invalid"]


def test_shared_support_accepts_a_declared_neighbour_in_the_same_hunk() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "observer",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "shared_support": [
                {
                    "evidence_id": "sa2",
                    "owner_id": "second",
                    "reason": "The logging line completes the same statement group.",
                }
            ],
            "side": "after",
            "literal": "log('done')",
        },
    )
    assert accept(manifest).claim_total == 5


def test_shared_support_must_share_a_hunk_with_its_own_support() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "observer",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "shared_support": [
                {
                    "evidence_id": "sc",
                    "owner_id": "other",
                    "reason": "The other module repeats the literal.",
                }
            ],
            "side": "after",
            "literal": "timeout = 60",
        },
    )
    assert codes(manifest) == ["grounding.shared_support_nonlocal"]


def test_hunk_support_binds_when_it_intersects_the_owned_change() -> None:
    manifest = explanation(
        value_change_claim(support=["sb", "h1"]),
    )
    assert accept(manifest).claim_total == 4


def test_hunk_support_cannot_reach_another_item_span_in_the_same_hunk() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "borrowed",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["h1"],
            "side": "after",
            "literal": "log('done')",
        },
    )
    assert codes(manifest) == ["grounding.operand_missing"]


def test_unit_support_cannot_reach_another_item_span_in_the_same_unit() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "borrowed",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["u1"],
            "side": "after",
            "literal": "log('done')",
        },
    )
    assert codes(manifest) == ["grounding.operand_missing"]


def test_line_support_is_evaluated_over_its_whole_span() -> None:
    """A single cited line must not shrink a span-scoped predicate."""
    packet = evidence()
    manifest = explanation(
        value_change_claim(support=["lb", "la1"]),
    )
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.outcomes[0].proof == (
        "replacement span sb -> sa changes 'timeout = 30' to 'timeout = 60'"
    )


def test_line_support_cannot_hide_a_sibling_line_from_a_replacement_check() -> None:
    packet = evidence()
    packet["files"][0]["hunks"][0]["lines"][2]["content"] = "    timeout = 30  # legacy"
    packet["files"][0]["citations"][1]["text"] = "    timeout = 60\n    timeout = 30  # legacy"
    manifest = explanation(value_change_claim(support=["lb", "la1"]))
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.replacement_link_missing"
    ]


def test_line_support_cannot_hide_a_sibling_line_from_an_absence_check() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "absent",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["la1", "la2"],
            "side": "after",
            "literal": "retries = 2",
        },
    )
    assert codes(manifest) == ["grounding.absence_violated"]


def test_line_support_cannot_hide_a_sibling_line_from_an_order_check() -> None:
    packet = evidence()
    packet["files"][0]["hunks"][0]["lines"][1]["content"] = "    emit(a); emit(b)"
    packet["files"][0]["hunks"][0]["lines"][2]["content"] = "    emit(b); emit(a)"
    manifest = explanation(
        {
            "id": "before-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sb"],
            "side": "before",
            "literal": "timeout = 30",
        },
        {
            "id": "order",
            "type": "source_order",
            "support_level": "verified",
            "support": ["la1"],
            "side": "after",
            "first": "emit(a)",
            "second": "emit(b)",
        },
    )
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.order_unproven"
    ]


def test_unresolved_presence_claims_do_not_assert_the_literal() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "unknown",
            "type": "text_presence",
            "support_level": "unresolved",
            "limits": "The evidence does not settle this wording.",
            "support": [],
            "side": "after",
            "literal": "cache.invalidate() runs after db.commit()",
        },
    )
    manifest["items"][0]["kind"] = "unresolved"
    manifest["items"][0]["confidence"] = "unresolved"
    del manifest["items"][0]["before"]
    del manifest["items"][0]["after"]
    grounding = accept(manifest)
    proofs = {outcome.claim_id: outcome.proof for outcome in grounding.outcomes}
    assert proofs["unknown"] == (
        "the presence of 'cache.invalidate() runs after db.commit()' in the after source "
        "is not established"
    )


def test_review_language_cannot_hide_in_an_unproven_operand() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "unknown",
            "type": "text_presence",
            "support_level": "unresolved",
            "limits": "The evidence does not settle this wording.",
            "support": [],
            "side": "after",
            "literal": "Critical severity: this change introduces a vulnerability.",
        },
    )
    manifest["items"][0]["kind"] = "unresolved"
    manifest["items"][0]["confidence"] = "unresolved"
    del manifest["items"][0]["before"]
    del manifest["items"][0]["after"]
    assert codes(manifest) == ["-", "-"]


def test_asserting_operands_may_quote_diff_text_that_mentions_a_vulnerability() -> None:
    packet = evidence()
    packet["files"][0]["hunks"][0]["lines"][1]["content"] = "    # fixes the vulnerability"
    packet["files"][0]["citations"][1]["text"] = "    # fixes the vulnerability\n    retries = 2"
    manifest = explanation(
        {
            "id": "before-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sb"],
            "side": "before",
            "literal": "timeout = 30",
        },
        {
            "id": "after-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "side": "after",
            "literal": "# fixes the vulnerability",
        },
    )
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == 5


def test_presence_proofs_name_the_region_they_examined() -> None:
    manifest = explanation(
        {
            "id": "before-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sb"],
            "side": "before",
            "literal": "timeout = 30",
        },
        {
            "id": "after-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["la1"],
            "side": "after",
            "literal": "retries = 2",
        },
    )
    grounding = accept(manifest)
    proofs = {outcome.claim_id: outcome.proof for outcome in grounding.outcomes}
    assert proofs["after-side"] == "the cited after source (sa) contains 'retries = 2'"


def test_review_language_cannot_hide_in_a_shared_support_reason() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "observer",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["sa"],
            "shared_support": [
                {
                    "evidence_id": "sa2",
                    "owner_id": "second",
                    "reason": "Critical severity: this change introduces a vulnerability.",
                }
            ],
            "side": "after",
            "literal": "log('done')",
        },
    )
    assert codes(manifest) == ["-", "-"]


def test_budget_omitted_citations_fall_back_to_changed_line_text() -> None:
    packet = evidence()
    citation = packet["files"][0]["citations"][1]
    citation["text"] = None
    citation["omitted"] = True
    manifest = explanation(
        value_change_claim(support=["cb", "ca"]),
    )
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == 4


def test_grounding_is_skipped_while_ownership_errors_remain() -> None:
    manifest = explanation()
    manifest["coverage_owners"] = [
        owner for owner in manifest["coverage_owners"] if owner["evidence_id"] != "lb"
    ]
    reported = codes(manifest)
    assert reported == ["-"]


def test_review_language_cannot_hide_in_claim_limits() -> None:
    manifest = explanation(
        value_change_claim(
            support_level="inferred",
            limits="This change introduces a bug that should be fixed.",
        )
    )
    manifest["items"][0]["confidence"] = "inferred"
    assert codes(manifest) == ["-", "-"]


def test_unverified_proofs_never_assert_the_unchecked_predicate() -> None:
    manifest = explanation(
        value_change_claim(
            support_level="inferred", limits="The replacement is read from context."
        ),
        {
            "id": "absent",
            "type": "text_absence",
            "support_level": "inferred",
            "limits": "Only the cited region is inspected.",
            "support": ["ca"],
            "side": "after",
            "literal": "timeout = 30",
        },
    )
    manifest["items"][0]["confidence"] = "inferred"
    manifest["items"][1]["grounding"]["claims"][0].update(
        support_level="inferred", limits="The addition may be a move."
    )
    manifest["items"][1]["confidence"] = "inferred"
    manifest["items"][3]["grounding"]["claims"][0].update(
        support_level="inferred", limits="Rename metadata is read from Git only."
    )
    manifest["items"][3]["confidence"] = "inferred"
    grounding = accept(manifest)
    proofs = {outcome.claim_id: outcome.proof for outcome in grounding.outcomes}
    assert proofs["value"] == (
        "'timeout = 30' appears in the bound before source and 'timeout = 60' in the bound "
        "after source; the replacement itself is asserted, not proven"
    )
    assert proofs["absent"] == (
        "the cited after source (sa) is bound to this claim; the absence of 'timeout = 30' "
        "is asserted, not proven"
    )
    assert proofs["log"] == (
        "\"log('done')\" appears in the bound after source; that it is added rather than moved "
        "or retained is asserted, not proven"
    )
    assert proofs["rename"] == (
        "change unit u3 is a rename change; any declared metadata is asserted, not proven"
    )


def test_unresolved_proofs_state_that_nothing_is_established() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "unresolved",
            "limits": "Callers were not inspected for this comparison.",
            "support": [],
            "fact_kind": "caller",
            "side": "after",
            "symbol": "connect",
        },
    )
    manifest["items"][0]["kind"] = "unresolved"
    manifest["items"][0]["confidence"] = "unresolved"
    del manifest["items"][0]["before"]
    del manifest["items"][0]["after"]
    grounding = accept(manifest)
    proofs = {outcome.claim_id: outcome.proof for outcome in grounding.outcomes}
    assert proofs["callers"] == (
        "a caller relation for 'connect' on the after side is not established"
    )


REVIEW_PROSE = "Critical severity: this change exposes credentials and I recommend fixing it."


@pytest.mark.parametrize(
    "level", ["verified", "inferred", "ambiguous", "unresolved", "unavailable"]
)
def test_absence_literals_are_scanned_at_every_support_level(level: str) -> None:
    """Nothing forces an absence literal to be source text, at any level."""
    claim: dict[str, Any] = {
        "id": "leak",
        "type": "text_absence",
        "support_level": level,
        "support": ["ca"],
        "side": "after",
        "literal": REVIEW_PROSE,
    }
    if level != "verified":
        claim["limits"] = "Scoped to the cited region."
    manifest = explanation(value_change_claim(), claim)
    if level in {"unresolved", "unavailable", "ambiguous"}:
        manifest["items"][0]["kind"] = "ambiguity" if level == "ambiguous" else "unresolved"
        manifest["items"][0]["confidence"] = level
        del manifest["items"][0]["before"]
        del manifest["items"][0]["after"]
    elif level == "inferred":
        manifest["items"][0]["confidence"] = "inferred"
    reported = codes(manifest)
    assert reported and all(code == "-" for code in reported)


def test_absence_literals_that_are_real_source_text_still_pass() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "no-legacy",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "literal": "timeout = 30",
        },
    )
    assert accept(manifest).claim_total == 5


def test_source_derived_absence_literal_survives_review_wording() -> None:
    """A deleted comment is quoted source even though it reads like a review."""
    packet = evidence()
    packet["files"][0]["hunks"][0]["lines"][0]["content"] = "    # this should be fixed"
    packet["files"][0]["citations"][0]["text"] = "    # this should be fixed"
    manifest = explanation(
        {
            "id": "gone",
            "type": "text_absence",
            "support_level": "verified",
            "support": ["ca"],
            "side": "after",
            "literal": "# this should be fixed",
        },
        {
            "id": "before-side",
            "type": "text_presence",
            "support_level": "verified",
            "support": ["cb"],
            "side": "before",
            "literal": "# this should be fixed",
        },
    )
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == 5


def test_graph_operands_are_scanned_when_nothing_matches_them() -> None:
    manifest = explanation(
        value_change_claim(),
        {
            "id": "callers",
            "type": "graph_relation",
            "support_level": "unresolved",
            "limits": "Callers are not established.",
            "support": [],
            "fact_kind": "caller",
            "side": "after",
            "symbol": REVIEW_PROSE,
        },
    )
    manifest["items"][0]["kind"] = "unresolved"
    manifest["items"][0]["confidence"] = "unresolved"
    del manifest["items"][0]["before"]
    del manifest["items"][0]["after"]
    reported = codes(manifest)
    assert reported and all(code == "-" for code in reported)


def test_unverified_non_text_metadata_is_scanned() -> None:
    manifest = explanation()
    claim = manifest["items"][3]["grounding"]["claims"][0]
    claim["support_level"] = "inferred"
    claim["limits"] = "Rename metadata is read from Git only."
    claim["metadata"] = {"old_path": REVIEW_PROSE}
    manifest["items"][3]["confidence"] = "inferred"
    reported = codes(manifest)
    assert reported and all(code == "-" for code in reported)


def test_claim_ids_are_scanned_like_other_authored_text() -> None:
    manifest = explanation(value_change_claim(id="this change introduces a bug"))
    reported = codes(manifest)
    assert reported and all(code == "-" for code in reported)


def test_non_text_metadata_must_match_one_unit() -> None:
    packet = _two_rename_packet()
    manifest = _two_rename_manifest()
    manifest["items"][3]["grounding"]["claims"][0]["metadata"] = {
        "old_path": "old.py",
        "new_path": "new_b.py",
    }
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.non_text_mismatch"
    ]


def test_non_text_metadata_accepts_a_single_consistent_unit() -> None:
    packet = _two_rename_packet()
    manifest = _two_rename_manifest()
    manifest["items"][3]["grounding"]["claims"][0]["metadata"] = {
        "old_path": "old_b.py",
        "new_path": "new_b.py",
    }
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    proof = next(
        outcome.proof for outcome in result.grounding.outcomes if outcome.claim_id == "rename"
    )
    assert proof.startswith("change unit u4 is a rename change")


def test_an_absent_metadata_key_does_not_match_a_declared_null() -> None:
    manifest = explanation()
    manifest["items"][3]["grounding"]["claims"][0]["metadata"] = {"missing_key": None}
    assert codes(manifest) == ["grounding.non_text_mismatch"]


def test_a_present_null_metadata_key_matches_a_declared_null() -> None:
    packet = evidence()
    packet["files"][2]["units"][0]["metadata"] = {"old_path": None, "new_path": "new.py"}
    manifest = explanation()
    manifest["items"][3]["grounding"]["claims"][0]["metadata"] = {"old_path": None}
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    assert result.grounding.level_counts["verified"] == 4


def test_multiple_identical_units_bind_the_lexicographically_first(caplog: Any) -> None:
    del caplog
    packet = _two_rename_packet()
    packet["files"][3]["units"][0]["metadata"] = {"old_path": "old.py", "new_path": "new.py"}
    manifest = _two_rename_manifest()
    result = validate_explanation(packet, manifest, require_grounding=True)
    assert result.grounding is not None
    proof = next(
        outcome.proof for outcome in result.grounding.outcomes if outcome.claim_id == "rename"
    )
    assert proof.startswith("change unit u3 is a rename change")


def test_non_text_claim_still_rejects_the_wrong_kind() -> None:
    packet = _two_rename_packet()
    manifest = _two_rename_manifest()
    manifest["items"][3]["grounding"]["claims"][0]["unit_kind"] = "binary"
    with pytest.raises(ValidationError) as error:
        validate_explanation(packet, manifest, require_grounding=True)
    assert [entry["code"] for entry in error.value.details["errors"]] == [
        "grounding.non_text_mismatch"
    ]


def _two_rename_packet() -> dict[str, Any]:
    packet = evidence()
    packet["files"].append(
        {
            "old_path": "old_b.py",
            "new_path": "new_b.py",
            "status": "renamed",
            "units": [
                {
                    "id": "u4",
                    "kind": "rename",
                    "hunk_ids": [],
                    "metadata": {"old_path": "old_b.py", "new_path": "new_b.py"},
                }
            ],
            "hunks": [],
            "spans": [],
            "citations": [],
        }
    )
    return packet


def _two_rename_manifest() -> dict[str, Any]:
    manifest = explanation()
    manifest["items"][3]["grounding"]["claims"][0]["support"] = ["u3", "u4"]
    manifest["coverage_owners"].append({"evidence_id": "u4", "owner_id": "renamed"})
    return manifest


def test_claim_counts_are_stable_under_repeated_validation() -> None:
    manifest = explanation()
    first = validate_explanation(evidence(), copy.deepcopy(manifest), require_grounding=True)
    second = validate_explanation(evidence(), copy.deepcopy(manifest), require_grounding=True)
    assert first.grounding is not None
    assert second.grounding is not None
    assert first.grounding.to_dict() == second.grounding.to_dict()
