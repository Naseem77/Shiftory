# Evidence and explanation formats

Shiftory bundles three JSON Schema Draft 2020-12 contracts:

| Schema | Purpose |
|---|---|
| `shiftory.evidence/v1` | Deterministic Git/source ledger plus optional graph facts |
| `shiftory.explanation/v1` | Agent-authored explanation and exact ownership table |
| `shiftory.report/v1` | Verified, renderable result |

Print the authoritative schemas:

```bash
shiftory schema evidence
shiftory schema explanation
shiftory schema report
```

This guide explains the contracts but does not replace those bundled schemas.

## Evidence hierarchy

An evidence packet records comparison and repository identities, ordered file
changes, optional graph facts, classifications/groups, diagnostics, omissions,
and metrics.

When `--path` is used, `comparison.paths` contains the sorted, normalized
selection. Only matching changed files appear in the ledger and metrics:
directories are recursive, repeated options are unioned, and file selections are
exact. The comparison identity binds this selection. Graph facts may reference
repository-wide structural context because Graphora indexes complete before/after
snapshots, but its changed-path and changed-line seeds are limited to selected
changes.

```text
FileChange
├── ChangeUnit
│   ├── text ──► one or more TextHunk IDs
│   └── binary | mode | rename | copy | submodule | unsupported
├── TextHunk
│   ├── old/new source range and optional heading
│   ├── raw patch byte count
│   └── changed lines
├── ChangeSpan
│   ├── before or after side
│   ├── inclusive source range
│   ├── changed-line IDs
│   └── optional replacement-span ID
└── SourceCitation
    ├── path, side, and inclusive range
    └── exact changed text and content hash
```

Context lines are used to validate coordinates and make the patch readable, but
only `+` and `-` lines become `ChangedLine` records. A deletion is on the
`before` side with `old_line`; an addition is on the `after` side with
`new_line`. Repeated identical source lines remain distinct because identity also
includes position and ordinal information.

The metrics block reports:

- files, units, hunks, and spans;
- added and deleted changed lines;
- raw patch and canonical evidence byte counts;
- normalized graph fact count; and
- engine ledger coverage ratios.

Evidence-generation coverage ratios describe the internal ledger. Final
explanation coverage is independently recomputed during verification.

## Explanation items

An explanation contains a non-empty summary and items with unique IDs. Item kinds
are:

- `behavioral`: explicit `before` and `after` statements;
- `structural`: organization or non-behavioral source change;
- `observer`: who can observe an already-described behavior;
- `ambiguity`: competing or uncertain interpretations; and
- `unresolved`: semantics the available evidence does not establish.

A pure addition or deletion uses a behavioral item with `absence: "before"` or
`absence: "after"` and sets that side to `null`. Confidence is one of
`extracted`, `inferred`, `ambiguous`, `unresolved`, or `unavailable`.

### Minimal shape

IDs below are placeholders; copy actual IDs from `evidence.json`.

```json
{
  "schema": "shiftory.explanation/v1",
  "summary": "The value selection changes.",
  "items": [
    {
      "id": "value-selection",
      "kind": "behavioral",
      "title": "Select the explicit value",
      "before": "The function returned the configured default.",
      "after": "The function returns the caller-provided value.",
      "confidence": "extracted",
      "citations": [
        "source_before_id",
        "source_after_id",
        {"id": "caller_fact_id"}
      ]
    }
  ],
  "coverage_owners": [
    {"evidence_id": "before_line_id", "owner_id": "value-selection"},
    {"evidence_id": "after_line_id", "owner_id": "value-selection"},
    {"evidence_id": "before_span_id", "owner_id": "value-selection"},
    {"evidence_id": "after_span_id", "owner_id": "value-selection"}
  ]
}
```

## Ownership is not citation

The two systems answer different questions:

- **Ownership:** Which single explanation item accounts for this change?
- **Citation:** Which evidence supports or contextualizes this statement?

### Exact ownership rules

`coverage_owners` must directly map each of these ownable IDs exactly once:

1. every added or deleted changed-line ID;
2. every change-span ID; and
3. every non-text unit ID (`binary`, `mode`, `rename`, `copy`, `submodule`, or
   `unsupported`).

Every `owner_id` must identify an explanation item. Duplicate entries are invalid
even when they repeat the same owner. Missing entries are invalid.

All changed lines in a span must share one effective owner, and the span's direct
owner must be that same item. This prevents a contiguous canonical span from
being split across unrelated explanations.

Text hunk and text-unit IDs are not placed in `coverage_owners`. A textual hunk is
covered when every descendant changed line is owned. A text unit is covered when
every changed line in all its hunks is owned. Non-text units have no descendant
lines, so they require direct ownership.

The verifier reports changed-line, span, hunk, and unit totals and ratios. Empty
categories have a ratio of `1.0`, and an empty comparison requires a summary that
explicitly says there are no changes and contains no items.

### Reusable citations

Each item's `citations` may reference:

- a changed line;
- a span;
- a textual hunk;
- a change unit;
- a `SourceCitation`; or
- a Graphora fact.

The same citation may appear more than once or support multiple items. Reuse is
valid and only increases `citation_count`; it cannot cause duplicate ownership.
Unknown references fail verification.

## Confidence and provenance

Confidence communicates how a fact was obtained:

| Value | Meaning |
|---|---|
| `extracted` | Directly represented by source or deterministic metadata |
| `inferred` | Derived through static matching or interpretation |
| `ambiguous` | More than one supported interpretation remains |
| `unresolved` | Available evidence does not resolve the relationship or meaning |
| `unavailable` | The evidence source could not be obtained |

Graph facts also carry provenance such as `graphora:tree-sitter`,
`graphora:regex`, `graphora:unknown-parser`, or an invalid-reference marker.
Regex definitions labeled extracted by the provider are downgraded to inferred.
Unknown confidence values normalize to unresolved.
The graph object records the exact provider version; this release's verified
adapter emits Graphora `0.2.1`.

An explanation cannot label uncertainty language such as “likely” or “might” as
`extracted`. The agent should create ambiguity or unresolved items rather than
invent certainty.

## Explanation, not review

The schema only admits explanation item kinds. Validation additionally rejects
review-style fields and communicative structures such as findings,
recommendations, severity/risk judgments, bug declarations, and suggested fixes.

The check is contextual rather than a raw token ban. Identifiers, paths, quoted
source, and faithful behavior descriptions may legitimately contain strings such
as `risk_score`, `severity_level`, `fix_bug`, or “review configuration.”

## Verification and rendering

```bash
shiftory verify --evidence evidence.json --explanation explanation.json
```

On success, `verify` prints canonical JSON containing `valid: true`, the
comparison identity, and computed coverage. It exits nonzero with structured
diagnostics for schema, ownership, citation, confidence, empty-diff, or policy
errors.

```bash
shiftory render \
  --evidence evidence.json \
  --explanation explanation.json \
  --format markdown
```

`render` performs verification again. Markdown sections always appear in this
order:

1. summary;
2. behavioral before to after;
3. structural and non-behavioral changes;
4. who observes the changes;
5. ambiguity and unresolved notes; and
6. complete source-cited coverage appendix.

JSON rendering emits `shiftory.report/v1`. Both forms state that accounting and
citation references were verified, not semantic correctness.

## Size budget behavior

`--max-evidence-bytes` defaults to 1,000,000 bytes. In the current v1
implementation, exceeding that value does not remove ledger records or source
citations. The complete evidence is retained and an `evidence_budget_exceeded`
diagnostic records requested and actual sizes. Consumers must not treat that
diagnostic as truncation or as successful compliance with the requested size.
