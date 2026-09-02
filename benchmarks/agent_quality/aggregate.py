"""Deterministic arithmetic aggregation: candidate-evaluation-v1 -> score-v1.

Every function here is pure arithmetic over an already-audited
``candidate-evaluation-v1`` record (see ``validation.py`` for the structural
checks that must pass before a record reaches this module) plus a
``rubric-v1`` record. Nothing here re-derives, second-guesses, or automates the
underlying claim verdicts -- it only counts, weights, and sums them. See
``benchmarks/agent_quality/__init__.py`` for the honesty statement this module
exists to uphold: automated code can prove the arithmetic below is correct
given fixed inputs; it cannot prove those inputs (the claim verdicts
themselves) are semantically correct.
"""

from __future__ import annotations

from typing import Any

ASSESSABLE_VERDICTS = frozenset({"supported_correct", "unsupported", "contradicted"})


def derive_audit_status(evaluation: dict[str, Any]) -> dict[str, Any] | None:
    """Derive score-v1's audit_status purely from fields already present in a
    candidate-evaluation-v1 record (annotation_passes, adjudication), so this
    is never a second, independently-authored (and potentially inconsistent)
    piece of data. Returns None when no annotation_passes were recorded (e.g.
    a synthetic fixture authored directly, without a dual-audit workflow)."""
    passes = evaluation.get("annotation_passes") or []
    if not passes:
        return None
    actor_types = {entry["annotation_provenance"]["actor_type"] for entry in passes}
    actor_type = actor_types.pop() if len(actor_types) == 1 else "mixed"
    mode = "dual_audit" if len(passes) >= 2 else "provisional_single_audit"
    claims = evaluation.get("claims", [])
    adjudication = evaluation.get("adjudication")
    overridden = adjudication.get("overridden_claim_ids", []) if adjudication else []
    disagreement_count = len(overridden)
    disagreement_rate = (disagreement_count / len(claims)) if claims else 0.0
    adjudicator = None
    if adjudication:
        adjudicator = adjudication["annotation_provenance"]["actor"]["name"]
    return {
        "mode": mode,
        "actor_type": actor_type,
        "disagreement_count": disagreement_count,
        "disagreement_rate": disagreement_rate,
        "adjudicator": adjudicator,
    }


def _invalid_score(
    case_id: str,
    candidate_id: str,
    candidate_kind: str,
    invalid_candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "shiftory.benchmark-agent-quality-score/v1",
        "case_id": case_id,
        "candidate_id": candidate_id,
        "candidate_kind": candidate_kind,
        "explanation_sha256": None,
        "accounting": None,
        "claim_factuality": None,
        "unsupported_claims": None,
        "contradicted_claims": None,
        "required_behavior_coverage": None,
        "semantic_omissions": None,
        "uncertainty_honesty": None,
        "usefulness_relevance": None,
        "rubric_match_heuristic": None,
        "audit_status": None,
        "gate": None,
        "structural_failure": {
            "reason": invalid_candidate["reason"],
            "raw_response_sha256": invalid_candidate["raw_response_sha256"],
            "protocol_violation": invalid_candidate["protocol_violation"],
        },
    }


def _claim_factuality(claims: list[dict[str, Any]]) -> dict[str, Any]:
    assessable = [claim for claim in claims if claim["verdict"] in ASSESSABLE_VERDICTS]
    supported = [claim for claim in assessable if claim["verdict"] == "supported_correct"]
    supported_weight = sum(claim["materiality"] for claim in supported)
    assessable_weight = sum(claim["materiality"] for claim in assessable)
    return {
        "supported_weight": supported_weight,
        "assessable_weight": assessable_weight,
        "supported_count": len(supported),
        "assessable_count": len(assessable),
        "ratio": (supported_weight / assessable_weight) if assessable_weight else None,
    }


def _verdict_bucket(claims: list[dict[str, Any]], verdict: str) -> dict[str, Any]:
    matched = [claim for claim in claims if claim["verdict"] == verdict]
    return {
        "count": len(matched),
        "weight": sum(claim["materiality"] for claim in matched),
        "claim_ids": sorted(claim["claim_id"] for claim in matched),
    }


def _coverage(
    claims: list[dict[str, Any]], required_facts: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A required fact is satisfied only by a claim the auditor graded
    supported_correct. This is deliberately independent of the fact's
    truth_status: for a fact whose true answer is that the evidence cannot
    resolve it, the *correct* claim is an honest 'the evidence doesn't show
    this' statement, and once the auditor confirms that statement is itself
    true, it is graded supported_correct like any other correct claim.
    truth_status instead guides the auditor's confidence_appropriate judgment
    (see uncertainty_honesty), not which verdict counts as coverage -- an
    'ambiguous_unresolvable' *verdict* means the auditor could not decide how
    to grade the claim at all, which is a much rarer situation than a claim
    correctly describing a fact as ambiguous.
    """
    satisfied_ids: set[str] = set()
    for claim in claims:
        fact_id = claim.get("maps_to_required_fact_id")
        if not fact_id or fact_id not in required_facts:
            continue
        if claim["verdict"] == "supported_correct":
            satisfied_ids.add(fact_id)
    all_ids = sorted(required_facts)
    satisfied_sorted = sorted(satisfied_ids)
    missed_ids = [fact_id for fact_id in all_ids if fact_id not in satisfied_ids]
    coverage = {
        "satisfied_count": len(satisfied_sorted),
        "total_count": len(all_ids),
        "ratio": (len(satisfied_sorted) / len(all_ids)) if all_ids else None,
        "satisfied_ids": satisfied_sorted,
    }
    omissions = {
        "missed_count": len(missed_ids),
        "missed_weight": sum(required_facts[fact_id]["importance"] for fact_id in missed_ids),
        "missed_ids": missed_ids,
    }
    return coverage, omissions


def _uncertainty_honesty(claims: list[dict[str, Any]]) -> dict[str, Any]:
    violations = sorted(
        claim["claim_id"] for claim in claims if not claim["confidence_appropriate"]
    )
    return {
        "checked": len(claims),
        "violations": len(violations),
        "violation_ids": violations,
    }


def _usefulness_relevance(claims: list[dict[str, Any]], item_count: int | None) -> dict[str, Any]:
    useful = [
        claim
        for claim in claims
        if claim["verdict"] == "supported_correct" and claim.get("maps_to_required_fact_id")
    ]
    non_semantic = [claim for claim in claims if claim["verdict"] == "non_semantic"]
    ambiguous = [claim for claim in claims if claim["verdict"] == "ambiguous_unresolvable"]
    return {
        "useful_count": len(useful),
        "item_count": item_count if item_count is not None else 0,
        "non_semantic_claims": len(non_semantic),
        "ambiguous_unresolvable_claims": len(ambiguous),
    }


def _gate(
    unsupported_claims: dict[str, Any],
    contradicted_claims: dict[str, Any],
    semantic_omissions: dict[str, Any],
    uncertainty_honesty: dict[str, Any],
) -> dict[str, Any]:
    """A generic, case-independent property used only to unit-test this module's
    own arithmetic against the synthetic baseline/adversarial fixtures (Delta 7):
    a claim-perfect candidate has no unsupported/contradicted claims, no missed
    required facts, and no uncertainty-honesty violations. This is never applied
    to a captured_real_run candidate (see aggregate_score)."""
    reasons = []
    if unsupported_claims["count"]:
        reasons.append(f"{unsupported_claims['count']} unsupported claim(s)")
    if contradicted_claims["count"]:
        reasons.append(f"{contradicted_claims['count']} contradicted claim(s)")
    if semantic_omissions["missed_count"]:
        reasons.append(f"{semantic_omissions['missed_count']} missed required fact(s)")
    if uncertainty_honesty["violations"]:
        reasons.append(f"{uncertainty_honesty['violations']} uncertainty-honesty violation(s)")
    return {"pass": not reasons, "reasons": reasons}


def aggregate_score(
    *,
    case_id: str,
    candidate_id: str,
    candidate_kind: str,
    explanation_sha256: str | None,
    evaluation: dict[str, Any],
    rubric: dict[str, Any],
    accounting: dict[str, Any] | None = None,
    item_count: int | None = None,
    audit_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a full score-v1 document from an already-validated evaluation.

    Callers must run ``validation.validate_candidate_evaluation`` first; this
    function assumes the record is structurally sound and does no re-validation
    of its own (it is deliberately kept separate from, and free of, any schema
    or invariant checking so its arithmetic is easy to audit in isolation).
    ``audit_status`` is derived automatically from ``evaluation``'s own
    ``annotation_passes``/``adjudication`` fields via ``derive_audit_status``
    when not explicitly supplied, so it can never silently drift from the
    committed evaluation record.
    """
    invalid_candidate = evaluation.get("invalid_candidate")
    if invalid_candidate is not None:
        return _invalid_score(case_id, candidate_id, candidate_kind, invalid_candidate)

    claims: list[dict[str, Any]] = evaluation.get("claims", [])
    required_facts = {fact["id"]: fact for fact in rubric["required_facts"]}

    claim_factuality = _claim_factuality(claims)
    unsupported_claims = _verdict_bucket(claims, "unsupported")
    contradicted_claims = _verdict_bucket(claims, "contradicted")
    required_behavior_coverage, semantic_omissions = _coverage(claims, required_facts)
    uncertainty = _uncertainty_honesty(claims)
    usefulness_relevance = _usefulness_relevance(claims, item_count)

    gate = None
    if candidate_kind != "captured_real_run":
        gate = _gate(unsupported_claims, contradicted_claims, semantic_omissions, uncertainty)

    resolved_audit_status = audit_status
    if resolved_audit_status is None and candidate_kind == "captured_real_run":
        resolved_audit_status = derive_audit_status(evaluation)

    return {
        "schema": "shiftory.benchmark-agent-quality-score/v1",
        "case_id": case_id,
        "candidate_id": candidate_id,
        "candidate_kind": candidate_kind,
        "explanation_sha256": explanation_sha256,
        "accounting": accounting,
        "claim_factuality": claim_factuality,
        "unsupported_claims": unsupported_claims,
        "contradicted_claims": contradicted_claims,
        "required_behavior_coverage": required_behavior_coverage,
        "semantic_omissions": semantic_omissions,
        "uncertainty_honesty": uncertainty,
        "usefulness_relevance": usefulness_relevance,
        "rubric_match_heuristic": None,
        "audit_status": resolved_audit_status if candidate_kind == "captured_real_run" else None,
        "gate": gate,
        "structural_failure": None,
    }
