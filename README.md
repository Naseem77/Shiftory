# Shiftory

> **We do not read code changes anymore, and at agent speed we cannot track every
> small piece. Shiftory does it for you without the sweat.**

Shiftory turns a Git comparison into deterministic, source-cited evidence, then
verifies and renders an agent-authored explanation. It accounts for every parsed
added and deleted line, textual hunk, and non-text change unit without treating a
language model—or Graphora—as the source of truth.

> [!IMPORTANT]
> **Shiftory explains changes; it does not review them.** Its output does not make
> bug findings, assign severity, rank risk, or recommend fixes. Verification proves
> accounting, citation references, schema conformance, and this communication
> boundary. It does not prove that the explanation is semantically correct.

## Install

Shiftory requires Python 3.10+ and Git. Install from PyPI when a release is
available:

```bash
python -m pip install shiftory
shiftory --version
```

To work from a source checkout:

```bash
python -m pip install -e '.[dev]'
shiftory --version
```

Graphora is pinned by the package to `graphora-kg==0.2.1`. Its required
`tree-sitter!=0.26.0,>=0.23` dependency is release-pinned to `tree-sitter==0.25.2`.

## Quick start

From the repository whose changes you want explained:

```bash
shiftory explain
```

With no scope flag, Shiftory compares `HEAD` with the complete working tree:
staged, unstaged, and non-ignored untracked content. The command creates a private
run, writes deterministic evidence and an explanation template, and prints a JSON
descriptor, then stops. The thin agent skill reads that descriptor, fills the
template, and invokes the recorded resume command; resume verifies before it
renders and emits the report.

For a manual two-phase run:

```bash
# Save the printed descriptor; it contains the exact evidence and template paths.
shiftory explain --graphora auto > run-descriptor.json

# After an agent fills the descriptor's explanation template:
shiftory explain \
  --resume /path/to/run/run.json \
  --explanation /path/to/run/explanation.json \
  --output shiftory-report.md
```

The successful resume removes the private run directory by default. See
[Artifacts and retention](#artifacts-and-retention) before using sensitive
repositories.

## Install and invoke the agent skill

Install the bundled, thin skill into the current project:

```bash
shiftory install-skill --target copilot
```

This writes `.github/skills/shiftory/SKILL.md`. It refuses to overwrite different
content. Claude and generic layouts are also supported:

```bash
shiftory install-skill --target claude   # .claude/skills/shiftory/SKILL.md
shiftory install-skill --target generic  # skills/shiftory/SKILL.md
shiftory install-skill --target copilot --directory /custom/skill/directory
```

Then make one request to the agent:

> **Use Shiftory to explain my current Git changes.**

Add the desired scope to that request when needed, for example, “Use Shiftory to
explain commit `abc123`.” The skill delegates Git parsing, evidence generation,
verification, and rendering to the CLI; it does not reproduce those systems in
its prompt.

## Comparison scopes

Only one scope may be selected.

| Scope | Comparison |
|---|---|
| no flag | `HEAD` → staged + unstaged + non-ignored untracked working tree |
| `--staged` | `HEAD` → index |
| `--unstaged` | index → working tree |
| `--commit REV` | selected parent → commit; merge commits require `--parent N` |
| `--range BASE..HEAD` | the two resolved commits |
| `--range BASE...HEAD` | merge base of the endpoints → resolved right endpoint |
| `--branch NAME` | merge base of current `HEAD` and `NAME` → current `HEAD` |
| `--pr NUMBER` | immutable PR base SHA → head SHA |

`--repo PATH` selects another checkout. `--pr` is the only analysis scope that
may contact a network: it uses an authenticated `gh` CLI and may fetch missing
objects from `--remote` (default `origin`). Other scopes use local Git objects,
the index, and the filesystem.

## Advanced CLI workflow

Collect canonical JSON evidence for staged changes:

```bash
shiftory analyze \
  --staged \
  --graphora auto \
  --context-lines 5 \
  --max-evidence-bytes 1000000 \
  --output evidence.json
```

Render the same evidence packet directly as Markdown:

```bash
shiftory analyze \
  --range 'main...feature' \
  --graphora off \
  --format markdown \
  --output evidence.md
```

After an agent writes `shiftory.explanation/v1`, validate it:

```bash
shiftory verify \
  --evidence evidence.json \
  --explanation explanation.json
```

Render only after verification succeeds:

```bash
shiftory render \
  --evidence evidence.json \
  --explanation explanation.json \
  --format markdown \
  --output report.md

shiftory render \
  --evidence evidence.json \
  --explanation explanation.json \
  --format json \
  --output report.json
```

Inspect the exact bundled contracts with `shiftory schema evidence`,
`shiftory schema explanation`, or `shiftory schema report`.

### Illustrative output

The following is an **illustrative, abbreviated sample**, not benchmark output.
Real IDs are deterministic hashes and the full appendix contains every owner.

```markdown
# Shiftory explanation

The value selection changes from the configured default to the explicit input.

## Behavioral before to after

### Select an explicit value

**Before:** The function returned the configured default.

**After:** The function returns the caller-provided value.

Evidence: `source_ab12`

Confidence: **extracted**

## Complete source-cited coverage appendix

- Changed lines: 4/4 (100%)
- Textual hunks: 1/1 (100%)
- Change units: 1/1 (100%)

> Shiftory verified accounting and citation references; it does not verify
> semantic correctness.
```

## What is accounted for

Shiftory models each file as:

```text
FileChange
└── ChangeUnit (text, binary, mode, rename, copy, submodule, unsupported)
    └── TextHunk
        └── ChangeSpan (contiguous changed lines on one side)
            └── ChangedLine
```

Each changed line, each span, and each non-text unit must have exactly one
explanation owner. Textual hunk and text-unit coverage is derived from complete
ownership of their descendant lines. Citations are independent references and
may be reused by multiple items without changing ownership counts.

Read [the evidence format](docs/evidence-format.md) for exact validation rules.

## Languages

Git accounting is language-independent. Every parseable patch receives the same
line/hunk/unit ledger even when structural enrichment is unavailable.

Graphora 0.2.1 recognizes Python, JavaScript/JSX, TypeScript/TSX, Go, Java, Rust,
C, C++, Ruby, and PHP files. It tries tree-sitter first and falls back to regular
expressions; fallback facts are downgraded where appropriate. Graphora facts are
optional enrichment, not coverage truth.

“Static tests” are source-level call relationships to files Graphora recognizes
as tests. They are not executed tests or runtime coverage. See
[Limitations](docs/limitations.md).

## Privacy and local state

Git analysis and Graphora enrichment are local by default; `--pr` alone uses
`gh` and may fetch missing objects. The CLI sends no telemetry, prompts, reports,
or product memory and does not itself send source to an LLM. The agent workflow
does intentionally give the invoked agent the bounded evidence file, so that
agent and its host's data-handling policy still apply.

Graphora needs source snapshots. Shiftory stores derived, repository-scoped
snapshots and graph data beneath the platform cache directory. Common credential
filenames and key suffixes are excluded, but source and changed text can still be
sensitive. Inspect and clear the current repository's cache with:

```bash
shiftory cache status
shiftory cache clear
```

Use `--cache-dir PATH` or `SHIFTORY_CACHE_DIR` to relocate it. `--no-cache`
disables cache-backed snapshots; combine it with `--graphora off` when no
structural enrichment is wanted.

### Artifacts and retention

`shiftory explain` stores runs under the platform state directory, or
`SHIFTORY_RUN_DIR` when set. Directories are owner-only and files are written
owner-readable/writable. An awaiting-explanation run remains available so the
agent can resume it. A successfully finalized run is deleted unless either:

```bash
shiftory explain \
  --resume /path/to/run/run.json \
  --explanation /path/to/run/explanation.json \
  --keep-artifacts

SHIFTORY_KEEP_ARTIFACTS=1 shiftory explain \
  --resume /path/to/run/run.json \
  --explanation /path/to/run/explanation.json
```

New failed runs retain `diagnostic.json` and report their artifact path. Shiftory
does not automatically expire retained runs or repository caches; clear them
deliberately according to your retention policy.

## Public benchmarks

All three Graphora 0.2.1 scenarios passed the complete cold and warm product path.
They were published together from clean Shiftory commit
`7248eb13f2ce458a05f88279cad0730459f1ffd6` (tree
`6ac0abc505342a4ee5bddfe2291d9cdafc172991`).

| Scenario | Base → head | Files | Hunks | Changed lines (+/−) | Spans |
|---|---|---:|---:|---:|---:|
| Click optional flag value | `7f7bbe4569ea68e8dabee232eade069ef3310aea` → `91de59c6c8abc8251e7af551cd4546cc964288af` | 3 | 5 | 52 (49/3) | 8 |
| Axios spec FormData/Blob | `65e8d1e28ce829f47a837e45129730e541950d3c` → `6ac574e00a06731288347acea1e8246091196953` | 9 | 22 | 359 (304/55) | 48 |
| ripgrep repeated flags | `c8e755f11f31b6da04329cdc7433747bba70150f` → `d83bab4d3f29a0176a20ea004c2cba44058d4210` | 2 | 14 | 2,026 (1,470/556) | 38 |

| Scenario | Complete path cold / warm (s) | Evidence JSON / Markdown (bytes) | Report Markdown (bytes) | Report reduction vs evidence Markdown | Line / span / hunk / unit coverage | Assertions (pass/fail/skip) | Deterministic | Artifacts |
|---|---:|---:|---:|---:|---:|---:|:---:|---|
| Click optional flag value | 3.397700 / 1.164486 | 67,082 / 33,774 | 5,320 | 28,454 bytes (84.25%) | 52/52 · 8/8 · 5/5 · 3/3 (100% each) | 31/0/0 | yes | [metrics](docs/benchmarks/click-optional-flag-value/metrics-v1.json) · [report](docs/benchmarks/click-optional-flag-value/report.md) |
| Axios spec FormData/Blob | 3.926553 / 1.379883 | 230,567 / 119,540 | 25,775 | 93,765 bytes (78.44%) | 359/359 · 48/48 · 22/22 · 11/11 (100% each) | 71/0/0 | yes | [metrics](docs/benchmarks/axios-spec-formdata-blob/metrics-v1.json) · [report](docs/benchmarks/axios-spec-formdata-blob/report.md) |
| ripgrep repeated flags | 4.673000 / 2.763596 | 1,243,642 / 673,829 | 135,985 | 537,844 bytes (79.82%) | 2,026/2,026 · 38/38 · 14/14 · 2/2 (100% each) | 40/0/0 | yes | [metrics](docs/benchmarks/ripgrep-repeated-flags/metrics-v1.json) · [report](docs/benchmarks/ripgrep-repeated-flags/report.md) |

Across the three scenarios, all 142 machine assertions passed with no failures or
skips, every accounting dimension reached 100%, and every cold/warm canonical
semantic bundle matched.

These are per-run values recorded on macOS 15.6.1 arm64 with 8 logical CPUs,
25,769,803,776 bytes of memory, Python 3.11.6, Git 2.53.0,
`graphora-kg==0.2.1`, and `tree-sitter==0.25.2`; wall times qualify only that
machine and environment. Cold and warm canonical semantic bundles matched after
excluding environment, acquisition and run timing, and local and installation
paths.

The publication binds `repository:src` execution to implementation manifest
`edaa36712710d2c59ec68cc7d2102dbad31612d382f72a71a5f2f4779d69415f`,
executed package-code digest
`e9279689384c1e9779fec8c85d19811b7d0bab64a360c56bcd1b425c135fe764`,
runner digest
`172249864800dc2f72951d879fbde0e7fe30b96c18308514e9200e5cddcf2dd4`,
and golden-input digest
`f1fd72c75085f3a989cb9b11438baa764a103eece3148e9b55ccc062866a25ac`.
The generated metrics contain the complete private-safe provenance.

Coverage is measurable **accounting**, not a semantic-correctness or prose-quality
score. Machine assertions check the listed selected observable facts, evidence,
and report wiring; they do not prove complete behavior. The published prose comes
from version-controlled golden templates and is not a measured model-quality
result. The separate [manual quality rubric](benchmarks/quality-rubric.schema.json)
is optional. See the exact
[benchmark methodology](docs/benchmark-methodology.md).

## Documentation

- [Architecture and data flow](docs/architecture.md)
- [Evidence and explanation formats](docs/evidence-format.md)
- [Limitations and confidence boundaries](docs/limitations.md)
- [Benchmark methodology](docs/benchmark-methodology.md)
- [Published benchmark metrics and reports](#public-benchmarks)
- [Contributing](CONTRIBUTING.md)

## Development

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy src/shiftory
pytest
python -m build
python -m twine check dist/*
```

CI is expected to enforce formatting, linting, strict typing, tests, schema and
package-data checks, clean-wheel installation, documentation links, license
inventory, and the repository's offline benchmark smoke checks. See
[CONTRIBUTING.md](CONTRIBUTING.md) for compatibility and generated-artifact
rules.

## License

Shiftory is licensed under Apache-2.0. See [LICENSE](LICENSE) and the repository's
third-party notices.
