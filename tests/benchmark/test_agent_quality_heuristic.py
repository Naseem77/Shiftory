"""Tests for the bounded, literal-only rubric-match heuristic.

Asserts both its matching behavior and -- just as importantly -- that it is
truly isolated from the headline aggregate.py sections (Delta 2 / point 2).
"""

from __future__ import annotations

import re

from benchmarks.agent_quality import aggregate, heuristic


def _fact(fact_id: str, aliases: list[str] | None = None) -> dict[str, object]:
    fact: dict[str, object] = {
        "id": fact_id,
        "description": "d",
        "importance": 3,
        "evidence_anchors": [],
        "truth_status": "extractable",
    }
    if aliases is not None:
        fact["heuristic_aliases"] = aliases
    return fact


def test_module_never_imports_re() -> None:
    import benchmarks.agent_quality.heuristic as module

    source = module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "import re" not in text
    assert "re.compile" not in text
    assert "re.search" not in text
    assert "re.match" not in text


def test_hit_and_miss_counts() -> None:
    explanation = {
        "summary": "The guard clause now runs after validation.",
        "items": [{"title": "t", "statement": "s", "before": "b", "after": "a"}],
    }
    rubric = {
        "required_facts": [
            _fact("f1", aliases=["guard clause"]),
            _fact("f2", aliases=["timeout increased"]),
            _fact("f3"),  # no aliases -> not counted as hit or miss
        ]
    }
    result = heuristic.compute_heuristic(explanation, rubric)
    assert result["hits"] == 1
    assert result["misses"] == 1
    assert result["matched_fact_ids"] == ["f1"]
    assert result["caveat"] == heuristic.HEURISTIC_CAVEAT


def test_matching_is_case_and_whitespace_insensitive_literal_containment() -> None:
    explanation = {"summary": "GUARD   CLAUSE moved.", "items": []}
    rubric = {"required_facts": [_fact("f1", aliases=["guard clause"])]}
    result = heuristic.compute_heuristic(explanation, rubric)
    assert result["hits"] == 1


def test_no_aliases_means_fact_is_neither_hit_nor_miss() -> None:
    explanation = {"summary": "irrelevant text", "items": []}
    rubric = {"required_facts": [_fact("f1")]}
    result = heuristic.compute_heuristic(explanation, rubric)
    assert result["hits"] == 0
    assert result["misses"] == 0


def test_heuristic_never_feeds_aggregate_headline_sections() -> None:
    """aggregate.aggregate_score never calls heuristic.compute_heuristic and
    always emits rubric_match_heuristic: null; callers attach it separately."""
    claims = [
        {
            "claim_id": "a",
            "source_item_id": "x",
            "field": "/items/0/statement",
            "start": 0,
            "end": 1,
            "excerpt_sha256": "0" * 64,
            "overlaps_with": [],
            "maps_to_required_fact_id": None,
            "verdict": "supported_correct",
            "materiality": 1,
            "evidence_anchor_cited": [],
            "confidence_expressed": "extracted",
            "confidence_appropriate": True,
            "rationale": "r",
            "annotation_provenance": {
                "actor_type": "agent",
                "actor": {"name": "t"},
                "method": "m",
                "timestamp": "2024-01-01T00:00:00Z",
            },
        }
    ]
    score = aggregate.aggregate_score(
        case_id="demo",
        candidate_id="cand",
        candidate_kind="synthetic_baseline",
        explanation_sha256="a" * 64,
        evaluation={"claims": claims},
        rubric={"required_facts": []},
    )
    assert score["rubric_match_heuristic"] is None

    aggregate_source = open(aggregate.__file__, encoding="utf-8").read()  # noqa: SIM115
    assert "import heuristic" not in aggregate_source
    assert "compute_heuristic" not in aggregate_source
    assert "from benchmarks.agent_quality import heuristic" not in aggregate_source


def test_false_positive_limitation_repeating_alias_without_asserting_behavior() -> None:
    """Documents a known false-positive: merely repeating an alias phrase
    'hits' even though the surrounding text asserts nothing meaningful."""
    explanation = {"summary": "guard clause guard clause guard clause", "items": []}
    rubric = {"required_facts": [_fact("f1", aliases=["guard clause"])]}
    result = heuristic.compute_heuristic(explanation, rubric)
    assert result["hits"] == 1  # hits despite asserting nothing coherent


def test_false_negative_limitation_paraphrase_is_invisible() -> None:
    """Documents a known false-negative: a correct paraphrase using different
    wording than the alias list is invisible to this heuristic."""
    explanation = {
        "summary": "The early-return check was relocated to follow the lookup.",
        "items": [],
    }
    rubric = {"required_facts": [_fact("f1", aliases=["guard clause moved"])]}
    result = heuristic.compute_heuristic(explanation, rubric)
    assert result["hits"] == 0
    assert re.search("relocated", explanation["summary"])  # the paraphrase is real, just missed
