# Benchmark methodology

## Status and scope

Public all-pass Graphora 0.2.1 results for all three scenarios are recorded under
`docs/benchmarks/`. They were produced in one clean publication suite from
Shiftory commit `7248eb13f2ce458a05f88279cad0730459f1ffd6` (tree
`6ac0abc505342a4ee5bddfe2291d9cdafc172991`). All 142 machine assertions
passed with no failures or skips, line/span/hunk/unit accounting was 100%, and
the cold and warm canonical semantic bundles matched for every scenario. The
values in `benchmarks/scenarios.toml` are immutable fixture expectations, not
measured Shiftory results. The
[public summary](../README.md#public-benchmarks) records the exact scenario
identities, accounting, timings, artifact sizes and reductions, coverage,
assertion totals, determinism result, and common private-safe provenance, and
links each scenario's generated metrics and report. Coverage is an accounting
guarantee. Golden assertions check selected observable facts; neither is a
complete semantic-correctness or prose-quality score.

Only reports created by `suite --publish` from a clean committed implementation
may be treated as release benchmark evidence.

Normal CI remains network-free and reconstructs only the small committed
`offline-smoke` Git history.

## Immutable acquisition

Each public scenario names a canonical remote, full 40-character base and head
SHA, expected license expression and files, and expected Git inventory. Acquisition
clones on first use and fetches the two objects into scenario-specific refs.
Before analysis the runner fails closed unless:

1. `origin` is the configured canonical remote;
2. both fetched commit refs resolve to the exact full SHAs;
3. every license file exists at the head, is non-empty, and matches the expected
   SPDX license text; and
4. an independent Git diff has the expected file, hunk, added-line, and
   deleted-line counts.

Acquisition writes a versioned manifest in the benchmark workspace. `acquire` does
network work without analysis. `run` defaults to already acquired objects;
`run --acquire` refreshes them. `suite` combines the two phases intentionally.

## Complete measured path

Cold and warm runs each execute the same complete product path:

1. `shiftory analyze` creates canonical evidence JSON;
2. the version-controlled golden explanation template maps conceptual hunks to
   the actual deterministic evidence IDs, assigns every changed line and non-text
   unit exactly one owner, and creates the explanation manifest;
3. `shiftory verify` validates schemas, citations, and complete ownership; and
4. `shiftory render` produces the final JSON and human-facing Markdown reports.

Every Shiftory subprocess uses the current Python interpreter in isolated mode,
without a shell, with a runner-built environment and the repository's `src`
directory inserted first in its import search path. Before any measured run, the
runner resolves `shiftory.__file__`, requires the repository-relative
`src/shiftory/__init__.py`, and hashes every Python file below that imported
package. This check is repeated after the
run and before publication, so ambient `PYTHONPATH`, editable installs, the
invoking working directory, and other checkouts cannot change the measured code.

Evidence Markdown is rendered from that run's canonical evidence object by the
same isolated, sanitized, repository-`src`-bound subprocess mechanism. The runner
requires the imported renderer to be the repository-relative
`src/shiftory/render/evidence.py`, verifies its SHA-256 and the subprocess's
full package-code digest against the recorded execution identity, and records
that renderer identity with each artifact run. Publication repeats these binding
checks, so ambient imports and `sys.modules` cannot supply the renderer. A
scenario-specific cache is removed before the cold run and retained for the warm
run. Acquisition time is never included in complete-path wall time.

Published provenance names distributions, import roles such as `repository:src`,
repository-relative module paths, and content digests. Absolute home, worktree,
workspace, cache, installation, and `site-packages` paths and local usernames are
rejected before public metrics JSON or report Markdown is written.

## Measurements

`shiftory.benchmark-metrics/v1` records:

- Git input files, hunks, raw patch bytes, added and deleted lines;
- Shiftory spans, changed lines, and summed raw hunk patch bytes;
- complete-path and per-phase wall time for cold and warm runs;
- byte sizes and SHA-256 digests for evidence JSON and Markdown, instantiated
  explanation JSON, verification JSON, assertion JSON, and report JSON and
  Markdown;
- the full Shiftory commit and tree SHA, clean-worktree flag, deterministic
  per-file implementation manifest and digest, runner digest, combined golden
  input digest, repository-relative imported package file, digest and per-file
  manifest of the actual imported package code, evidence renderer package name,
  repository-relative module path and digest bound to that package-code identity,
  and declared and installed
  package/dependency versions;
- cache existence, file inventory, graph status, and graph cache key before and
  after each run;
- verified line, span, hunk, and unit coverage;
- every machine-readable assertion result; and
- UTC time, OS, CPU, memory, Python, Git, Shiftory version and commit, dependency
  versions, and worktree state.

No aggregation hides the two observations: cold and warm raw values are reported
separately.

## Assertions

The golden assertion manifest is evaluated against the exact base and head blobs.
Every `file_exists`, `contains`, and `not_contains` condition becomes an individual
machine-readable pass or fail. The runner also asserts evidence commit identity
and inventory, verifies that the named behavior has resolving evidence citations,
and confirms that the item appears in the corresponding final report section.

When graph facts are available, their identity, side, and provenance are checked.
When graph enrichment is disabled or unavailable, that check is explicitly
recorded as skipped rather than passed. Shiftory verification itself is an
assertion. Any failure aborts the scenario and prevents publication.

## Determinism

Cold and warm semantic bundles are canonicalized and hashed. The bundle includes
product artifact semantics, assertion results, graph facts, verified Graphora
distribution/artifact/package-code facts, and the verified Shiftory package,
evidence renderer, benchmark runner, and golden-input digests. Graphora module
location and direct install URL, absolute workspace/cache/install paths,
timestamps, and acquisition or run timings are deliberately outside it. Raw
artifact digests remain recorded separately. The two semantic hashes must match,
including when the same verified code runs from different local locations. This
comparison is stricter than comparing only rendered Markdown.

## Running and publishing

Python 3.11 or newer and the project environment are required.
The runner fails closed unless the declared `graphora-kg==0.2.1` dependency and
the installed `graphora-kg==0.2.1` and `tree-sitter==0.25.2` distributions match
the release environment.

```console
python benchmarks/runner.py acquire
python benchmarks/runner.py run click-optional-flag-value
python benchmarks/runner.py suite
python benchmarks/runner.py suite --publish
```

Use two stages for a release. First, while implementation changes are still
uncommitted, run `suite` without `--publish`; dirty-worktree results are local
validation only. Then commit the implementation, ensure `git status --porcelain`
is empty, and run the complete suite again with `--publish` before pushing.
Publication checks cleanliness before running and again before writing, and
rejects stale results whose implementation identity differs from the current
commit. It also recomputes the isolated execution identity and rejects results
whose executed package-code identity differs from either `ROOT/src` or the clean
repository implementation identity.

Artifacts are retained below the selected workspace (by default the user cache
directory). Only `suite --publish` writes versioned `metrics-v1.json` and the
actual human-facing `report.md` below `docs/benchmarks/<scenario>/`. These files
are generated release evidence: do not edit them manually or copy in results from
a non-publishing run. Publication starts only after all three public scenarios
have passed in the same suite invocation. A failed or partial suite does not
publish claimed results. Review and commit the generated report changes after the
clean publication run.

## Optional manual quality review

Published reports use version-controlled golden explanation templates; the suite
does not measure model-authored prose quality. Human quality scoring is separate
from correctness gates, optional, and never required by CI. Evaluations may use
`benchmarks/quality-rubric.schema.json`. A completed record must identify the
evaluator, model (if any), evaluation environment, scenario and artifact digests,
numeric ratings, and notes. Manual ratings must not replace or alter machine
assertion results.

## Limitations

Wall time applies only to the recorded machine and environment. Literal golden
assertions can miss behavior outside their selected facts. Graph availability
depends on the installed provider. Public hosts may later stop serving historical
objects even though the pinned identities remain immutable.
