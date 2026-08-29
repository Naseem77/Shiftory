# Contributing to Shiftory

Shiftory's core contract is exact change accounting with an explicit
explanation-not-review boundary. Contributions must preserve both.

## Set up

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Keep changes focused. Do not mix schema, stable-identity, generated benchmark, or
golden-output changes with unrelated cleanup.

## Local checks

Run the narrowest relevant tests while developing, then the complete local gate:

```bash
ruff format --check .
ruff check .
mypy src/shiftory
pytest
python -m build
python -m twine check dist/*
```

Also smoke-test the built wheel in a clean environment when package data, CLI
entry points, schemas, or the bundled skill changes. The installed wheel must
contain all three JSON schemas, `py.typed`, and the bundled `SKILL.md`.

If the repository provides documentation-link, license/SBOM, or offline benchmark
commands, run those commands exactly as CI does. Network-fetched release
benchmarks are release gates, not substitutes for unit or integration tests.

## CI expectations

A change is ready only when CI confirms:

1. formatting and linting;
2. strict static typing;
3. unit, integration, end-to-end, golden, and regression tests;
4. exact changed-line/span/hunk/unit accounting and policy cases;
5. deterministic output on repeat runs where applicable;
6. source and wheel builds plus clean-wheel CLI/skill smoke tests;
7. bundled schema and documentation-link validation;
8. dependency license inventory/SBOM checks; and
9. the configured offline benchmark smoke test.

Do not weaken a gate, lower coverage, or relabel an unavailable dependency as
success to make CI pass.

## Behavioral rules

### Explain; do not review

User-facing explanations describe before/after behavior, structural changes,
observers, ambiguity, and unresolved meaning. They must not produce findings,
severity judgments, risk rankings, defect declarations, or recommended fixes.

When changing policy validation:

- add positive tests for the disallowed communicative intent;
- add negative tests showing that identifiers, paths, quoted source, and faithful
  descriptions remain legal; and
- avoid raw word deny lists.

### Git remains authoritative

Do not make Graphora, an agent, checkout contents, or a generated report
authoritative for comparison identity or coverage. Graphora failures may degrade
to explicit unavailable evidence in auto mode, but never to silent empty success.

### Preserve exact ownership

Every parsed changed line and span, plus every non-text unit, must have exactly
one explanation owner. Textual hunk and unit coverage derives from owned
descendant lines. Citations are reusable and must remain independent from
ownership.

Tests for accounting changes must cover missing owners, duplicate owners, reused
citations, empty diffs, and relevant non-text units.

## Schema and identity compatibility

The schemas are public contracts:

- do not silently change the meaning of a v1 field;
- keep `additionalProperties` behavior deliberate;
- update models, serializers, validators, renderers, schema files, tests, and
  package-data checks together;
- preserve deterministic ordering and timestamp-free canonical payloads; and
- use a new schema version for an incompatible contract change.

Stable IDs are also compatibility-sensitive. Changing canonical payloads,
normalization, hashing, or parent relationships invalidates evidence references.
Document the migration and add before/after fixtures rather than treating such a
change as an internal refactor.

## Tests and goldens

Tests are organized by purpose:

- `tests/unit`: deterministic logic and edge cases;
- `tests/integration`: Git scopes, snapshots, cache, and real adapter contracts;
- `tests/e2e`: installed-style CLI/skill flows;
- `tests/golden`: deterministic rendering; and
- `tests/regression`: previously discovered failures.

Add a regression test for every corrected parser or accounting defect. Golden
changes must be reviewed as behavior, not blindly regenerated. In the pull
request, explain why each changed line of expected output follows from the
contract.

Do not add nondeterministic timestamps, random IDs, machine-specific absolute
paths, or network dependencies to normal golden tests.

## Graphora changes

Shiftory is pinned to `graphora-kg==0.2.1` and integrates through a narrow public
API adapter. Changes must:

- keep Graphora models behind Shiftory-owned types;
- use the embedded backend and repository-scoped cache;
- validate normalized paths and line references;
- retain parser provenance and honest confidence;
- describe static tests as relationships, never runtime coverage; and
- test both graceful auto-mode degradation and required-mode failure.

Dependency upgrades require adapter contract tests, deterministic repeat runs,
license metadata review, and updated third-party attribution.

## Documentation

Documentation must match shipped behavior and clearly distinguish guarantees from
plans. Label synthetic output as illustrative. Do not claim benchmark numbers,
language semantics, privacy properties, or CI gates that have not been verified.
Use relative links for repository documents and run the existing link checker
when present.

## Benchmarks

Do not hand-edit generated benchmark reports or invent missing results. When a
benchmark harness and scenario manifest are present, change scenarios or expected
claims in their source files, run the full documented path, and commit only
truthful generated output.

Benchmark evaluation must keep these categories separate:

- deterministic engine facts: ownership, hunk/unit accounting, references,
  assertions, byte size, runtime, cache behavior, and repeatability; and
- optional prose assessment: organization, clarity, faithfulness, uncertainty,
  and absence of review language.

Record evaluator, model, environment, and tool versions for any manual or agent
quality evaluation. Paid or nondeterministic services must not become correctness
gates.

## Pull requests

Include:

- the behavior or contract changed;
- tests and exact commands run;
- schema, stable-ID, cache, privacy, and compatibility effects;
- any unavailable checks with a concrete reason; and
- generated artifacts, if any, with the command that produced them.

Keep commits reviewable and never include credentials, local caches, explain run
directories, build output, or unrelated workspace files.
