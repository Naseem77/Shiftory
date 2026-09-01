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

Citations answer "what is related?" They do not answer "does this evidence
support this sentence?" That question belongs to grounding.

## Grounded claims

Each item may carry an optional `grounding` object holding one to thirty-two
`claims`. A claim binds a named, machine-checkable predicate to the exact
evidence allowed to support it. Grounding is additive: a `shiftory.explanation/v1`
manifest without it stays valid and behaves exactly as before.

```json
{
  "id": "timeout",
  "type": "value_change",
  "support_level": "verified",
  "support": ["before_span_id", "after_span_id"],
  "before_literal": "TIMEOUT = 30",
  "after_literal": "TIMEOUT = 60"
}
```

### Support levels

| Level | Meaning |
|---|---|
| `verified` | Shiftory proved this claim's predicate over the bound evidence. |
| `inferred` | The operands are proven present and bound; the predicate is the agent's reading. |
| `ambiguous` | The operands are proven present and bound; competing readings remain. |
| `unresolved` | Nothing is asserted; the evidence does not settle the question. |
| `unavailable` | Nothing is asserted because a source such as the graph is missing. |

Every level except `verified` requires a non-empty `limits` string. `verified`,
`inferred`, and `ambiguous` all enforce the claim's *obligations*: the operands
must actually appear, on the required side, inside evidence the item owns. Only
`verified` additionally enforces the claim's *entailment*.

That split is what makes an ordering claim useful. A statement about two
operations must evidence both operations at every asserting level; only the
ordering itself may be downgraded.

### Claim types

| Type | Obligations | `verified` adds |
|---|---|---|
| `text_presence` | `literal` appears in bound `side` evidence | nothing further |
| `text_absence` | at least one bound region on `side` | `literal` appears in no cited region |
| `value_change` | `before_literal` in bound before evidence, `after_literal` in bound after evidence | the two regions are replacement-linked, and each literal appears only on its own side |
| `addition` | `literal` appears in bound after evidence | `literal` appears in no changed before line of the same changed file |
| `deletion` | `literal` appears in bound before evidence | `literal` appears in no changed after line of the same changed file |
| `source_order` | `first` and `second` both appear in bound `side` evidence | exactly one cited region, in which every `first` occurrence precedes every `second` occurrence |
| `graph_relation` | a supported graph fact matches `fact_kind`, `side`, `symbol`, and any `target`/`path` | the graph is `available` and a matched fact is `extracted` |
| `non_text_change` | a supported non-text unit the item owns | the unit `kind` and every declared `metadata` entry match |

Matching is byte-exact substring matching on evidence text. There is no case
folding, whitespace normalization, tokenization, or regular-expression
interpretation. Literals are one to 512 characters.

Region text comes from the source citation covering a span. When the evidence
budget omitted that citation's text, the span's changed-line content is used
instead, so a bounded packet still grounds correctly.

### Support must be bound to the item

A claim's `support` may only name evidence that resolves into the change the
item owns through `coverage_owners`:

- a changed line, span, or source citation must resolve to owned changed lines;
- a textual hunk or text unit must intersect owned changed lines, and only the
  spans the item owns inside it can supply claim text;
- a non-text unit must be directly owned; and
- a graph fact's `path` must be a path the item owns, or any changed path when
  the item owns no source evidence.

A cited changed line is read as the whole contiguous span that contains it, and
every proof names the span it examined. A span is atomic and has one owner, so
this keeps a single-line citation from shrinking a span-scoped predicate.

Unrelated same-file citations, wrong-side citations, stale identifiers, and
another item's evidence are all rejected. `verified` also requires at least one
support reference the item owns itself.

### Legitimate shared support

When two items genuinely explain the same textual hunk, a claim may declare
`shared_support` entries with `evidence_id`, the real `owner_id`, and a
`reason`. The declared owner must be a different existing item that really owns
that evidence, and the shared evidence must sit in a textual hunk the claim's
own support already touches. Cross-item borrowing outside that locality is
rejected.

### Confidence may not exceed support

Item `confidence` is compared with the weakest support level among its claims:

- `extracted` requires every claim to be `verified`;
- `inferred` allows `verified` and `inferred`;
- `ambiguous` also allows `ambiguous`; and
- `unresolved` or `unavailable` allow any level.

A behavioral item with both `before` and `after` needs a `value_change` claim,
or grounded claims on both sides. A pure addition needs an `addition` claim, and
a pure deletion needs a `deletion` claim.

### Grounded example

```json
{
  "id": "value-selection",
  "kind": "behavioral",
  "title": "Select the explicit value",
  "before": "The function returned the configured default.",
  "after": "The function returns the caller-provided value.",
  "confidence": "inferred",
  "citations": ["source_before_id", "source_after_id"],
  "grounding": {
    "claims": [
      {
        "id": "literal",
        "type": "value_change",
        "support_level": "verified",
        "support": ["before_span_id", "after_span_id"],
        "before_literal": "return DEFAULT",
        "after_literal": "return value"
      },
      {
        "id": "ordering",
        "type": "source_order",
        "support_level": "inferred",
        "limits": "Source order does not establish execution order.",
        "support": ["after_citation_id"],
        "side": "after",
        "first": "validate(value)",
        "second": "return value"
      }
    ]
  }
}
```

### Enforcement mode

`shiftory explain` requires grounding on every item by default and records that
decision in its run descriptor, so a resume invocation cannot weaken it with a
flag and a descriptor without a valid grounding block is rejected. Pass
`--grounding optional` in the first phase to keep the historical v1 behavior.
The run directory is owner-only private state that the workflow itself writes;
as with the evidence file, Shiftory does not defend against a caller that edits
its own run artifacts.

`shiftory verify` and `shiftory render` default to `--grounding optional`, so
manifests written by existing tooling keep verifying. Declared grounding is
always validated in both modes; the mode only decides whether a missing
`grounding` block is an error.

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
comparison identity, computed coverage, and a `grounding` block whenever the
manifest declares claims. It exits nonzero with structured diagnostics for
schema, ownership, citation, confidence, empty-diff, grounding, or policy
errors. Grounding diagnostics additionally carry a `code` such as
`grounding.operand_missing` or `grounding.support_unbound`, so an agent can
correct exactly the failing claim.

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
5. ambiguity and unresolved notes;
6. grounded claims, when any item declares grounding; and
7. complete source-cited coverage appendix.

JSON rendering emits `shiftory.report/v1`. An ungrounded report states that
accounting and citation references were verified, not semantic correctness. A
grounded report additionally states that every declared claim was verified
against the exact evidence bound to it, and that verified claims are
source-level facts rather than runtime behavior.

## Size budget behavior

`--max-evidence-bytes` defaults to 1,000,000 bytes. In the current v1
implementation, exceeding that value does not remove ledger records or source
citations. The complete evidence is retained and an `evidence_budget_exceeded`
diagnostic records requested and actual sizes. Consumers must not treat that
diagnostic as truncation or as successful compliance with the requested size.
