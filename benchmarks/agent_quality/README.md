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
  it from reading the rubric directly. Every `agent-run-v2` record has a
  `generator_access_profile` and `isolation_method: "protocol"` stating this
  plainly, so readers can judge how much to trust a given capture's blindness.
- **Generation timing is honestly bounded, not fabricated.** `agent-run-v2`'s
  `invocation` is a tagged union: for a `copilot_task` invocation (every
  capture in this repository today), this harness never controlled or
  streamed the generating sub-agent, so real generation start/end time is
  `null` with an explicit reason unless genuinely obtained -- never
  substituted with `capture_ingested_at_utc` (when this harness read the
  already-completed `RAW_RESPONSE`), which is separately and honestly
  labeled. This replaces `agent-run-v1`'s `started_at_utc`/`finished_at_utc`,
  which were, for every capture here, only ever microseconds apart -- proof
  they were bookkeeping time, not generation time. See
  ["Provenance" in the methodology doc](../../docs/agent-quality-benchmark-methodology.md#provenance-engine-identity-and-protocol-commit).
- **`orchestrator_agent_handle` is a traceability label, never provider
  attestation.** Every `copilot_task` capture records the caller-supplied
  name given to the orchestrating session's own sub-agent-launch tool at
  invocation time (e.g. `freeze-cap-reorder-a`), unique across all 12
  official captures. Its schema hard-codes `externally_verifiable: false`:
  this string is chosen by the orchestrator itself before the call, so it
  helps this benchmark's own bookkeeping distinguish invocations during
  review, but it is not, and cannot be treated as, a model-provider- or
  platform-issued proof that a given invocation was distinct or that no
  retry occurred. That guarantee is instead a **declared protocol
  attestation** in `protocol_registry.json`'s `invocation_protocol`
  (`one_attempt_only`/`no_retry`/`no_repair`, etc.) -- a commitment this
  benchmark's process follows, not something any field cryptographically
  proves after the fact.
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
- **A capture generated under a leaked prompt package, or generated before
  its governing protocol was committed, is withdrawn, never edited in
  place.** If `case.json`'s `category`/`description` are later found to have
  disclosed a rubric conclusion (this has happened twice in this
  benchmark's short history), every capture made under that leaked version
  is contaminated, regardless of whether its own wording happens to echo
  the leak. Separately, if a capture's `benchmark_protocol_commit` did not
  exist **before** that capture's own generation (this has also happened,
  for six captures across three cases -- see
  ["Protocol freeze and recapture" in the methodology doc](../../docs/agent-quality-benchmark-methodology.md#protocol-freeze-and-recapture)),
  it is withdrawn for that reason instead, even if its content matches the
  now-committed protocol byte-for-byte. Either way the withdrawn capture is
  moved to `invalidated/<case>/` (nested under a distinctly-named
  subdirectory, e.g. `protocol-not-precommitted/`, whenever a case has more
  than one withdrawal so the two archive groups can never collide or
  overwrite each other) as an `invalidated-answer-leak-v1` or
  `invalidated-protocol-not-precommitted-v1` record (the latter carrying an
  explicit `reason_code`) with hash-verified pointers to its original raw
  response, agent-run provenance, evaluation, and score (see
  `validation.py`'s `validate_invalidated_capture`), and a genuinely fresh
  capture is made under the corrected/frozen package. `invalidated/` is
  outside `cases/` and `auditor/` specifically so `runner.py`'s
  `case_ids()`/`captured_candidates()` can never accidentally enumerate,
  score, or publish it as an official result. This benchmark currently
  carries fourteen such archived captures in total across four distinct
  reason codes (two answer-leak; six protocol-not-precommitted for
  reordering-guard-clause/context-limited-helper-call/cross-file-validation-edit's
  first replacement pair; six more, under a fourth reason code
  `protocol-config-not-precommitted`, for error-swallow-to-raise/
  threshold-value-replacement/binary-asset-replacement once the
  protocol-commit verifier was strengthened to also check the committed
  config registry, not just prompt bytes -- see "Protocol freeze and
  recapture" in the methodology doc);
  `test_exactly_fourteen_archived_captures_with_distinct_reasons` pins this
  count and distribution in CI. All 12 currently-official captures now
  reference the same protocol-freeze commit
  (`test_all_official_captures_bind_to_the_frozen_commit_with_unique_handles`).

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

`protocol_registry.json` (schema `protocol-registry-v1`) is the
machine-readable, committed freeze of exactly this: both configurations'
provider/model/agent-type/tool/invocation-kind fields, the sha256 of the
`INSTRUCTIONS` prompt text and bundled `SKILL.md` at freeze time, the
invocation protocol's non-negotiable invariants (one attempt only, no
retry, no repair, no fence-stripping), and a `case_revisions` map pinning
the exact `case.json` version each of `reordering-guard-clause`,
`context-limited-helper-call`, and `cross-file-validation-edit` must be at.
It exists specifically so that "this protocol was fixed before generation,
not selected afterward to match a result" is independently verifiable by a
third party from Git history alone, not merely asserted -- see "Protocol
freeze and recapture" in the methodology doc for why this was added on top
of (not instead of) the pre-existing full-manifest content-equality proof.

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

**Known self-assessment overlap.** For every case's `captured_config_a`
candidate (generated by `gpt-5.3-codex`), one of its two independent
annotation passes is also performed by `gpt-5.3-codex`. This is a real
conflict-of-interest limitation, not merely a hypothetical one: the same
model that authored a candidate also grades it in one of the two passes for
that candidate. It does not affect `captured_config_b` (generated by
`gemini-3.7-flash`, graded once by `claude-sonnet-5` and once by
`gpt-5.3-codex`, neither of which produced it). Mitigating this fully would
require a third, disjoint annotator model for every `config-a` grading pass;
until that is done, treat `captured_config_a` results as carrying this
additional, disclosed limitation on top of the general agent-vs-agent
caveat above.

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
3. **Before any real capture is generated**, a reviewer manually reads
   `case.json`'s `category` and `description` -- the only two case fields
   that are ever materialized into the prompt package -- against the full
   rubric and asks: could an agent infer any required fact's conclusion, even
   paraphrased, from these two fields alone plus commit messages in
   `history.fast-import` (which are also visible to a generating agent via
   `git log`)? This benchmark has already shipped two leaks past its
   automated checks that this manual step is meant to catch (see
   ["Withdrawn captures"](../../docs/agent-quality-benchmark-methodology.md#withdrawn-captures-the-delete-add-not-a-rename-leak)
   in the methodology doc) -- `test_prepare_never_copies_auditor_content` and
   the reviewed-neutral-field allowlist in
   `tests/benchmark/test_agent_quality_harness.py` catch verbatim rubric-text
   copies and regressions of specific known-bad phrases, but neither can
   prove a new case's fields are free of a *novel* paraphrase. If a capture
   is later found to have been generated under a leaked package, it must be
   withdrawn (see below), never edited in place while keeping the
   contaminated capture.
4. Synthetic `baseline`/`adversarial` explanations and their
   `candidate-evaluation-v1` records are hand-authored to unit-test the
   scorer: a claim-perfect candidate and a deliberately defective one (at
   least one hallucination, one omission, one overconfident claim on a
   genuinely ambiguous fact, and one low-content padding item), always
   validated against the real `shiftory.explain.validator` before being
   committed.
5. Any change to a rubric's required facts requires bumping its `version`
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
