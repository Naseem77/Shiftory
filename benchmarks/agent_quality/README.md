# Agent-quality benchmark

This directory scores **real, agent-authored** `explanation.json` documents
against hand-audited semantic rubrics. It is separate from, and never modifies,
the deterministic accounting benchmark in [`../runner.py`](../runner.py) (see
[`../README.md`](../README.md) and
[the benchmark methodology](../../docs/benchmark-methodology.md)). That
benchmark proves accounting: ownership, coverage, citation resolution, byte
stability. It does not prove that prose correctly explains behavior. This
layer exists to measure that separately, honestly, and without pretending
automation can do more than it can.

See [`docs/agent-quality-benchmark-methodology.md`](../../docs/agent-quality-benchmark-methodology.md)
for the full normative methodology (schemas, scoring rules, CI gating scope).
This README covers scope, trust boundaries, and the case/rubric-review
workflow.

## What is, and is not, automated

- **Automated and required by CI**: schema validation, structural invariants
  (excerpt-hash anchoring, audit-coverage consistency, invalid-candidate
  exclusivity), fixture reconstruction against pinned commit SHAs, snapshot
  reproducibility, and the aggregator's arithmetic against synthetic fixtures.
  None of this is a semantic judgment.
- **Never automated**: whether a claim in a real candidate explanation is
  actually correct, unsupported, or contradicted. That is a
  `candidate-evaluation-v1` record, produced by a human or agent auditor
  decomposing the candidate's claims and grading each one against the
  rubric's required facts. `benchmarks/agent_quality/aggregate.py` only sums
  and weights those already-audited verdicts; it never re-derives them.
- **A labeled, non-authoritative aid**: `benchmarks/agent_quality/heuristic.py`
  performs a bounded, literal-only (no regex) substring match against a
  rubric's optional `heuristic_aliases`. It produces a separate
  `rubric_match_heuristic` section on every `score-v1` document and never
  feeds `claim_factuality`, `unsupported_claims`, `contradicted_claims`,
  `required_behavior_coverage`, `semantic_omissions`, or `uncertainty_honesty`.
  It has real, documented false-positive and false-negative rates (see its
  module docstring) and must never be read as evidence of correctness on its
  own.

## Trust boundaries (read this before generating or trusting a capture)

- **The rubric is not secret.** It lives in this same public, open-source
  repository as everything else (`auditor/<case>/rubric.json`). It is
  **withheld from the materialized agent prompt package** (see
  `agent_harness.prepare_prompt_package`, which copies only `case.json`, a
  real reconstructed git repository, and the bundled `SKILL.md`) -- it is not
  hidden from a determined reader of this repository.
- **Isolation is by protocol, not by sandbox.** If the agent generating a
  capture has general filesystem/shell/network access -- which every capture
  in this repository so far does, since it runs as a Copilot CLI sub-agent in
  the same environment as the orchestrating session -- nothing here prevents
  it from reading the rubric directly. Every `agent-run-v1` record has a
  `generator_access_profile` and `isolation_method: "protocol"` stating this
  plainly, so readers can judge how much to trust a given capture's blindness.
- **`captured/*` candidates are real, unedited agent output.** A raw response
  is preserved as `raw-response.txt`/`.bin` and only promoted to
  `explanation.json` when it is, byte-for-byte, exactly one valid JSON
  document structurally resembling `shiftory.explanation/v1` -- no
  Markdown-fence stripping, no repair. A response that fails this protocol is
  recorded as an `invalid_candidate`, not discarded or fixed up.
- **`synthetic/baseline` and `synthetic/adversarial` are hand-authored scorer
  fixtures, not agent output.** They exist only to unit-test
  `aggregate.py`'s arithmetic (a claim-perfect candidate passes the generic
  gate; a deliberately defective one does not) and are always labeled
  `synthetic_baseline`/`synthetic_adversarial`. They are never presented as
  evidence about how a real agent performs, and `gate` is always `null` for
  every `captured_real_run` candidate -- see
  ["Gating" in the methodology doc](../../docs/agent-quality-benchmark-methodology.md#gating).

## Predeclared capture configurations

Every real capture in this repository uses one of these two predeclared
configurations (written down here before any capture was generated, per the
no-cherry-picking commitment below):

- **Configuration A**: a fresh, stateless GitHub Copilot CLI sub-agent
  (`task` tool, `agent_type=general-purpose`, model `gpt-5.3-codex`), given
  only the materialized prompt-package directory and instructed to run
  `shiftory explain --graphora off` against the fixture repository, follow
  `SKILL.md`, and write its final verified explanation verbatim to
  `RAW_RESPONSE`.
- **Configuration B**: the same protocol, model `gemini-3.7-flash`.

Both configurations are invoked fresh (no shared context, no access to any
other capture's output or to this rubric) for every case a capture is
produced for. Whatever each configuration produces -- including a tie, a
structural failure, or both configurations performing equally well or poorly
-- is committed unedited. There is no discard-and-retry for a "better"
result.

## Dual-audit annotation workflow

Every `captured_real_run` candidate's `candidate-evaluation-v1` record is
built from **two independent annotation passes** (fresh agent invocations,
each given only the rubric and the exact captured candidate text, with no
visibility into the other pass's output) followed by one **adjudication
pass** that reconciles any disagreement and records its rationale. The
resulting `score-v1.audit_status` is derived automatically from the
evaluation's own `annotation_passes`/`adjudication` fields
(`aggregate.derive_audit_status`), so it can never silently drift from what
was actually recorded. When only one pass is feasible, the record is marked
`audit_status.mode: "provisional_single_audit"` honestly rather than treated
as final.

**Every annotation and adjudication pass performed so far is agent-vs-agent,
not human ground truth.** `annotation_provenance.actor_type` is always
`"agent"` in this repository today; nothing here should be read as verified
by a human domain expert. A maintainer can promote a case to human-reviewed
by replacing the relevant `annotation_provenance` entries with `actor_type:
"human"`, re-adjudicating, and bumping the rubric's `version`.

## Case/rubric review workflow

1. A new case's fixture (`history.fast-import`, `metadata.json`, `case.json`)
   is authored offline, matching the pattern in
   [`../fixtures/offline-smoke`](../fixtures/offline-smoke): fixed
   author/committer timestamps for deterministic commit SHAs, verified by
   actually running `git fast-import` and recording the real resulting SHAs
   and diff inventory (never fabricated).
2. `auditor/<case>/rubric.json` is authored separately, listing required
   facts with an evidence anchor, importance, and `truth_status` for each.
   `rubric_provenance` records who authored it and its review status --
   today, authored and not yet independently re-reviewed; treat every rubric
   in this repository as provisional until a second reviewer signs off.
3. Synthetic `baseline`/`adversarial` explanations and their
   `candidate-evaluation-v1` records are hand-authored to unit-test the
   scorer: a claim-perfect candidate and a deliberately defective one (at
   least one hallucination, one omission, one overconfident claim on a
   genuinely ambiguous fact, and one low-content padding item), always
   validated against the real `shiftory.explain.validator` before being
   committed.
4. Any change to a rubric's required facts requires bumping its `version`
   and re-validating every existing `candidate-evaluation-v1` record for that
   case against the new version (the runner's `rubric_version` field on each
   evaluation makes a version mismatch structurally checkable).

## Running locally

```console
python -m benchmarks.agent_quality.runner suite      # score every case, print results
python -m benchmarks.agent_quality.runner publish    # write docs/benchmarks/agent-quality snapshots (manual only)
python scripts/agent_quality_benchmark.py            # what required CI runs: validate, regenerate-and-diff, synthetic-discrimination
```

`agent_harness.py` (`prepare_prompt_package`, `capture_result`,
`run_capped_subprocess`) is the opt-in capture tool described above. It is
never invoked by required CI.
