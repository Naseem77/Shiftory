"""Agent-written explanation quality benchmark layer.

This package is deliberately separate from the existing accounting benchmark in
``benchmarks/runner.py``. It scores *real, agent-authored* ``explanation.json``
documents against hand-audited semantic rubrics.

Read ``benchmarks/agent_quality/README.md`` first: it states the trust boundaries
and honesty limitations that apply to every module here. In short:

- Correctness/hallucination/omission/uncertainty results come from **human- or
  agent-performed claim-level annotation** (``candidate-evaluation-v1`` records),
  never from automated text matching. Automated checks only verify that claims
  point at real text and that an auditor asserted their decomposition was
  complete; they cannot verify that no proposition was missed.
- The optional lexical ``rubric_match_heuristic`` section is a clearly labeled,
  non-authoritative aid and never feeds the headline scores.
- Nothing in this package is a required product-correctness gate. Mandatory CI
  validates schemas, structural invariants, fixture integrity, and snapshot
  reproducibility; it never gates on a real captured agent's quality score.
"""

from __future__ import annotations
