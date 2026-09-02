"""Bounded, literal-only, non-authoritative rubric-match heuristic.

This module never imports ``re``. It performs plain, case-insensitive,
whitespace-normalized substring containment checks against a rubric's
``heuristic_aliases`` -- nothing more. It exists only to produce the separate,
clearly labeled ``rubric_match_heuristic`` section of a score-v1 document (see
``aggregate.py``, which never reads this module's output when computing
``claim_factuality``, ``unsupported_claims``, ``contradicted_claims``,
``required_behavior_coverage``, ``semantic_omissions``, or
``uncertainty_honesty``).

Known limitations, stated here because this heuristic is easy to misread as
more rigorous than it is:

- **False negatives**: paraphrases, synonyms, negation, and any wording an
  alias author didn't anticipate are invisible to it.
- **False positives**: an item that merely repeats an alias phrase "hits" even
  if it asserts something false or irrelevant around it.
- It says nothing about whether a matched or unmatched fact is actually true.
"""

from __future__ import annotations

from typing import Any


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


HEURISTIC_CAVEAT = (
    "Non-authoritative literal-alias heuristic; not evidence of semantic "
    "correctness and never used to compute the sections above."
)


def compute_heuristic(explanation: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    haystacks: list[str] = []
    summary = explanation.get("summary")
    if isinstance(summary, str):
        haystacks.append(_normalize(summary))
    for item in explanation.get("items", []):
        if not isinstance(item, dict):
            continue
        for field in ("title", "statement", "before", "after"):
            value = item.get(field)
            if isinstance(value, str):
                haystacks.append(_normalize(value))
    combined = " \n ".join(haystacks)

    matched_fact_ids: list[str] = []
    hits = 0
    misses = 0
    for fact in rubric.get("required_facts", []):
        aliases = fact.get("heuristic_aliases") or []
        if not aliases:
            continue
        found = any(_normalize(alias) in combined for alias in aliases)
        if found:
            hits += 1
            matched_fact_ids.append(fact["id"])
        else:
            misses += 1

    return {
        "caveat": HEURISTIC_CAVEAT,
        "hits": hits,
        "misses": misses,
        "matched_fact_ids": matched_fact_ids,
    }
