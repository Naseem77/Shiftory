"""Structural-invariant tests: excerpt anchoring, overlap declarations, audit
coverage exhaustiveness (structural, not semantic), invalid-candidate
exclusivity, and case-id path/traversal/symlink safety."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agent_quality import validation as v


def _provenance() -> dict[str, Any]:
    return {
        "actor_type": "agent",
        "actor": {
            "name": "test-auditor",
            "organization": None,
            "model": None,
            "tool": None,
            "version": None,
        },
        "method": "manual review",
        "timestamp": "2024-01-01T00:00:00Z",
    }


def _explanation() -> dict[str, Any]:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "Guard clause moved after validation.",
        "items": [
            {
                "id": "x",
                "kind": "behavioral",
                "title": "Reorder guard",
                "before": "Empty input returned early.",
                "after": "Empty input is validated after the lookup runs.",
                "statement": "The guard clause moved after the lookup call.",
                "confidence": "extracted",
                "citations": ["h1"],
            }
        ],
        "coverage_owners": [],
    }


def _claim(explanation: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    text = explanation["items"][0]["statement"]
    start, end = 4, 27
    claim = {
        "claim_id": "c1",
        "source_item_id": "x",
        "field": "/items/0/statement",
        "start": start,
        "end": end,
        "excerpt_sha256": hashlib.sha256(text[start:end].encode()).hexdigest(),
        "overlaps_with": [],
        "maps_to_required_fact_id": "f1",
        "verdict": "supported_correct",
        "materiality": 3,
        "evidence_anchor_cited": ["h1"],
        "confidence_expressed": "extracted",
        "confidence_appropriate": True,
        "rationale": "matches the fixture diff",
        "annotation_provenance": _provenance(),
    }
    claim.update(overrides)
    return claim


def _full_audit_coverage(claim_ids_for_statement: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "field": "/summary",
            "claim_ids": [],
            "decomposition_complete": True,
            "non_claim_rationale": "restated",
        },
        {
            "field": "/items/0/title",
            "claim_ids": [],
            "decomposition_complete": True,
            "non_claim_rationale": "label",
        },
        {
            "field": "/items/0/before",
            "claim_ids": [],
            "decomposition_complete": True,
            "non_claim_rationale": "restated",
        },
        {
            "field": "/items/0/after",
            "claim_ids": [],
            "decomposition_complete": True,
            "non_claim_rationale": "restated",
        },
        {
            "field": "/items/0/statement",
            "claim_ids": claim_ids_for_statement,
            "decomposition_complete": True,
        },
    ]


def _evaluation(explanation: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "shiftory.benchmark-agent-quality-candidate-evaluation/v1",
        "case_id": "demo-case",
        "rubric_version": 1,
        "candidate_id": "synthetic_baseline",
        "candidate_kind": "synthetic_baseline",
        "explanation_sha256": "a" * 64,
        "claims": [claim],
        "audit_coverage": _full_audit_coverage([claim["claim_id"]]),
    }


def _rubric() -> dict[str, Any]:
    return {"required_facts": [{"id": "f1"}]}


def test_valid_evaluation_passes() -> None:
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_excerpt_hash_mismatch_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation, excerpt_sha256="0" * 64)
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="does not match the candidate text"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_out_of_range_span_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation, start=0, end=10_000)
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="out-of-range span"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_zero_length_span_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation, start=5, end=5)
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="end <= start"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_undeclared_overlap_is_rejected() -> None:
    explanation = _explanation()
    claim_a = _claim(explanation, claim_id="c1", start=4, end=27)
    claim_b = _claim(explanation, claim_id="c2", start=10, end=30, excerpt_sha256="1" * 64)
    # Force a real hash so only the overlap check trips, not the anchor check.
    text = explanation["items"][0]["statement"]
    claim_b["excerpt_sha256"] = hashlib.sha256(text[10:30].encode()).hexdigest()
    evaluation = _evaluation(explanation, claim_a)
    evaluation["claims"] = [claim_a, claim_b]
    evaluation["audit_coverage"] = _full_audit_coverage(["c1", "c2"])
    with pytest.raises(v.AgentQualityError, match="overlap"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_declared_overlap_is_accepted() -> None:
    explanation = _explanation()
    text = explanation["items"][0]["statement"]
    claim_a = _claim(explanation, claim_id="c1", start=4, end=27, overlaps_with=["c2"])
    claim_b = _claim(
        explanation,
        claim_id="c2",
        start=10,
        end=30,
        excerpt_sha256=hashlib.sha256(text[10:30].encode()).hexdigest(),
        overlaps_with=["c1"],
    )
    evaluation = _evaluation(explanation, claim_a)
    evaluation["claims"] = [claim_a, claim_b]
    evaluation["audit_coverage"] = _full_audit_coverage(["c1", "c2"])
    v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_missing_audit_coverage_field_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    evaluation["audit_coverage"] = [
        c for c in evaluation["audit_coverage"] if c["field"] != "/summary"
    ]
    with pytest.raises(v.AgentQualityError, match="Missing audit_coverage"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_audit_coverage_claim_id_mismatch_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    for entry in evaluation["audit_coverage"]:
        if entry["field"] == "/items/0/statement":
            entry["claim_ids"] = []
            entry["non_claim_rationale"] = "wrong"
    with pytest.raises(v.AgentQualityError, match="lists claim ids"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_claim_referencing_field_without_material_text_is_rejected() -> None:
    explanation = _explanation()
    explanation["items"][0]["after"] = ""
    claim = _claim(explanation, field="/items/0/after", start=0, end=1, excerpt_sha256="0" * 64)
    evaluation = _evaluation(explanation, claim)
    evaluation["audit_coverage"] = [
        c for c in _full_audit_coverage([]) if c["field"] != "/items/0/after"
    ]
    with pytest.raises(v.AgentQualityError):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_claim_mapping_to_unknown_required_fact_is_rejected() -> None:
    explanation = _explanation()
    claim = _claim(explanation, maps_to_required_fact_id="does-not-exist")
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="unknown required fact"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_invalid_candidate_cannot_coexist_with_claims() -> None:
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    evaluation["invalid_candidate"] = {
        "reason": "not JSON",
        "raw_response_sha256": "a" * 64,
        "protocol_violation": "wrapped in markdown fences",
    }
    with pytest.raises(v.AgentQualityError, match="must not co-occur"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_invalid_candidate_alone_is_valid() -> None:
    evaluation = {
        "schema": "shiftory.benchmark-agent-quality-candidate-evaluation/v1",
        "case_id": "demo-case",
        "rubric_version": 1,
        "candidate_id": "captured-a",
        "candidate_kind": "captured_real_run",
        "explanation_sha256": None,
        "invalid_candidate": {
            "reason": "raw response was prose, not JSON",
            "raw_response_sha256": "b" * 64,
            "protocol_violation": "response was not exactly one JSON document",
        },
    }
    v.validate_candidate_evaluation(None, evaluation, _rubric())


def test_usable_candidate_requires_explanation_sha256() -> None:
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    evaluation["explanation_sha256"] = None
    with pytest.raises(v.AgentQualityError, match="requires explanation_sha256"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_too_many_claims_is_rejected() -> None:
    # The candidate-evaluation-v1 schema's own maxItems cap trips first; either
    # that schema-level rejection or the module's own count check is acceptable,
    # as long as the record is rejected.
    explanation = _explanation()
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    evaluation["claims"] = [claim] * (v.MAX_CLAIMS_PER_EVALUATION + 1)
    with pytest.raises(v.AgentQualityError):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_too_many_items_is_rejected() -> None:
    explanation = _explanation()
    explanation["items"] = explanation["items"] * (v.MAX_ITEMS_PER_EXPLANATION + 1)
    claim = _claim(explanation)
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="exceeding cap"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


def test_oversized_text_field_is_rejected() -> None:
    explanation = _explanation()
    explanation["items"][0]["statement"] = "x" * (v.MAX_TEXT_FIELD_CHARS + 1)
    claim = _claim(explanation, end=v.MAX_TEXT_FIELD_CHARS + 1)
    claim["excerpt_sha256"] = hashlib.sha256(
        explanation["items"][0]["statement"][4 : v.MAX_TEXT_FIELD_CHARS + 1].encode()
    ).hexdigest()
    evaluation = _evaluation(explanation, claim)
    with pytest.raises(v.AgentQualityError, match="exceeds"):
        v.validate_candidate_evaluation(explanation, evaluation, _rubric())


# -- case-id path/traversal/symlink safety -----------------------------------


def test_safe_case_dir_accepts_valid_id(tmp_path: Path) -> None:
    (tmp_path / "demo-case").mkdir()
    resolved = v.safe_case_dir(tmp_path, "demo-case")
    assert resolved == (tmp_path / "demo-case").resolve()


def test_safe_case_dir_rejects_invalid_id(tmp_path: Path) -> None:
    with pytest.raises(v.AgentQualityError, match="Invalid case id"):
        v.safe_case_dir(tmp_path, "../escape")


def test_safe_case_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    escape_id = "escape-case"
    (base / escape_id).symlink_to(outside)
    with pytest.raises(v.AgentQualityError, match="escapes"):
        v.safe_case_dir(base, escape_id)


def test_check_file_size_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "history.fast-import"
    path.write_bytes(b"x" * 100)
    with pytest.raises(v.AgentQualityError, match="exceeding the"):
        v.check_file_size(path, max_bytes=10, label="fixture history")
