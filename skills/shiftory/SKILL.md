---
name: shiftory
description: Explain every Git change with exact, source-cited accounting.
---

Treat one invocation of this skill as one workflow, even when the CLI returns
multiple private phases:

1. Run `shiftory explain` once with exactly the requested scope. No scope flag
   means `HEAD` versus staged, unstaged, and non-ignored untracked content.
2. Read the returned descriptor's `schema` and follow exactly one path:
   - For `shiftory.run/v1`, read only its bounded `evidence` file and fill its
     `explanation` file as `shiftory.explanation/v1`.
   - For `shiftory.run/v2`, never read `ledger` or `plan`. Process `chunks` in
     listed order. Read one chunk's `payload`, fill only that entry's
     `explanation` as `shiftory.chunk-explanation/v1`, then release that payload
     before reading the next. Preserve the payload's chunk, comparison, and
     ledger identities. Give every listed span or non-text `ownership_target`
     exactly one owner; the CLI expands span ownership to changed lines.
3. When a chunk context has `text: null`, invoke only the descriptor's
   `retrieve_command` with each listed `retrieval_range_id`. Do not construct a
   path or range, read repository files directly, or use retrieval IDs from
   another run. Treat any hash or mutable-state failure as fatal.
4. Explain behavior before-to-after, mark uncertainty honestly, keep item IDs
   unique across chunks, and cite only IDs in the current payload's
   `allowed_citation_ids`. Citations may be reused and never change ownership.
   Consult the descriptor's `schema_command` when needed. Do not ask the user to
   manage chunks, retrieval, manifests, or resume steps.
5. After the one v1 explanation or all v2 chunk explanations are complete, run
   the descriptor's `resume_command`. This finalizer always verifies exact global
   coverage and renders before emitting the report. Present only that emitted
   report.
6. On failure, report the exact `details.diagnostic` path from CLI JSON. Never
   present an unverified template or partial report.

Use only these scope forms when requested: `--staged`, `--unstaged`,
`--commit REV [--parent N]`, `--range BASE..HEAD`, `--branch REF`, or
`--pr NUMBER [--remote NAME]`. Do not add `--keep-artifacts` unless requested.

Explain; do not review. Do not make findings, severity/risk judgments, or
recommendations. Do not parse Git, query Graphora, reproduce schemas, or place a
large explanatory prompt in this skill; the CLI owns those mechanics.
