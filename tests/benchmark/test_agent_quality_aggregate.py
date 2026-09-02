"""Unit and property tests for the pure score-v1 arithmetic aggregator."""

from __future__ import annotations

import random
from typing import Any

from benchmarks.agent_quality import aggregate


def _provenance() -> dict[str, Any]:
    return {
        "actor_type": "agent",
        "actor": {
            "name": "test",
            "organization": None,
            "model": None,
            "tool": None,
            "version": None,
        },
        "method": "manual review",
        "timestamp": "2024-01-01T00:00:00Z",
    }


def _claim(**overrides: Any) -> dict[str, Any]:
    claim = {
        "claim_id": "c1",
        "source_item_id": "x",
        "field": "/items/0/statement",
        "start": 0,
        "end": 5,
        "excerpt_sha256": "0" * 64,
        "overlaps_with": [],
        "maps_to_required_fact_id": None,
        "verdict": "supported_correct",
        "materiality": 3,
        "evidence_anchor_cited": [],
        "confidence_expressed": "extracted",
        "confidence_appropriate": True,
        "rationale": "test",
        "annotation_provenance": _provenance(),
    }
    claim.update(overrides)
    return claim


def _rubric(*facts: dict[str, Any]) -> dict[str, Any]:
    return {"required_facts": list(facts)}


def _fact(fact_id: str, truth_status: str = "extractable", importance: int = 3) -> dict[str, Any]:
    return {
        "id": fact_id,
        "description": "d",
        "importance": importance,
        "evidence_anchors": [],
        "truth_status": truth_status,
    }


def _evaluation(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {"claims": claims}


def test_claim_factuality_ratio() -> None:
    claims = [
        _claim(claim_id="a", verdict="supported_correct", materiality=2),
        _claim(claim_id="b", verdict="unsupported", materiality=1),
        _claim(claim_id="c", verdict="non_semantic", materiality=5),  # excluded, not assessable
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(),
    )
    factuality = score["claim_factuality"]
    assert factuality["assessable_count"] == 2
    assert factuality["supported_count"] == 1
    assert factuality["assessable_weight"] == 3
    assert factuality["supported_weight"] == 2
    assert factuality["ratio"] == 2 / 3


def test_unsupported_and_contradicted_are_separate_and_count_unmapped_claims() -> None:
    claims = [
        _claim(claim_id="a", verdict="unsupported", materiality=2, maps_to_required_fact_id=None),
        _claim(claim_id="b", verdict="contradicted", materiality=4, maps_to_required_fact_id=None),
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(),
    )
    assert score["unsupported_claims"] == {"count": 1, "weight": 2, "claim_ids": ["a"]}
    assert score["contradicted_claims"] == {"count": 1, "weight": 4, "claim_ids": ["b"]}


def test_extractable_fact_requires_supported_correct_to_be_covered() -> None:
    fact = _fact("f1", truth_status="extractable", importance=4)
    claims = [_claim(claim_id="a", verdict="unsupported", maps_to_required_fact_id="f1")]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_adversarial",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
    )
    assert score["required_behavior_coverage"]["satisfied_count"] == 0
    assert score["semantic_omissions"]["missed_ids"] == ["f1"]
    assert score["semantic_omissions"]["missed_weight"] == 4


def test_ambiguous_fact_is_satisfied_by_a_correctly_graded_honest_hedge_claim() -> None:
    """truth_status is auditor guidance for confidence_appropriate, not a switch
    on which verdict counts as coverage: coverage always requires
    supported_correct. For a fact whose true answer is genuinely
    ambiguous/unresolvable, the *correct* claim is an honest 'the evidence does
    not resolve this' statement, and once the auditor confirms that statement
    is itself true, it is graded supported_correct like any other correct
    claim -- it is not penalized for hedging.
    """
    fact = _fact("f1", truth_status="ambiguous_unresolvable", importance=2)

    honestly_ambiguous = [
        _claim(
            claim_id="a",
            verdict="supported_correct",
            confidence_expressed="ambiguous",
            confidence_appropriate=True,
            maps_to_required_fact_id="f1",
        )
    ]
    score_honest = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(honestly_ambiguous),
        rubric=_rubric(fact),
    )
    assert score_honest["required_behavior_coverage"]["satisfied_count"] == 1
    assert score_honest["semantic_omissions"]["missed_ids"] == []
    assert score_honest["uncertainty_honesty"]["violations"] == 0

    overconfident_wrong = [
        _claim(
            claim_id="a",
            verdict="unsupported",
            confidence_expressed="extracted",
            confidence_appropriate=False,
            maps_to_required_fact_id="f1",
        )
    ]
    score_overconfident = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_adversarial",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(overconfident_wrong),
        rubric=_rubric(fact),
    )
    assert score_overconfident["required_behavior_coverage"]["satisfied_count"] == 0
    assert score_overconfident["semantic_omissions"]["missed_ids"] == ["f1"]
    assert score_overconfident["uncertainty_honesty"]["violations"] == 1


def test_wrong_mapped_claim_still_leaves_fact_missed() -> None:
    # A required fact addressed only by an unsupported/contradicted claim must
    # still be counted as missed -- addressing a fact is not the same as
    # correctly conveying it.
    fact = _fact("f1")
    claims = [_claim(claim_id="a", verdict="contradicted", maps_to_required_fact_id="f1")]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_adversarial",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
    )
    assert score["semantic_omissions"]["missed_ids"] == ["f1"]
    assert score["contradicted_claims"]["count"] == 1


def test_uncertainty_honesty_violations() -> None:
    claims = [
        _claim(claim_id="a", confidence_appropriate=True),
        _claim(claim_id="b", confidence_appropriate=False),
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(),
    )
    assert score["uncertainty_honesty"] == {"checked": 2, "violations": 1, "violation_ids": ["b"]}


def test_usefulness_relevance_counts_useful_non_semantic_and_ambiguous() -> None:
    fact = _fact("f1")
    claims = [
        _claim(claim_id="a", verdict="supported_correct", maps_to_required_fact_id="f1"),
        _claim(claim_id="b", verdict="supported_correct", maps_to_required_fact_id=None),
        _claim(claim_id="c", verdict="non_semantic"),
        _claim(claim_id="d", verdict="ambiguous_unresolvable"),
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
        item_count=4,
    )
    usefulness = score["usefulness_relevance"]
    assert usefulness["useful_count"] == 1
    assert usefulness["item_count"] == 4
    assert usefulness["non_semantic_claims"] == 1
    assert usefulness["ambiguous_unresolvable_claims"] == 1


def test_invalid_candidate_nulls_every_semantic_section() -> None:
    evaluation = {
        "invalid_candidate": {
            "reason": "raw response was prose",
            "raw_response_sha256": "a" * 64,
            "protocol_violation": "not exactly one JSON document",
        }
    }
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="captured-a",
        candidate_kind="captured_real_run",
        explanation_sha256=None,
        evaluation=evaluation,
        rubric=_rubric(),
    )
    for key in (
        "accounting",
        "claim_factuality",
        "unsupported_claims",
        "contradicted_claims",
        "required_behavior_coverage",
        "semantic_omissions",
        "uncertainty_honesty",
        "usefulness_relevance",
        "rubric_match_heuristic",
        "audit_status",
        "gate",
    ):
        assert score[key] is None, key
    assert score["structural_failure"]["reason"] == "raw response was prose"


def test_gate_passes_for_claim_perfect_synthetic_candidate() -> None:
    fact = _fact("f1")
    claims = [_claim(claim_id="a", verdict="supported_correct", maps_to_required_fact_id="f1")]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="synthetic_baseline",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
    )
    assert score["gate"] == {"pass": True, "reasons": []}


def test_gate_fails_for_defective_synthetic_candidate() -> None:
    fact = _fact("f1", importance=5)
    claims = [
        _claim(claim_id="a", verdict="unsupported"),
        _claim(claim_id="b", verdict="contradicted"),
        _claim(claim_id="c", confidence_appropriate=False),
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="synthetic_adversarial",
        candidate_kind="synthetic_adversarial",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
    )
    assert score["gate"] is not None
    assert score["gate"]["pass"] is False
    assert len(score["gate"]["reasons"]) == 4  # unsupported, contradicted, missed fact, violation


def test_gate_is_always_none_for_captured_real_run() -> None:
    claims = [_claim(claim_id="a", verdict="unsupported")]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="captured-a",
        candidate_kind="captured_real_run",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(),
    )
    assert score["gate"] is None


def test_result_is_order_independent() -> None:
    fact = _fact("f1")
    claims = [
        _claim(claim_id="a", verdict="supported_correct", maps_to_required_fact_id="f1"),
        _claim(claim_id="b", verdict="unsupported", confidence_appropriate=False),
        _claim(claim_id="c", verdict="contradicted"),
        _claim(claim_id="d", verdict="non_semantic"),
    ]
    shuffled = list(claims)
    random.Random(7).shuffle(shuffled)

    score_original = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(claims),
        rubric=_rubric(fact),
    )
    score_shuffled = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation(shuffled),
        rubric=_rubric(fact),
    )
    assert score_original == score_shuffled


def test_empty_claims_means_every_fact_is_missed() -> None:
    facts = [_fact("f1"), _fact("f2")]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_adversarial",
        explanation_sha256="a" * 64,
        evaluation=_evaluation([]),
        rubric=_rubric(*facts),
    )
    assert score["semantic_omissions"]["missed_ids"] == ["f1", "f2"]
    assert score["required_behavior_coverage"]["ratio"] == 0.0


def test_no_required_facts_gives_null_coverage_ratio_not_division_error() -> None:
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation=_evaluation([]),
        rubric=_rubric(),
    )
    assert score["required_behavior_coverage"]["ratio"] is None
    assert score["claim_factuality"]["ratio"] is None


def _annotation_provenance(actor_type: str, name: str) -> dict:
    return {
        "actor_type": actor_type,
        "actor": {"name": name, "organization": None, "model": None, "tool": None, "version": None},
        "method": "m",
        "timestamp": "2024-01-01T00:00:00Z",
    }


def test_derive_audit_status_dual_audit_with_no_disagreement() -> None:
    evaluation = {
        "claims": [_claim(claim_id="a"), _claim(claim_id="b")],
        "annotation_passes": [
            {"annotation_provenance": _annotation_provenance("agent", "pass-1")},
            {"annotation_provenance": _annotation_provenance("agent", "pass-2")},
        ],
        "adjudication": {
            "rationale": "full agreement",
            "annotation_provenance": _annotation_provenance("agent", "adjudicator"),
            "overridden_claim_ids": [],
        },
    }
    status = aggregate.derive_audit_status(evaluation)
    assert status == {
        "mode": "dual_audit",
        "actor_type": "agent",
        "disagreement_count": 0,
        "disagreement_rate": 0.0,
        "adjudicator": "adjudicator",
    }


def test_derive_audit_status_reflects_overridden_claims() -> None:
    evaluation = {
        "claims": [
            _claim(claim_id="a"),
            _claim(claim_id="b"),
            _claim(claim_id="c"),
            _claim(claim_id="d"),
        ],
        "annotation_passes": [
            {"annotation_provenance": _annotation_provenance("agent", "pass-1")},
            {"annotation_provenance": _annotation_provenance("agent", "pass-2")},
        ],
        "adjudication": {
            "rationale": "partial disagreement",
            "annotation_provenance": _annotation_provenance("agent", "adjudicator"),
            "overridden_claim_ids": ["a", "b", "c"],
        },
    }
    status = aggregate.derive_audit_status(evaluation)
    assert status["disagreement_count"] == 3
    assert status["disagreement_rate"] == 0.75


def test_derive_audit_status_single_pass_is_provisional() -> None:
    evaluation = {
        "claims": [_claim(claim_id="a")],
        "annotation_passes": [{"annotation_provenance": _annotation_provenance("agent", "pass-1")}],
        "adjudication": None,
    }
    status = aggregate.derive_audit_status(evaluation)
    assert status["mode"] == "provisional_single_audit"
    assert status["adjudicator"] is None


def test_derive_audit_status_none_when_no_passes_recorded() -> None:
    assert aggregate.derive_audit_status({"claims": []}) is None


def test_derive_audit_status_mixed_actor_types() -> None:
    evaluation = {
        "claims": [],
        "annotation_passes": [
            {"annotation_provenance": _annotation_provenance("agent", "pass-1")},
            {"annotation_provenance": _annotation_provenance("human", "pass-2")},
        ],
        "adjudication": None,
    }
    status = aggregate.derive_audit_status(evaluation)
    assert status["actor_type"] == "mixed"


def test_aggregate_score_auto_derives_audit_status_for_captured_real_run() -> None:
    evaluation = {
        "claims": [_claim(claim_id="a", verdict="supported_correct")],
        "annotation_passes": [
            {"annotation_provenance": _annotation_provenance("agent", "pass-1")},
            {"annotation_provenance": _annotation_provenance("agent", "pass-2")},
        ],
        "adjudication": {
            "rationale": "agree",
            "annotation_provenance": _annotation_provenance("agent", "adjudicator"),
            "overridden_claim_ids": [],
        },
    }
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="captured-a",
        candidate_kind="captured_real_run",
        explanation_sha256="a" * 64,
        evaluation=evaluation,
        rubric=_rubric(),
    )
    assert score["audit_status"] is not None
    assert score["audit_status"]["mode"] == "dual_audit"
    assert score["gate"] is None
