---
name: shiftory
description: Explain every Git change with exact, source-cited accounting.
---

Treat one invocation of this skill as one workflow, even though the CLI uses two
private phases:

1. Run `shiftory explain` once with exactly the requested scope. No scope flag
   means `HEAD` versus staged, unstaged, and non-ignored untracked content.
2. Read the returned JSON descriptor and its bounded `evidence` file. Fill the
   descriptor's `explanation` file as `shiftory.explanation/v1`, consulting its
   `schema_command` when needed. Do not ask the user to manage the descriptor or
   resume step.
3. Explain behavior before-to-after, mark uncertainty honestly, and give every
   changed line and every non-text unit exactly one `coverage_owners` entry.
   Span ownership is inherited from its unanimously owned lines. Citations are
   separate and may be reused.
4. Run the descriptor's `resume_command`. This finalizer always verifies and
   renders before emitting the report. Present only that emitted report.
5. On failure, report the exact `details.diagnostic` path from CLI JSON. Never
   present an unverified template or partial report.

Use only these comparison scope forms when requested: `--staged`, `--unstaged`,
`--commit REV [--parent N]`, `--range BASE..HEAD`, `--branch REF`, or
`--pr NUMBER [--remote NAME]`. Add one `--path PATH` for each requested file or
directory; directory selection is recursive and repeated paths are unioned. Do
not invent other path syntax. Do not add `--keep-artifacts` unless requested.

Explain; do not review. Do not make findings, severity/risk judgments, or
recommendations. Do not parse Git, query Graphora, reproduce schemas, or place a
large explanatory prompt in this skill; the CLI owns those mechanics.
