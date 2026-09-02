# Agent-quality benchmark methodology

## Status and scope

This benchmark measures the semantic quality of **real, agent-authored**
`shiftory.explanation/v1` documents against small, hand-audited fixtures. It
is a distinct layer from the deterministic accounting benchmark described in
[`benchmark-methodology.md`](benchmark-methodology.md): that benchmark proves
accounting (ownership, coverage, citation resolution, byte stability); this
one **aggregates agent-audited claim labels** against hand-authored rubrics
for six small cases with real or hand-built candidates -- it does not itself
prove that a candidate's prose is semantically true, and it is not a general
claim about Shiftory or any agent's overall capability. Whether a specific
claim is actually correct remains a human-or-agent judgment call recorded in
a `candidate-evaluation-v1`; this benchmark's code sums and reports those
judgments honestly, it does not verify them against reality on its own.

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
                       agent-run, score, scores (published-suite),
                       invalidated-capture, invalidated-generation-attempt,
                       and protocol-registry schemas
  protocol_registry.json  committed freeze of the predeclared capture
                       configurations, prompt/skill digests, invocation
                       protocol (including directory-exclusivity
                       invariants), and required case.json revisions -- see
                       "Protocol freeze and recapture" below
  cases/<id>/         public case.json + offline git fast-import fixture +
                       synthetic baseline/adversarial explanations +
                       captured/<config>/ real agent output (when present)
  auditor/<id>/       rubric.json (required facts) + evaluations/*.json
                       (audited claim records for every candidate)
  invalidated/<id>/   withdrawn captures found to be contaminated (e.g. an
                       answer-key leak), generated before their governing
                       protocol commit existed, or that were themselves
                       second invocations after a directory-collision
                       defect -- preserved with full provenance, never
                       scored or published as official results; also
                       carries lost-generation-attempts/ incident records
                       (no recoverable bytes at all) for the six invocations
                       actually lost to that collision; see "Withdrawn
                       captures" below
  validation.py       duplicate-key-rejecting loader, schema validation,
                       structural invariants, bounded caps
  aggregate.py        pure arithmetic: candidate-evaluation-v1 -> score-v1
  heuristic.py        bounded literal-alias matcher (separate, non-authoritative)
  fixtures.py         offline fast-import reconstruction (reuses benchmarks.runner)
  agent_harness.py    opt-in prompt-package isolation + capped capture +
                       protocol-commit/precommitment verification +
                       exclusive prompt/output directory claiming
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
- **`agent-run-v1`** (superseded by `agent-run-v2`; preserved only in
  withdrawn-capture archives, see
  ["Withdrawn captures"](#withdrawn-captures-the-delete-add-not-a-rename-leak)):
  the original capture provenance schema. Its `started_at_utc`/
  `finished_at_utc` conflated capture_result's own post-hoc bookkeeping time
  with real generation timing -- see `agent-run-v2` below.
- **`agent-run-v2`**: full capture provenance, including
  `prompt_package_manifest` (per-file hashes of exactly what a capture's
  generator could see), `generator_access_profile`, and
  `isolation_method: "protocol"`. Adds a tagged `invocation`
  (`local_process`/`copilot_task`) so command-argv and generation-timing
  fields only ever describe what was genuinely observed for that invocation
  kind, a separately labeled `capture_ingested_at_utc` (when this harness
  actually read `RAW_RESPONSE`, distinct from generation time), and
  `engine_identity`/`benchmark_protocol_commit` -- see
  ["Provenance: engine identity and protocol commit"](#provenance-engine-identity-and-protocol-commit).
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

## Provenance: engine identity and protocol commit

`agent-run-v2`'s `invocation` field is a tagged union distinguishing two
fundamentally different situations, so no record can imply more precision
than was actually observed:

- **`local_process`**: `agent_harness.capture_result` itself started and
  waited on a subprocess (`agent_harness.run_capped_subprocess`). Real
  generation timing, the actual non-empty `command_argv`, and stdout/stderr
  hashes are all directly observed and required.
- **`copilot_task`**: the generating agent ran to completion as a Copilot CLI
  task sub-agent in a separate conversation this harness never controlled or
  streamed output from, and left `RAW_RESPONSE` behind for `capture_result`
  to read afterward. Every capture in this repository today is this kind.
  `generation_started_at_utc`/`generation_finished_at_utc` are only ever set
  from a value the caller genuinely obtained from that sub-agent; when
  unavailable (true for every capture in this repository so far) both are
  `null` and `generation_timing_unavailable_reason` says so explicitly.
  `capture_ingested_at_utc` -- always present, always distinct in meaning --
  is when `capture_result` actually read `RAW_RESPONSE`. This is a real fix,
  not a cosmetic rename: `agent-run-v1`'s `started_at_utc`/`finished_at_utc`
  were, for every `copilot_task` capture in this repository, only ever
  **microseconds apart** (confirmed by an independent review of this exact
  chronology), because they were set around that same post-hoc bookkeeping
  read, never around the actual generation, which took tens of seconds to
  minutes. v1 presented that as run provenance; v2 does not.

  `copilot_task`'s `orchestrator_agent_handle` records the caller-supplied
  name given to the orchestrating session's own sub-agent-launch tool when
  a capture's generating agent was invoked (e.g. `freeze-cap-reorder-a`),
  echoed back by that tool's own session/agent registry as a runtime
  handle. This is deliberately **not** presented as provider- or
  platform-issued attestation: `externally_verifiable` is a hard
  `const: false`, because the orchestrator itself chooses this string
  before the call -- it aids this benchmark's own traceability (e.g.
  confirming during review which sub-agent produced which raw response)
  but cannot, on its own, prove that a given invocation was distinct from
  another or that no retry occurred. Every official capture's handle is
  required to be non-null and unique across all 12
  (`test_all_official_captures_bind_to_the_frozen_commit_with_unique_handles`);
  a capture's actual no-retry/one-attempt-only guarantee is instead a
  **declared protocol attestation** in `protocol_registry.json`'s
  `invocation_protocol` (see below), not something this field cryptographically
  proves.

Two further fields separate two facts v1 conflated into one ambiguous
`shiftory_commit`:

- **`engine_identity`**: what ran `shiftory analyze`/`explain` to produce the
  evidence a capture is based on -- `verification_method` states exactly how
  `value` was established (`git_commit`, `source_tree_digest`, or `unknown`),
  never fabricated.
- **`benchmark_protocol_commit`**: the repository commit whose committed
  files define the exact protocol a capture was generated under:
  `case.json`, `metadata.json`, `history.fast-import`, the bundled
  `SKILL.md`, `agent_harness.py`'s `INSTRUCTIONS` text, **and**
  `protocol_registry.json` (the predeclared config-a/config-b registry,
  prompt/skill digests, invocation-protocol invariants, and case-revision
  pins -- see "Protocol freeze and recapture" below). `verified: true` on
  the record is only ever a hint written when the record was authored -- it
  is never trusted on its own.
  `agent_harness.recompute_benchmark_protocol_commit_verification`
  independently recomputes it every time (including in
  `test_every_case_has_exactly_the_two_predeclared_real_captures`, so a
  regression is caught by CI, not just a one-time manual check), and this
  check now has two independent halves that BOTH must hold:
  1. **Full prompt-package content-equality**:
     `agent_harness.reconstruct_full_prompt_manifest_at_commit` reconstructs
     the **entire** prompt package -- not just `case.json` -- using only
     files as committed at that commit (`case.json` and `metadata.json`/
     `history.fast-import` via `git show`, `SKILL.md` via `git show` against
     its committed path, and the harness's own `INSTRUCTIONS` constant parsed
     out of that commit's `agent_harness.py` source with `ast`, never
     executed), and compares the resulting manifest to the same capture's
     own recorded `prompt_package_manifest` path-for-path.
  2. **Committed config/registry match**:
     `agent_harness.reconstruct_protocol_registry_at_commit` loads
     `protocol_registry.json` as committed at that same commit (never the
     working tree), confirms it is schema-valid, confirms its
     `case_revisions` entry for this capture's case matches the `version`
     actually reconstructed from that commit's `case.json`, confirms its
     `instructions_sha256`/`skill_sha256` match the reconstructed bytes, and
     confirms this capture's provider/model/agent-type/tool/invocation-kind
     match the registry's declared config for this capture's config id
     (`agent_harness.verify_config_registry_match`).

  This two-part check is **strictly stronger** than checking `case.json`
  alone (an earlier revision of this check did only that, and an
  independent review correctly flagged it as materially understating what
  `benchmark_protocol_commit` claims to establish), and stronger still than
  content-equality alone: a later, definitive review found that
  content-equality never actually verified the committed
  provider/model/config, only prompt bytes -- a commit whose `case.json`
  happens to match, with no `protocol_registry.json` at all, previously
  passed; it no longer does (see "Protocol freeze and recapture" below for
  what this meant in practice for six previously-official captures). This
  proof is a **wall-clock-independent** proof of *what* protocol a capture
  used; it does not by itself prove anything about *when* that protocol
  became fixed relative to the capture -- see
  `agent_harness.verify_protocol_precommitment` for that separate,
  complementary, chronological check.

All 12 official captures' `benchmark_protocol_commit.verified` independently
recomputes to `true` under both halves of this check, and all 12 reference
the **same** commit: the protocol-freeze commit `5c7289b`
(`test_all_official_captures_bind_to_the_frozen_commit_with_unique_handles`
pins this). This was not always true -- see "Protocol freeze and
recapture" immediately below for how this benchmark got from "some
captures bound to an earlier, registry-less commit" to "all 12 bound to
one shared, precommitted, fully-verified freeze commit".

### Protocol freeze and recapture

An earlier revision of this document argued that content-equality alone
(the full-manifest reconstruction proof above) was sufficient, and that a
separate demand -- that a capture's protocol commit must **precede** that
capture's own generation, not just match it byte-for-byte -- should be
rejected as unnecessary, since a retroactively-selected-but-content-
identical commit cannot smuggle in different case content. That argument
was considered twice, independently upheld both times, and was still
overruled by the coordinating reviewer: content-equality and
precommitment are different properties, and only a commit that exists
**before** generation makes a "no cherry-picking, no post-hoc case
adjustment" claim independently auditable by a third party who was not
present during generation. This section documents what was actually done
in response, not just the disagreement.

`benchmarks/agent_quality/protocol_registry.json` (schema
`protocol-registry-v1`, validated by `validation.validate_protocol_registry`)
is a machine-readable freeze of both predeclared capture configurations
(provider/model/agent type/tool/invocation kind for config-a and config-b),
the exact sha256 of the `INSTRUCTIONS` prompt text and the bundled
`SKILL.md` at freeze time, the invocation protocol's `const: true`
invariants (one attempt only, no retry, no repair, no fence-stripping), and
a `case_revisions` map pinning the exact `case.json` version each of the
three affected cases must be at. This registry, together with the version
bumps for those three cases' `case.json` files, was committed as `5c7289b`
-- a real, ordinary Git commit, strictly before any of the six recaptures
described below were invoked.

`agent_harness.verify_protocol_precommitment` then independently checks,
for any capture, that its recorded `benchmark_protocol_commit`'s
**committer date** (not author date, which is trivially forgeable by
resetting a local clock) is strictly earlier than that capture's own
`capture_ingested_at_utc`. This is a distinct, complementary check from
`recompute_benchmark_protocol_commit_verification`'s content-equality
proof: one proves *what* protocol a capture used; the other proves *when*
that protocol became fixed relative to the capture. Both independently
return `true` for all 12 official captures as of this revision.

Following the freeze commit, the six captures that predated it in time --
`reordering-guard-clause`'s original two captures,
`context-limited-helper-call`'s original two captures, and
`cross-file-validation-edit`'s two *replacement* captures from the
answer-leak withdrawal (see "Withdrawn captures" below) -- were archived
unchanged (see below) and six entirely fresh, stateless sub-agent
invocations were made against the frozen protocol: one config-a and one
config-b for each of the three affected cases, no retries, no repair,
first raw bytes preserved exactly. Two of these six (both
`gemini-3.7-flash`/config-b) turned out to be genuine structural failures
unrelated to timing -- see "Real captured-run results" below.

### Strengthening the check found six more captures unprotected

The freeze commit and the six recaptures above satisfied the
precommitment requirement, but a further, definitive review of this exact
mechanism found that `recompute_benchmark_protocol_commit_verification`
still only ever checked full-manifest **content**-equality -- it never
loaded or verified `protocol_registry.json` at the referenced commit at
all. This meant `verified: true` proved a capture's prompt **bytes**
matched a committed protocol, but never that its provider/model/agent-type/
tool/invocation-kind actually matched a **committed configuration**. The
six captures for `error-swallow-to-raise`, `threshold-value-replacement`,
and `binary-asset-replacement`, previously described as "unaffected" and
left bound to `7b9d99b` (the earliest commit at which both the relevant
case content and the capture harness are simultaneously present in
history), were exposed by this: `7b9d99b` predates
`protocol_registry.json`'s introduction entirely, so no committed registry
existed there at all. Once `agent_harness.reconstruct_protocol_registry_at_commit`
and `agent_harness.verify_config_registry_match` were added (see
"Provenance" above) and folded into the same recompute function, these six
captures' `benchmark_protocol_commit.verified` claim stopped
independently recomputing to `true` -- a real regression this benchmark's
own strengthened check caught in its own history, not a false positive.

These six were archived exactly like the first six (see "Withdrawn
captures: protocol-not-precommitted" below, `protocol-config-not-precommitted`
reason code), bringing the total archive count to **14**, and six entirely
fresh captures were generated for these three cases against the same
frozen commit `5c7289b` (which does carry a committed
`protocol_registry.json`, and independently verifies for both content and
config match). All 12 official captures now reference this single shared
commit. Of these six new captures, one config-b
(`error-swallow-to-raise`) and both `binary-asset-replacement` captures
turned out to also be genuine structural failures with fabricated
citations -- see "Real captured-run results" below; this benchmark's
current total is five structural failures out of 12 official captures,
disclosed in full rather than only reporting the successes.

This round's fresh captures also introduced `orchestrator_agent_handle` (see
"Provenance" above), replacing a `task_id` field this harness could never
actually populate for `copilot_task` invocations (it was always `null`).
The six captures generated immediately after the original freeze were
retroactively updated with their real, recovered handles (confirmed still
present in this session's own agent registry at the time); this did not
change their raw response, explanation, or any other provenance field, and
was independently re-verified against both the content/registry and
precommitment checks after the edit.

This proof is deliberately scoped to the **generation-side prompt
package** (case content, `SKILL.md`, `INSTRUCTIONS.md`) -- it says nothing
about the **rubric** (`auditor/<case>/rubric.json`), which is a distinct,
real gap an independent review correctly identified: nothing here pins a
rubric's `required_facts` to a commit or verifies they were unchanged
after a capture was ingested. What this benchmark can state instead is a
directly checkable fact, not a mechanism: every rubric's `required_facts`
for the three cases this dispute concerns has, as of this revision, never
been edited since its first authoring. `cross-file-validation-edit`'s
rubric was touched a second time during its rename, but a direct diff of
that commit shows the only change was the `case_id` field matching the
rename -- `required_facts` is byte-for-byte identical before and after.
`reordering-guard-clause` and `context-limited-helper-call`'s rubrics have
each been modified exactly once, ever. This is disclosed as a real,
unenforced gap, not papered over as solved.

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
Nine of the 12 currently official captures produced structurally valid,
schema-conformant JSON that also passed the real
`shiftory.explain.validator.validate_explanation` check; three --
`reordering-guard-clause`/`context-limited-helper-call`'s `captured_config_b`
and `binary-asset-replacement`'s `captured_config_b` -- are genuine
structural failures (see below), reported honestly rather than omitted or
silently repaired. Every valid capture was dual-audited (two independent
annotation passes plus one adjudication pass); the table reports
`required_behavior_coverage` (satisfied out of 3 required facts), raw
`unsupported_claims`/`contradicted_claims` counts, `uncertainty_honesty`
violations out of claims checked, and the adjudication `disagreement_rate`
between the two independent passes, exactly as regenerated in
`docs/benchmarks/agent-quality/<case>/scores-v1.json`:

| Case | Candidate | Coverage | Unsupported | Contradicted | Uncertainty violations | Disagreement rate |
|---|---|---:|---:|---:|---:|---:|
| reordering-guard-clause | captured_config_a | 1/3 | 0 | 0 | 6/9 | 0.11 |
| reordering-guard-clause | captured_config_b | **structural failure** | -- | -- | -- | -- |
| error-swallow-to-raise | captured_config_a | 1/3 | 1 | 0 | 1/5 | 0.2 |
| error-swallow-to-raise | captured_config_b | 1/3 | 0 | 0 | 0/3 | 0.33 |
| threshold-value-replacement | captured_config_a | 2/3 | 0 | 0 | 2/3 | 0.0 |
| threshold-value-replacement | captured_config_b | 2/3 | 0 | 0 | 0/3 | 0.0 |
| cross-file-validation-edit | captured_config_a | **3/3** | 0 | 0 | 0/11 | 0.09 |
| cross-file-validation-edit | captured_config_b | 2/3 | 0 | 0 | 0/8 | 0.25 |
| binary-asset-replacement | captured_config_a | **3/3** | 1 | 0 | 1/9 | 0.22 |
| binary-asset-replacement | captured_config_b | **structural failure** | -- | -- | -- | -- |
| context-limited-helper-call | captured_config_a | 2/3 | 0 | 0 | 0/7 | 0.0 |
| context-limited-helper-call | captured_config_b | **structural failure** | -- | -- | -- | -- |

Read honestly, not as a leaderboard. `cross-file-validation-edit`'s
`captured_config_a` and `binary-asset-replacement`'s `captured_config_a`
are this benchmark's only two captures to reach 3/3 required-fact
coverage. For `binary-asset-replacement`, both independent annotator
passes agreed the candidate covers all three facts (the binary content
replacement, the file mode staying unchanged, and -- correctly reported as
genuine ambiguity rather than a fabricated guess -- that the new payload's
semantic meaning cannot be determined from bounded evidence), but both
also independently caught the same overreach: the summary's claim that
`icon.dat` is "the repository's single binary asset" is not established
by evidence about `icon.dat` alone (this benchmark's fixtures are not
audited for how many binary files exist elsewhere in the repository, so
this specific uniqueness claim is unverifiable and correctly scored
`unsupported`). Several of this candidate's specific commit-prefix and
blob-hash citations were independently disputed by one annotation pass
(which had only the rubric's prose, not the real evidence, to check
against) and confirmed accurate by the adjudicator directly reconstructing
this case's real fixture -- a concrete example of why this benchmark's
adjudication step exists, not merely majority-vote reconciliation.
`cross-file-validation-edit`'s `captured_config_a` states plainly that the
two `validate_email` functions are not equivalent and that the change is a
behavior regression; its sibling `captured_config_b` splits the same
change into two unconnected items and does not connect them to a single
"not equivalent" claim, so it scores 2/3. `context-limited-helper-call`'s
and `threshold-value-replacement`'s remaining captures all cover 2/3
required facts with zero unsupported claims -- both `threshold-value-replacement`
candidates correctly state every raw before/after number but never
perform or assert the combined worst-case-latency arithmetic the rubric's
third fact requires, even though `captured_config_b`'s wording states all
three raw ingredients needed to derive it; both independent annotator
passes explicitly distinguished "stating the ingredients" from "drawing
the inference" and withheld credit for the same reason in both cases.
`error-swallow-to-raise`'s two captures both cover only the central
re-raise fact (1/3): `captured_config_a`'s one overreaching claim
("callers observe the original failure", asserting a definite
caller-side consequence the rubric explicitly treats as unresolvable) was
disputed between the two annotation passes and adjudicated as
`unsupported` for consistency with this benchmark's established standard
for the identical claim type; `captured_config_b`'s claim that
`load_config` "catches the exception and re-raises it" was doubted by
both annotation passes (neither had direct source access), but the
adjudicator confirmed against this case's real fixture that the actual
code is exactly `except ValueError: return {}` -> `except ValueError:
raise`, so the candidate's mechanism description is byte-for-byte
accurate. Neither `error-swallow-to-raise` capture mentions the unchanged
success path. Every other capture correctly identified the single most
important (highest-importance) required fact, but **omitted at least one
lower-importance required fact**, consistent with this benchmark's
established pattern of captured models producing a single, narrowly-scoped
explanation item rather than covering every required behavior.

### Structural failures: fabricated citation ids

`reordering-guard-clause`, `context-limited-helper-call`, and
`binary-asset-replacement`'s `captured_config_b` (all `gemini-3.7-flash`)
are this benchmark's structural failures -- three out of 12 official
captures. Every one of these raw responses is well-formed
`shiftory.explanation/v1`-shaped JSON -- it passes `agent_harness
.capture_result`'s shallow shape check (schema tag, `items`,
`coverage_owners` present with the right structure) -- but each cites
`citations` evidence ids that do not exist anywhere in this environment's
real, deterministically-generated Shiftory evidence (Graphora is
disabled/unavailable in this environment, so `evidence.graph.facts` is
always empty regardless of which `--graphora` mode is requested): the
model fabricated plausible-looking `fact_*` identifiers that `shiftory
analyze` never actually produced for that diff. Running the real
`shiftory.explain.validator.validate_explanation` (the same validator
`shiftory verify` uses) against each raw response, as this benchmark's
independent review rounds have required, catches this; the shallow check
inside the capture harness deliberately does not, since it is meant only
to decide whether a raw response is *worth* materializing as
`explanation.json` before the deeper, evidence-aware check runs. No
`explanation.json` was written for any of the three (materializing one
from citations that fail real validation would misrepresent it as a
legitimate, scoreable explanation); each candidate's `raw-response.txt`
and `agent-run.json` are preserved exactly as first received -- this is
not a retry or repair, only a corrected classification of the same
first-attempt bytes. All three candidates' `score-v1` records carry
`structural_failure` populated and every semantic field `null`, per the
schema's mutual-exclusion rule (see "Coverage and omissions" above); their
annotation/adjudication fields are absent entirely, since there is no
`explanation.json` to decompose into claims.

Notably, `binary-asset-replacement/captured_config_b` fabricates the exact
same two citation ids (`fact_32314a96f2b283ccc2da5503`,
`fact_711411314f61102f006d3668`) that this same model
(`gemini-3.7-flash`) also fabricated for this same case in a genuinely
separate, independent invocation weeks earlier under the now-archived
registry_version 2 protocol -- confirmed by direct inspection of both raw
responses, not a processing artifact; no shared prompt content or
documentation in this repository contains these literal strings. This
reproduction across two completely independent invocations (different
task ids, different prompt-package directories, different protocol
commits) rules out a one-off coincidence and strongly suggests this model
deterministically derives these specific strings from some input it can
see in the real evidence (e.g. the correctly-cited `unit_` id or blob
hashes), via a transformation this benchmark has not identified and does
not claim to understand. `binary-asset-replacement/captured_config_a`
(`gpt-5.3-codex`, a different model family, generated in the same
registry_version 3 round) does not reproduce these ids and is structurally
valid, so this is disclosed as a reproducible finding specific to this
model/case combination, not a general claim about either model family.

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
identity includes the message). Both captures were then redone from
scratch, using two entirely fresh sub-agent invocations against the
corrected package. Those two replacement captures are themselves now
archived for a second, unrelated reason -- see the next section -- and have
been superseded by a third, fully independent pair generated after the
protocol freeze. Every one of the six cases' materialized prompt packages
was manually re-read against its own rubric for this same class of leak as
part of this revision's independent review; no further leaks were found,
though see "Limitations" below for what that check can and cannot
establish.

### Withdrawn captures: protocol-not-precommitted (six captures)

A distinct, later review round raised a different concern about the ten
captures not covered by the leak withdrawal above: for three cases, the
commit that `benchmark_protocol_commit` resolved to did not **precede**
that capture's own generation. Specifically (all times UTC):

- `reordering-guard-clause`'s two captures were ingested before this
  benchmark's very first commit (`0aaeb5c`) even existed, let alone before
  its predeclared capture configurations (`af94f95`) or its harness
  (`7b9d99b`) were committed.
- `context-limited-helper-call`'s two captures were ingested after that
  case's answer-leak fix (`4c8994a`) was made on disk, but before that fix
  was actually committed.
- `cross-file-validation-edit`'s (then-current) two replacement captures,
  described in the previous section, were ingested after that case's own
  neutral-prompt fix (`d1af70b`) was made on disk, but before it was
  committed.

As explained under "Protocol freeze and recapture" above, this benchmark's
position was, and remains, that full-manifest content-equality is the
property that actually rules out retroactive case tampering, and that a
later commit matching a capture's content byte-for-byte does not retroactively
become "the commit that produced it." The coordinating reviewer's
counter-position -- precommitment and content-equality are different,
non-substitutable properties, and only a commit that exists **before**
generation lets an independent third party verify "no cherry-picking"
without having to trust whoever ran the capture -- was adopted as the final
decision. Accordingly, all six of these captures were withdrawn, not
edited or reused: their raw responses, explanations, agent-run provenance
(now in the `agent-run-v2` shape), evaluations, and scores are preserved
byte-for-byte under
[`benchmarks/agent_quality/invalidated/<case-id>/protocol-not-precommitted/`](../benchmarks/agent_quality/invalidated/)
as `invalidated-protocol-not-precommitted-v1` records, each with a distinct
machine-readable `reason_code`
(`protocol-not-predeclared-before-generation`,
`prompt-fix-not-committed-before-generation`, or
`neutral-prompt-not-committed-before-generation` respectively) and the
original protocol metadata that was in force at generation time. Combined
with the two pre-existing `invalidated-answer-leak-v1` records for
`cross-file-validation-edit`'s original captures, this brought the total
to eight archived, non-official captures at the time -- a sixth reason
code and six more archives followed shortly after (see "A second archival
round" below); the current, final total of fourteen is what
`test_exactly_fourteen_archived_captures_with_distinct_reasons` pins in CI.

Six entirely fresh, stateless sub-agent invocations were then made -- one
config-a and one config-b for each of the three affected cases -- using
only the prompt package reconstructed from the freeze commit `5c7289b`,
with no access to any of the six archived captures, their scores, or this
document. Two of the six new byte streams are, by coincidence of content,
extremely similar in structure to their archived predecessors for the same
case/config pairing (most visibly `cross-file-validation-edit`'s
`captured_config_b`, which again splits the change into two unconnected
items) -- but each new capture's own `agent-run.json` records a raw
response hash that is independently verified against the actual bytes
`agent_harness.capture_result` received from that specific invocation, not
copied from the archive; nothing in this benchmark's tooling has the
ability to read an archived capture and write it back out as if newly
generated, since the archival and generation code paths do not share any
raw-response data.

### A second archival round: the config registry was never actually checked

As described under "Strengthening the check found six more captures
unprotected" above, a later review found that
`recompute_benchmark_protocol_commit_verification` never actually loaded
or verified `protocol_registry.json` at a capture's referenced commit --
only its prompt-package bytes. `error-swallow-to-raise`,
`threshold-value-replacement`, and `binary-asset-replacement`'s six
captures, previously bound to `7b9d99b` (which predates
`protocol_registry.json` entirely), failed this strengthened check even
though their prompt content still matched `7b9d99b` byte-for-byte. These
six were withdrawn using the same mechanism, under a fourth distinct
`reason_code`, `protocol-config-not-precommitted`:
`invalidated/<case-id>/protocol-not-precommitted/config-{a,b}/`, hash-verified
identically to the other archives.

Six replacement captures were then generated against the commit that added
`protocol_registry.json` (`5c7289b`, registry_version 2), each in its own
isolated prompt-package directory. **These six replacement captures were
themselves later found to be defective and were withdrawn again** -- see
the next section. An earlier draft of this document claimed the
directory-collision defect described there was caught before any bytes
were ever ingested as official data; that claim was false, and is
corrected here rather than silently edited away.

### A third archival round: a shared prompt directory silently lost the true first attempt

A definitive integrity review found that the six registry_version 2
replacement captures above were themselves second invocations, not first
attempts: they were generated by materializing one prompt-package
directory per **case** (shared across both `config-a` and `config-b`), so
both sub-agents wrote `RAW_RESPONSE` to the identical path and one
silently overwrote the other before `agent_harness.capture_result` ever
read either. registry_version 2's own `invocation_protocol` already
declared `one_attempt_only`/`no_retry`; generating and then officially
recording a second invocation after the true first was silently lost --
however unintentional -- violates that declared invariant for these six
captures, independent of whether their content and protocol-commit
verification otherwise held (both did, which is precisely why this defect
went unnoticed until a chronology-focused review specifically looked for
it).

The response was threefold, committed in this order:

1. **Freeze registry_version 3 before any new generation**
   (`benchmarks/agent_quality/protocol_registry.json`, commit `2ed5e10`).
   Adds three new `invocation_protocol` invariants --
   `unique_prompt_directory_per_invocation`,
   `output_directory_must_not_preexist`, `exclusive_directory_creation` --
   and a `prior_incidents` entry naming exactly what registry_version 2
   got wrong. These three fields are optional, not required, in
   `protocol-registry-v1.schema.json` specifically so registry_version 2's
   historical content (still the correctly-verified protocol for
   `reordering-guard-clause`/`context-limited-helper-call`/
   `cross-file-validation-edit`, unaffected by this defect) continues to
   validate honestly as what it always was;
   `validation.validate_protocol_registry` instead enforces these three
   invariants for any `registry_version >= 3` specifically.
   `error-swallow-to-raise`/`threshold-value-replacement`/
   `binary-asset-replacement`'s `case.json` bump to version 2 in the same
   commit, so a fresh capture generated afterward is governed by a
   genuinely new, never-before-generated-against protocol revision.
   `agent_harness.claim_exclusive_directory` enforces the new invariants
   in code: it creates a directory with `exist_ok=False` (never silently
   deletes and recreates one that already exists) and writes a sibling --
   never nested, so it can never leak into agent-visible prompt content --
   JSON sentinel recording which case/config/registry-revision claimed it.
   `capture_result`'s output directory is similarly strict by default now
   (refusing to reuse a directory that already contains a prior
   invocation's output), with an explicit, narrow escape hatch reserved
   for deliberate, visible corrections. `test_prepare_prompt_package_refuses_a_preexisting_directory`
   and `test_capture_result_refuses_a_preexisting_out_dir` reproduce the
   exact collision this closes and prove the refusal, rather than merely
   asserting it in prose.

2. **Record the six lost first attempts as honest, structured incident
   data, not merely prose.** A new schema,
   `invalidated-generation-attempt-v1`, is deliberately distinct from
   `invalidated-capture-v1`: it exists for a generation attempt whose raw
   output was never recoverable at all, so there is nothing to
   hash-verify, only an honest acknowledgment of the gap. Six records
   (`invalidated/<case-id>/lost-generation-attempts/{config-a,config-b}.json`)
   record the caller-supplied orchestrator handle recovered from this
   session's own still-idle agent registry, the model, and the
   registry_version 2 protocol commit the lost invocation actually ran
   under -- each with an explicit `provenance_limitation` stating plainly
   that which of a case's two invocations was actually lost cannot be
   determined after the fact (the overwrite happened at the filesystem
   level, with no write-order record), and that no raw bytes, hash,
   explanation, or generation timing exist or are recoverable for it.
   `orchestrator_reported_metadata` (elapsed time since launch, turn
   count) is explicitly labeled
   `orchestrator_tool_reported_not_provider_attested`, never implied to be
   provider-confirmed generation duration.
   `test_exactly_six_lost_generation_attempt_incidents` pins this count
   and schema-validates every record.

3. **Withdraw the six registry_version 2 replacement captures, and
   generate six genuinely fresh registry_version 3 replacements.** The six
   captures withdrawn in step 1 above are archived a *second* time --
   `invalidated/<case-id>/retry-after-collision/config-{a,b}/`, a third,
   distinctly-named archive group so none of a case's three withdrawal
   histories can ever collide -- under reason code
   `retry-after-unrecoverable-shared-directory-collision`. Combined with
   the two answer-leak withdrawals, the first six protocol-not-precommitted
   withdrawals, and these last six, this benchmark now carries **twenty**
   total archived, hash-verified, non-official captures across five
   distinct reason codes, plus the six lost-generation-attempt incidents
   enumerated separately (they have no bytes to hash-verify and must never
   be conflated with the twenty capture archives) --
   `test_exactly_twenty_archived_captures_with_distinct_reasons` pins the
   former, `test_exactly_six_lost_generation_attempt_incidents` the
   latter. Six entirely fresh, stateless sub-agent invocations were then
   made against the prompt package reconstructed from the frozen
   registry_version 3 commit (`2ed5e10`), each in its own exclusively-claimed
   directory -- one config-a and one config-b for each of
   `error-swallow-to-raise`, `threshold-value-replacement`, and
   `binary-asset-replacement`. `binary-asset-replacement/captured_config_b`
   turned out to also be a genuine structural failure (see "Structural
   failures" above). `reordering-guard-clause`, `context-limited-helper-call`,
   and `cross-file-validation-edit`'s six captures are unaffected by any of
   this and remain bound to the original registry_version 2 freeze commit
   `5c7289b`, which independently verifies for both content/registry match
   and precommitment.
   `test_all_official_captures_bind_to_a_verified_frozen_commit_with_unique_handles`
   asserts every official capture verifies against its referenced commit
   and that the three affected cases now reference a strictly different,
   `registry_version >= 3` commit than the three unaffected cases.

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
- Nine of the 12 currently-official captures succeeded structurally; three
  (`reordering-guard-clause`, `context-limited-helper-call`, and
  `binary-asset-replacement`'s `captured_config_b`) are real structural
  failures with fabricated citation ids -- see "Structural failures" above.
  The harness and schemas' support for recording timeouts/protocol
  violations/structural failures as `invalid_candidate` records is
  therefore now exercised by real captures, not only by unit tests.
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
- `benchmark_protocol_commit` verification (see "Provenance" above) proves
  the *generation-side* prompt package (case content, `SKILL.md`,
  `INSTRUCTIONS.md`) matches a specific commit's protocol. It does **not**
  cover the rubric: nothing pins a `rubric.json`'s `required_facts` to a
  commit or automatically verifies they were unchanged after a capture was
  ingested. This is a real, unenforced gap, not a solved problem -- see
  "Provenance" for the manually-checked (not code-enforced) fact that no
  disputed case's `required_facts` has ever been edited since its first
  authoring.
