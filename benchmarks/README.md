# Shiftory benchmark inputs

This directory contains deterministic benchmark metadata, golden source assertions,
explanation-manifest templates, and a small offline Git fixture.

Graphora 0.2.1 public results are generated and published under
[`docs/benchmarks/`](../docs/benchmarks/) only after the complete suite passes from
a clean committed worktree. The published artifacts are:

- Click optional flag value: [report](../docs/benchmarks/click-optional-flag-value/report.md)
  and [metrics](../docs/benchmarks/click-optional-flag-value/metrics-v1.json)
- Axios spec FormData Blob: [report](../docs/benchmarks/axios-spec-formdata-blob/report.md)
  and [metrics](../docs/benchmarks/axios-spec-formdata-blob/metrics-v1.json)
- ripgrep repeated flags: [report](../docs/benchmarks/ripgrep-repeated-flags/report.md)
  and [metrics](../docs/benchmarks/ripgrep-repeated-flags/metrics-v1.json)

See the [benchmark methodology](../docs/benchmark-methodology.md) for the
normative procedure and metric definitions. The expected inventory counts in
`scenarios.toml` describe the pinned Git diffs; they are fixture facts, not
measured Shiftory output or performance metrics.

## Layout

- `scenarios.toml` pins the three public repositories, commit ranges, licenses,
  expected diff inventories, and golden input paths.
- `golden/<scenario>/assertions.json` describes literal source substrings expected
  at each side of a pinned comparison.
- `golden/<scenario>/explanation-template.json` supplies explanatory-only behavior
  groups and assigns every conceptual hunk to one group.
- `fixtures/offline-smoke/history.fast-import` creates a two-commit repository
  without network access; its expected identities and inventory are in
  `fixtures/offline-smoke/metadata.json`.
- `runner.py` acquires immutable inputs and executes the complete product path.
- `quality-rubric.schema.json` defines optional, manually recorded quality review.
- [`docs/benchmark-methodology.md`](../docs/benchmark-methodology.md) is the
  normative procedure and metrics definition.

The public repositories are not vendored. Acquire them explicitly, then analyze:

```console
python benchmarks/runner.py acquire
python benchmarks/runner.py run click-optional-flag-value
```

`suite --publish` is the only command that writes `docs/benchmarks/`; it does so
only after every public scenario passes. Normal CI runs only the committed
network-free fixture through the same analyze, instantiate, verify, and render path.

For pre-commit validation, run `suite` without `--publish`; a dirty worktree is
allowed, but its artifacts are never release reports. Publication requires a
clean committed Shiftory worktree and binds every report to that commit's
implementation manifest, package/dependency details, runner digest, and golden
input digest. Do not hand-edit generated reports.
