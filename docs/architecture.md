# Architecture

Shiftory separates deterministic change accounting from agent-authored semantic
explanation. Git owns comparison truth, Shiftory owns evidence identities and
validation, Graphora contributes optional static structure, and the agent owns
the prose.

## Data flow

```text
scope + repository
        │
        ▼
comparison resolver ──► immutable SHAs / index / working-tree fingerprint
        │
        ▼
Git patch acquisition ──► parser ──► line/span/hunk/unit ledger
        │                                  │
        │                                  ├──► classifications + source citations
        │                                  │
        ├──► before source snapshot ─┐      │
        └──► after source snapshot ──┴──► isolated worker ──► Graphora 0.2.1
                                                                  │
                                                                  ▼
                                                         normalized optional facts
                                           │
ledger + citations + facts ────────────────┴──► complete evidence/v1 ledger
                                                    │
                                     fits budget ───┴── exceeds budget
                                         │                    │
                                         ▼                    ▼
                                  agent explanation/v1   chunk plan + bounded chunks
                                                              │
                                                              ▼
                                                  chunk explanations + composition
                                         │                    │
                                         └─────────┬──────────┘
                                                   ▼
                                     schema + ownership + policy validation
                                                   │
                                                   ▼
                                         report/v1 or Markdown
```

## Runtime stages

### 1. Resolve a comparison

`shiftory.git.repository` discovers the canonical root with
`git rev-parse --show-toplevel`. It resolves committed endpoints to full commit
objects and records labels, SHAs, a repository identity, and a deterministic
comparison identity.

The repository identity hashes the absolute Git common directory and origin URL,
so worktrees that share Git storage use the same repository cache namespace while
portable evidence avoids absolute paths. Dirty targets use index or working-tree
fingerprints.

Supported modes are working tree, staged, unstaged, commit, two-dot range,
three-dot range, branch, and pull request. All are local except explicit pull
request resolution, which calls `gh pr view` and may run `git fetch` for missing
base/head objects.

### 2. Acquire and parse Git truth

The repository layer invokes Git with stable locale, no pager, no color, no
external diff driver, no text conversion, full object IDs, binary markers, and
explicit context. Full working-tree mode also creates patches for non-ignored
untracked files.

The parser:

1. separates `diff --git` records;
2. reads paths, blob IDs, file modes, status, rename/copy, and binary metadata;
3. validates each hunk's declared old/new totals;
4. assigns side-aware IDs to each added/deleted line;
5. groups maximal contiguous same-side lines into spans;
6. links adjacent before/after spans as replacements;
7. creates text and non-text units; and
8. rejects missing ownership links or stable-ID collisions in the ledger.

Source-range validation reads committed bytes from Git objects, staged bytes from
the index, and working bytes from the filesystem. It does not change the checkout.

### 3. Organize deterministic evidence

File rules classify evidence as binary, dependency, tests, docs, generated,
schema, configuration, structural, behavioral, or unresolved. These categories
organize evidence; they are not judgments about impact or correctness.

Changed spans become exact before/after source citations. Metrics count files,
units, hunks, spans, added/deleted lines, patch bytes, evidence bytes, and graph
facts. Direct `analyze` preserves the complete ledger and records a diagnostic when
its mandatory floor exceeds `--max-evidence-bytes`.

`explain` first attempts that compatible v1 packet. If the mandatory floor is still
too large, a v2 private run keeps the complete ledger outside agent payloads.
Replacement-linked spans and non-text units become indivisible work atoms.
Deterministic affinity prefers existing classifications, file/unit/hunk locality,
and valid Graphora relationships between changed files. A union-find groups each
symbol relationship through one deterministic representative. Graph fact sort keys
and canonical component sizes are computed once, then per-path size indexes skip
fact ranges that cannot fit a chunk's remaining budget. Graphora-unavailable runs
use the same hierarchy-only order. Candidate chunks are accepted only after their
final canonical JSON fits the effective byte/token-derived ceiling.

### 4. Enrich with Graphora

When Graphora is enabled, Shiftory materializes separate before/after source
snapshots in its cache and opens Graphora's embedded backend in a
repository-scoped directory. The adapter uses only Graphora's public package API:
repository indexing, per-file parsing, and blast-radius relationships.

Graphora and its tree-sitter extensions load only in a Shiftory-owned worker
process. The parent sends explicit snapshot, cache, side, changed-path, and
changed-line options in a versioned request under a sanitized environment and
accepts only a strictly validated, versioned JSON result. Worker execution has a
bounded timeout. A signal, nonzero exit, timeout, or malformed result therefore
cannot terminate the parent: auto mode records Graphora as unavailable, while
required mode raises a typed failure.

Before enrichment, a separate worker probe records the distribution version,
resolved `graphora.__file__`, `direct_url.json` metadata and editable flag, and a
deterministic digest of the installed Graphora package code. Shiftory verifies
the module path, package inventory, and wheel `RECORD` hashes, then binds the
enrichment request to that exact provenance digest. Editable installs and
origins that do not match the selected distribution are unavailable in auto
mode and typed failures in required mode.

Shiftory normalizes definitions, direct callers, direct callees, importers, and
static test relationships into its own `GraphFact` model. Paths and available
line numbers are checked against the corresponding snapshot. The adapter records
parser provenance and confidence; regex-extracted definitions cannot remain
`extracted`.

`--graphora auto` turns adapter failures into explicit unavailable diagnostics.
`--graphora required` fails instead. `--graphora off` skips source snapshot
materialization and enrichment.

### 5. Retrieve omitted source safely

Chunk source text that does not fit is represented by pre-recorded inclusive source
ranges. `shiftory retrieve` accepts only a generated run and range ID: there is no
path or coordinate input. It revalidates private artifact paths, schemas, identities,
exact canonical on-disk bytes, digests, recorded sizes, repository/comparison
identity, mutable fingerprints, exact source coordinates, and content hashes. Any
mismatch fails closed.

### 6. Explain, verify, and render

The v1 evidence packet and every v2 chunk are deterministic. A v1 agent writes
`shiftory.explanation/v1`. A v2 agent writes one bound
`shiftory.chunk-explanation/v1` per chunk, directly owning each assigned span or
non-text unit. Composition rejects missing/duplicate/stale/tampered chunks, expands
span owners to every global changed-line ID, and emits the existing explanation/v1.
The validator then:

- validates both schemas;
- validates item kinds and confidence;
- requires exactly one owner for every changed line, span, and non-text unit;
- derives complete textual hunk/unit coverage from line ownership;
- checks every citation reference;
- rejects review/judgment communication structures; and
- handles empty comparisons explicitly.

The renderer groups valid items into fixed sections and emits a coverage appendix.
It never upgrades accounting success into a claim of semantic correctness.

## State and ownership

| State | Owner | Lifecycle |
|---|---|---|
| Git objects, index, working files | repository/user | Read-only to Shiftory |
| Evidence JSON | Shiftory | Deterministic command output or run artifact |
| Chunk plan/payloads | Shiftory | Deterministic private v2 run artifacts |
| Recorded retrieval range | Shiftory | Served only after source/hash revalidation |
| Explanation manifest | agent/user | Authored after evidence collection |
| Report | Shiftory | Built only after validation |
| Source/Graphora cache | Shiftory | Repository-scoped; retained until `cache clear` |
| Explain run directory | Shiftory | Awaiting runs retained; successful finalized runs normally deleted |

Cache paths are keyed by a repository hash and source snapshot fingerprints.
Writes use private permissions, atomic replacement, and a per-repository advisory
lock. The cache contains local derived source and graph data, not prompts,
telemetry, reports, or cross-repository product memory.

Private run writes also use atomic replacement rather than truncating an existing
inode. Run reads and writes reject symbolic links, multiple hard links, foreign
ownership, and non-private modes before consuming or replacing an artifact.

## Boundaries and invariants

The important boundaries are Git subprocesses, the optional `gh` subprocess,
filesystem snapshots, the isolated Graphora worker and its public-API calls, the
agent-authored manifest, and JSON Schema validation.

The invariants that define Shiftory's correctness boundary are:

1. every parsed changed line belongs to exactly one span;
2. every text hunk belongs to exactly one text unit;
3. every file change has at least one unit;
4. changed source ranges fit the corresponding before/after source;
5. every ownable evidence ID has exactly one explanation owner;
6. all lines in a span have the same effective owner as that span; and
7. citations are valid references but never affect ownership counts;
8. each v2 span or non-text ownership target occurs in exactly one chunk; and
9. Graphora affinity never creates, removes, or owns Git ledger work.

Violating one of these invariants is an error, not a partial-success report.

## Determinism

Stable identities hash versioned canonical payloads containing paths, source
coordinates, object/fingerprint data, and content. Files, facts, groups, and
owners are sorted; JSON is canonical; report sections have fixed order; and
reproducibility-sensitive payloads contain no timestamps.

Working-tree evidence remains deterministic only for the recorded filesystem
state. If files change between phases, use the run's captured evidence and
comparison identity rather than starting a different comparison.
