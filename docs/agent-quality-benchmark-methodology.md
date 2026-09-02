# Agent-quality benchmark methodology

## Status and scope

This benchmark measures the semantic quality of **real, agent-authored**
`shiftory.explanation/v1` documents against small, hand-audited fixtures. It
is a distinct layer from the deterministic accounting benchmark described in
[`benchmark-methodology.md`](benchmark-methodology.md): that benchmark proves
accounting (ownership, coverage, citation resolution, byte stability); this
one measures whether prose correctly, completely, and honestly explains
behavior, and it does so only for six small cases with real or hand-built
candidates, not as a general claim about Shiftory or any agent's overall
capability.

Everything in this document and the code it describes is subject to one
governing honesty rule, stated in
[`benchmarks/agent_quality/__init__.py`](../benchmarks/agent_quality/__init__.py):
automated code can prove that a claim points at real text in a real candidate
and that an auditor asserted their decomposition was complete. It cannot
prove that no semantic proposition was missed, and it never automates the
judgment of whether a claim is actually true.

## Architecture

```
benchmarks/agent_quality/
  schemas/            case, rubric, claim-record, candidate-evaluation,
                       agent-run, score, scores (published-suite), and
                       invalidated-capture schemas
  cases/<id>/         public case.json + offline git fast-import fixture +
                       synthetic baseline/adversarial explanations +
                       captured/<config>/ real agent output (when present)
  auditor/<id>/       rubric.json (required facts) + evaluations/*.json
                       (audited claim records for every candidate)
  invalidated/<id>/   withdrawn captures found to be contaminated after the
                       fact (e.g. an answer-key leak) -- preserved with full
                       provenance, never scored or published as official
                       results; see "Withdrawn captures" below
  validation.py       duplicate-key-rejecting loader, schema validation,
                       structural invariants, bounded caps
  aggregate.py        pure arithmetic: candidate-evaluation-v1 -> score-v1
  heuristic.py        bounded literal-alias matcher (separate, non-authoritative)
  fixtures.py         offline fast-import reconstruction (reuses benchmarks.runner)
  agent_harness.py    opt-in prompt-package isolation + capped capture
  runner.py           suite scoring + manual publish flow
```

See [`benchmarks/agent_quality/README.md`](../benchmarks/agent_quality/README.md)
for trust boundaries, the predeclared capture configurations, and the
case/rubric review workflow.

## Schemas

All are JSON Schema draft 2020-12, `additionalProperties: false`, versioned
`v1`, and loaded everywhere through a duplicate-key-rejecting loader
(`validation.load_json_strict`).

- **`case-v1`**: the public prompt-package content -- id, category,
  behavior-neutral description, fixture pointer. No expected claims.
- **`rubric-v1`**: `required_facts[]`, each with an `id`, auditor-only
  `description`, `importance` (1-5), `evidence_anchors`, and `truth_status`
  (`extractable` / `inferred_from_context` / `ambiguous_unresolvable`).
  `truth_status` guides the auditor's `confidence_appropriate` judgment on
  mapped claims; it does not switch which claim verdict counts as coverage
  (coverage always requires a `supported_correct`-graded claim -- see
  "Coverage and omissions" below). An optional bounded `heuristic_aliases`
  list feeds only the separate, non-authoritative heuristic section.
- **`claim-record-v1`**: one audited claim. `field`/`start`/`end`/
  `excerpt_sha256` anchor it to an exact substring of the real candidate,
  recomputed and checked by `validation.check_claim_anchor`. `verdict` is one
  of `supported_correct` / `unsupported` / `contradicted` /
  `ambiguous_unresolvable` (the auditor could not decide how to grade this
  specific claim at all -- a rare fallback, distinct from a claim that
  itself correctly and verifiably describes something as ambiguous, which is
  `supported_correct`) / `non_semantic`. `materiality` (1-5) weights it.
  `confidence_appropriate` compares the candidate's expressed confidence
  against what the auditor judges the evidence actually supports.
- **`candidate-evaluation-v1`**: the auditor's full annotation of one
  candidate. `claims[]` plus `audit_coverage[]` (one attestation per material
  field, each either listing its claim ids or explaining why no claim was
  warranted) prove the record is internally consistent; `annotation_passes[]`
  and `adjudication` record a dual-audit workflow when used.
  `invalid_candidate` (mutually exclusive with `claims`) captures a
  structural failure instead of pretending one didn't happen.
- **`agent-run-v1`**: full capture provenance, including
  `prompt_package_manifest` (per-file hashes of exactly what a capture's
  generator could see), `generator_access_profile`, and
  `isolation_method: "protocol"`.
- **`score-v1`**: the arithmetic aggregation described below, plus
  `rubric_match_heuristic` (separate, labeled non-authoritative),
  `audit_status`, and `gate` (present only for synthetic candidates -- see
  "Gating").
- **`scores-v1`**: one published document per case, an array of every
  candidate's `score-v1`.

## Exhaustiveness: what is, and is not, verifiable by code

Two structural invariants are checked by `validation.py` and enforced by
tests (`tests/benchmark/test_agent_quality_validation.py`):

1. Every material field (`title`/`statement`/`before`/`after`/`summary`) in a
   candidate has exactly one `audit_coverage` attestation, and every claim
   record's declared span, when re-extracted from the actual candidate text
   and hashed, matches its recorded `excerpt_sha256`.
2. `invalid_candidate` never co-occurs with `claims`/`audit_coverage`, and a
   usable evaluation always carries an `explanation_sha256`.

**What this proves**: claims point at real, exact text in the real
candidate, and the record is internally self-consistent. **What this does
not prove**: that the auditor actually noticed every proposition in the
text. A `decomposition_complete: true` attestation is the auditor's
assertion of completeness, not a machine-verified fact -- this limitation is
stated in the schema, in code comments, and here, deliberately, more than
once.

## Coverage and omissions (derived, not authored)

`missed_required_facts` is not a field an auditor fills in. It is derived by
`aggregate._coverage`: a required fact counts as satisfied only if at least
one claim graded `supported_correct` maps to it via
`maps_to_required_fact_id`. A required fact addressed only by an
`unsupported`/`contradicted`/`ambiguous_unresolvable`-graded claim is still
missed. This removes an entire class of auditor/derived-field drift that a
separately-authored omissions list would risk.

## The six semantic dimensions (never conflated with accounting)

- **`claim_factuality`**: `supported_correct` weight over all *assessable*
  claims (verdict in `{supported_correct, unsupported, contradicted}`) --
  `ambiguous_unresolvable`/`non_semantic` claims are excluded from this ratio
  and reported separately, not silently folded in.
- **`unsupported_claims`** / **`contradicted_claims`**: raw counts and
  materiality-weighted sums over **all** audited claims, mapped or not --
  this is where unanticipated, novel hallucinations are counted, because the
  auditor classifies every material claim in the candidate, not only
  phrases anticipated by the rubric.
- **`required_behavior_coverage`** / **`semantic_omissions`**: derived as
  above.
- **`uncertainty_honesty`**: `violations` = count of claims where the
  auditor set `confidence_appropriate: false`. This is never triggered by
  hedging on a genuinely ambiguous fact -- an honest "the evidence doesn't
  resolve this" claim, correctly graded `supported_correct`, is not a
  violation.
- **`usefulness_relevance`**: counts of claims that are both
  `supported_correct` and mapped to a required fact ("useful, correct,
  on-topic content"), plus separately reported `non_semantic_claims` and
  `ambiguous_unresolvable_claims` counts (informational, never penalized).

`accounting` (the existing `shiftory.explain.validator.ValidationResult`) and
`rubric_match_heuristic` remain separate top-level sections and are never
combined with the above into one score.

## Gating

Mandatory CI (`scripts/agent_quality_benchmark.py`, run as a step inside the
existing `benchmark-smoke` job -- this benchmark adds no new CI job, keeping
the total at 11) validates three things, none of which is a semantic
judgment:

1. **`validate`**: every committed case/rubric/candidate-evaluation JSON
   document is schema-valid and structurally consistent; every fixture
   reconstructs to its pinned commit SHAs and diff inventory.
2. **`regenerate-and-diff`**: every case's `scores-v1.json` is regenerated
   into a temporary directory and byte-compared against the committed
   snapshot under `docs/benchmarks/agent-quality/<case>/`. This script never
   writes into that directory itself; only the separate, manual
   `python -m benchmarks.agent_quality.runner publish` command does.
3. **`synthetic-discrimination`**: the aggregator gives every case's
   claim-perfect `synthetic_baseline` a passing `gate` and its deliberately
   defective `synthetic_adversarial` a failing one. This proves the
   **aggregator's arithmetic** responds correctly to a known-good vs.
   known-bad annotation pattern; it is not evidence about any real agent.

**A real captured agent's quality score is never a required gate.**
`gate` is always `null` for every `candidate_kind: captured_real_run`
candidate. Real-run scores are computed, snapshot-pinned for integrity, and
reported -- never turned into a pass/fail bar on Shiftory's correctness.

## Bounded execution

- Every JSON document this layer reads is loaded through a duplicate-key-
  rejecting loader with an explicit byte cap; every schema-described document
  additionally caps item/claim/fact counts and text-field lengths.
  `history.fast-import` and captured raw responses have their own byte caps.
- Case ids are validated against `^[a-z0-9][a-z0-9-]{0,63}$` and resolved
  through `validation.safe_case_dir`, which rejects any path or symlink
  escape from the intended base directory.
- Any subprocess this layer runs (`shiftory analyze`/`explain`, or an opt-in
  configured agent command) uses `shell=False`, an allow-listed environment
  (`agent_harness.build_allowlisted_env` -- names only, never a secret on
  argv), `start_new_session=True`, and streamed, incrementally-capped
  stdout/stderr reads (`agent_harness.run_capped_subprocess`) that never
  buffer the full output before enforcing the cap. Exceeding the timeout or
  byte cap kills the whole process group.

## Real captured-run results (all 12, unfiltered)

Every one of the six cases has two real captures from the two predeclared
configurations (`gpt-5.3-codex` and `gemini-3.7-flash`, see
[`benchmarks/agent_quality/README.md`](../benchmarks/agent_quality/README.md)).
All 12 currently official captures produced structurally valid,
schema-conformant JSON on the first attempt of their respective prompt
package version -- no timeouts, protocol violations, or structural failures
occurred in this round, which is itself reported honestly rather than
omitted. Every capture was dual-audited (two independent annotation passes
plus one adjudication pass); the table reports `required_behavior_coverage`
(satisfied out of 3 required facts), raw `unsupported_claims`/
`contradicted_claims` counts, `uncertainty_honesty` violations out of claims
checked, and the adjudication `disagreement_rate` between the two
independent passes, exactly as regenerated in
`docs/benchmarks/agent-quality/<case>/scores-v1.json`:

| Case | Candidate | Coverage | Unsupported | Contradicted | Uncertainty violations | Disagreement rate |
|---|---|---:|---:|---:|---:|---:|
| reordering-guard-clause | captured_config_a | 1/3 | 1 | 0 | 1/4 | 0.75 |
| reordering-guard-clause | captured_config_b | 1/3 | 0 | 0 | 0/4 | 0.0 |
| error-swallow-to-raise | captured_config_a | 1/3 | 0 | 0 | 0/3 | 0.0 |
| error-swallow-to-raise | captured_config_b | 1/3 | 0 | 0 | 0/3 | 0.0 |
| threshold-value-replacement | captured_config_a | 2/3 | 0 | 0 | 4/4 | 0.0 |
| threshold-value-replacement | captured_config_b | 2/3 | 0 | 0 | 0/7 | 0.0 |
| cross-file-validation-edit | captured_config_a | **3/3** | 0 | 0 | 4/9 | 0.44 |
| cross-file-validation-edit | captured_config_b | 2/3 | 0 | 0 | 8/9 | 0.33 |
| binary-asset-replacement | captured_config_a | 2/3 | 0 | 0 | 0/3 | 0.33 |
| binary-asset-replacement | captured_config_b | 1/3 | 0 | 0 | 0/4 | 0.0 |
| context-limited-helper-call | captured_config_a | 1/3 | 2 | 0 | 2/5 | 0.0 |
| context-limited-helper-call | captured_config_b | 2/3 | 0 | 0 | 2/6 | 0.33 |

Read honestly, not as a leaderboard. `cross-file-validation-edit`'s
`captured_config_a` is this benchmark's first real capture to reach 3/3
required-fact coverage -- but the adjudication record for that case is
explicit that this is a partial win, not an unqualified one: both
independent annotator passes flagged that the candidate never gives a
concrete counterexample string (e.g. `'a@'`, `'@@@'`) for the central,
central-importance "not a behavior-preserving move" fact, and its own
"relocated and simplified" framing arguably undersells that this is a real
behavior regression rather than a benign refactor; the fact was graded
satisfied on the strength of two independent passes reaching that
conclusion despite the hedge, not because the case for it is airtight. Its
sibling capture, `captured_config_b`, splits the same change into two
unconnected removal/addition items and is a clean, cross-validated
counterexample: neither of its two independent annotation passes mapped any
claim to that fact at all, so it scores 2/3 -- the same rubric, two
genuinely different real explanations of the identical diff, two
genuinely different, independently-audited outcomes. Every other capture
correctly identified the single most important (highest-importance)
required fact, but **omitted at least one lower-importance required fact**
(most often the genuinely-ambiguous "what does this imply, and what can't
be known" fact or the "what stayed the same" fact) -- both captured models
otherwise consistently produced a single, narrowly-scoped explanation item
rather than covering every required behavior. `threshold-value-replacement`'s
and `cross-file-validation-edit`'s `captured_config_a` runs also show
several audited claims' `confidence: "extracted"`/`"inferred"` judged mildly
miscalibrated (`confidence_appropriate: false`) for facts that were, in
fact, directly extractable from the diff, or that required more
interpretation than their stated confidence admitted -- independently
confirmed by two annotator passes each, not an adjudicator override -- a
real, reproducible finding that both under-hedging directly-visible facts
and over-stating interpretive synthesis as direct extraction are honesty
violations in this rubric's model. `context-limited-helper-call`'s captures
were regenerated after an initial `case.json`/`metadata.json` description
leaked this case's own ambiguity conclusion into the materialized prompt
package (found during independent review); the fresh, unleaked
`captured_config_a` capture correctly omits the ambiguity fact entirely
(coverage dropped from 2/3 to 1/3), while `captured_config_b` independently
produced an honest ambiguity acknowledgment on its own -- a concrete
demonstration of why leakage checks matter, not just a claim that they were
performed.

### Withdrawn captures: the `delete-add-not-a-rename` leak

A second, more serious leak was found by an independent review of the
previous revision's `delete-add-not-a-rename` case: its `category`
(`deletion-addition-confused-with-move`) and `description` ("a similarly
named, but behaviorally weaker, function is added") disclosed both of the
case's importance-5 rubric conclusions directly into the materialized
prompt package, and its fixture's own commit messages repeated the
answer-bearing id. One of the two original captures for that case used the
word "weaker" verbatim, matching the leak.

Both original captures were **withdrawn, not edited in place**: patching
only the description while keeping captures generated under the leaked
version would let contaminated evidence keep counting as if it were blind.
They are preserved byte-for-byte, with full agent-run provenance, their
original candidate-evaluation-v1 records, and their original score-v1
records, under
[`benchmarks/agent_quality/invalidated/cross-file-validation-edit/`](../benchmarks/agent_quality/invalidated/cross-file-validation-edit/)
as `invalidated-answer-leak-v1` records (see
`schemas/invalidated-capture-v1.schema.json`); `validation.py`'s
`validate_invalidated_capture` recomputes every referenced artifact's
sha256 against the archive to prove it is an honest, unmodified copy of
what was withdrawn. This archive is deliberately outside `cases/` and
`auditor/` so it can never be accidentally enumerated, scored, or published
by `runner.py` as an official result -- `case_ids()`/`captured_candidates()`
only ever look inside `cases/<id>/captured/`, which the withdrawn files are
not part of.

The case was renamed to `cross-file-validation-edit`, its `category` and
`description` rewritten to state only directly-visible, mechanical facts (a
function name, the two files involved, deletion vs. addition -- nothing
about behavior, strength, or whether this resembles a move), and its
fixture's commit messages changed to match (which changes the fixture's
pinned base/head commit SHAs in `metadata.json`, since fast-import commit
identity includes the message). **Both captures were then redone from
scratch**, using two entirely fresh sub-agent invocations against the
corrected package -- see the results table above for the outcome. Every
one of the six cases' materialized prompt packages was manually re-read
against its own rubric for this same class of leak as part of this
revision's independent review; no further leaks were found, though see
"Limitations" below for what that check can and cannot establish.

## Limitations

- Six cases is a small, deliberately curated set, not a representative
  sample of all possible code changes.
- Every annotation and adjudication pass performed so far is agent-vs-agent;
  none of it is verified human ground truth (see
  [`benchmarks/agent_quality/README.md`](../benchmarks/agent_quality/README.md)).
- For every case's `captured_config_a` candidate, one of its two independent
  annotation passes uses the same model (`gpt-5.3-codex`) that generated the
  candidate -- a genuine self-assessment overlap, disclosed in full in
  [`benchmarks/agent_quality/README.md`](../benchmarks/agent_quality/README.md#dual-audit-annotation-workflow)
  rather than hidden. `captured_config_b` is unaffected.
- All 12 currently-official captures happened to succeed structurally; the
  harness and schemas support recording timeouts/protocol
  violations/structural failures as `invalid_candidate` records, but this
  benchmark has not yet observed one in practice, so that code path is
  currently exercised only by unit tests, not a real capture.
- Answer-key leakage into a case's own `category`/`description` -- the
  defect found and fixed twice now in this benchmark's short history (once
  for `context-limited-helper-call`, once for `delete-add-not-a-rename`) --
  is fundamentally a semantic judgment call that automated string matching
  can only partially catch. `test_agent_quality_harness.py` pins every
  case's currently-reviewed category/description to an explicit allowlist
  (so a future silent edit fails CI) and checks for exact-sentence rubric
  copies and the specific previously-leaked phrases, but it cannot prove a
  *new* case's public fields are free of a *novel* paraphrased leak --
  that requires a human/agent to actually read the case against its rubric
  and judge inferability, which is what the independent review rounds in
  this benchmark's history have done and what any future case addition
  must repeat.
- The literal-alias heuristic is a labeled, non-authoritative aid with real
  false-positive/false-negative rates (see `heuristic.py`'s module
  docstring); it is never a substitute for audited claim verdicts.
