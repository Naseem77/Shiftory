# Limitations

Shiftory has a deliberately narrow guarantee: deterministic accounting and
reference validation for a Git comparison. It is not a semantic verifier or code
reviewer.

## What verification proves

For evidence that Shiftory successfully parsed, verification proves:

- every changed line and span has exactly one explanation owner;
- every non-text unit has exactly one owner;
- every text hunk and text unit has complete descendant-line ownership;
- all citation IDs exist;
- evidence and explanation match their v1 schemas; and
- the manifest does not use supported review/judgment structures.

It does **not** prove:

- that a prose statement is true or complete;
- that a changed function behaves as the agent describes at runtime;
- that observer/blast-radius claims include all dynamic consumers;
- that a change is correct, safe, compatible, or performant; or
- that no defect, security issue, or operational concern exists.

Shiftory intentionally does not produce bug findings, severity, risk rankings, or
recommended fixes. Use separate review and testing systems for those jobs.

## Git and content boundaries

Git patches are the accounting source. Binary changes, mode changes, renames,
copies, submodules, and unsupported records can be represented and owned, but
binary behavior cannot be interpreted from source text.

Text decoding uses UTF-8 replacement for patch display. Source-range validation
operates on byte content split into lines; non-UTF-8 meaning may not be faithfully
expressed in prose. Symlinks are not copied into Graphora working snapshots.

Ignored files are excluded from the default untracked inventory. External diff
drivers and text conversion are disabled, so Shiftory may intentionally differ
from a developer's customized `git diff` view.

The working tree is mutable. Comparison fingerprints identify the state seen
during analysis, but Shiftory does not freeze user files. Re-run analysis after
edits instead of combining an old evidence packet with a new working state.

Merge commits require explicit `--parent N`. Pull request resolution requires an
authenticated GitHub CLI and may fetch objects. Other comparison modes are local
and do not repair missing Git history automatically.

## Graphora enrichment

Shiftory pins `graphora-kg==0.2.1` and uses its embedded backend. Graphora is
optional and never authoritative for Git identities, changed-line accounting, or
coverage.

Graphora 0.2.1 recognizes these file families:

- Python (`.py`)
- JavaScript and JSX (`.js`, `.jsx`)
- TypeScript and TSX (`.ts`, `.tsx`)
- Go (`.go`)
- Java (`.java`)
- Rust (`.rs`)
- C (`.c`, `.h`)
- C++ (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`)
- Ruby (`.rb`)
- PHP (`.php`)

It attempts tree-sitter parsing and can fall back to regular expressions. A
fallback has lower semantic precision, and Shiftory downgrades extracted
definition confidence to inferred. If parser provenance is not observable,
Shiftory labels it unknown rather than claiming tree-sitter extraction.

Graphora relationships are static and primarily name-based. In particular:

- bare-name resolution can conflate same-named symbols or leave them unresolved;
- callers and callees do not model complete runtime dispatch;
- imports do not establish that code executed;
- “static tests” are call relationships involving test-like paths, not executed
  tests and not runtime coverage;
- reflection, metaprogramming, generated code, dynamic imports, dependency
  injection, inheritance, overload/type resolution, aliases, callbacks, data
  flow, and control flow may be incomplete or absent; and
- a definition's source position does not by itself establish that the changed
  line altered that definition's behavior.

`--graphora auto` records failures as unavailable and preserves Git accounting.
`--graphora required` fails the command. Unsupported languages still receive the
Git ledger, classification, and source citations, with structural semantics
unresolved or unavailable.

Graphora runs in a bounded, sanitized subprocess because its native tree-sitter
extensions can fail below Python's exception boundary. A worker signal, nonzero
exit, timeout, or invalid response is treated like any other Graphora failure and
cannot terminate the main Shiftory process. This isolation does not make an
unavailable graph complete; it preserves only the authoritative Git accounting.

Shiftory accepts Graphora enrichment only from a non-editable installed artifact
whose module origin, package inventory, and recorded file hashes agree. Provider
provenance (version, module path, direct-URL/editable metadata, and package-code
digest) is retained with success or failure diagnostics. Development checkouts
must be built and installed as an artifact before they can provide graph facts.

## Confidence is not probability

`extracted`, `inferred`, `ambiguous`, `unresolved`, and `unavailable` are
provenance/epistemic categories, not calibrated probabilities. An extracted fact
can be faithfully read from source and still be irrelevant to runtime behavior.
An inferred statement can be correct but is not upgraded merely because it sounds
confident.

Agent-authored prose is bounded by the evidence chunks and retrieved ranges the
agent reads. The validator can reject obvious uncertainty mislabeled as extracted,
but it cannot generally detect hallucinated or misleading prose.

## Policy validation is bounded

The explanation-not-review check validates known fields, item kinds, and sentence
patterns. It is designed to allow domain identifiers and quoted source that
contain words such as “risk” or “bug.” Consequently, it is neither a general
natural-language classifier nor a guarantee that every possible judgmental
paraphrase is rejected.

## Evidence size

Direct `analyze` remains a v1 compatibility surface. It can omit citation text, but
when the mandatory ledger itself exceeds `--max-evidence-bytes` it records a
diagnostic and remains larger than the requested threshold.

The low-friction `explain` workflow handles that case with bounded v2 chunks and
recorded retrieval responses while retaining the complete authoritative ledger
outside agent payloads. It cannot split a replacement-linked span atom, non-text
unit, or individual source line. If the smallest such payload cannot fit, the run
fails with `chunk_budget_error` instead of weakening ownership or silently
exceeding the cap.

Byte metrics are exact canonical serialized counts. Token values use the documented
`ceil(bytes / 4)` estimate and are not model-tokenizer measurements.

## Privacy and retention

Git analysis and Graphora enrichment run locally. `--pr` uses `gh pr view` and
may fetch missing Git objects; all other comparison modes are network-free. The
Shiftory CLI has no telemetry and does not itself send source to an LLM. The
agent workflow intentionally gives the invoked agent bounded chunks and requested
recorded ranges, so that agent and its host's data-handling policy apply. Artifacts
remain sensitive:

- evidence embeds changed source text;
- v2 global ledgers and plans remain private run artifacts that the skill does not
  instruct the agent to read;
- explanation and report files can contain source-derived prose;
- Graphora cache snapshots can contain eligible tracked and non-ignored
  untracked repository files, not only changed files; and
- retained diagnostics can contain paths and error details.

Snapshot filtering excludes `.git`, symlinks in a working snapshot, the
configured cache root from a working snapshot, common credential filenames
(`.env`, `.npmrc`, `.pypirc`, SSH key names, and credential JSON), and common
private-key suffixes. It cannot recognize every secret or sensitive filename. Do
not rely on the filter as a secret scanner.

Repository caches persist until `shiftory cache clear` or external retention
removes them. Awaiting and failed explain runs persist for recovery. Successful
finalized runs are deleted by default, but `--keep-artifacts` and
`SHIFTORY_KEEP_ARTIFACTS=1` retain them. There is no automatic expiry.

The current cache lock uses POSIX advisory file locking (`fcntl`), so native
Windows support is not provided by this implementation.

Run hashes detect partial, stale, or accidental artifact modification; they are not
a signature against the local owner deliberately rewriting an entire private run
and all of its related hashes.

## Classification

File classifications use explicit filename, path, suffix, and Git metadata rules.
“Behavioral” means a source file is a behavioral candidate; it does not prove an
observable behavior change. “Structural” does not prove zero runtime impact.
Ambiguous files remain unresolved.

## Benchmarks

The [published benchmark summary](../README.md#public-benchmarks) reports one cold
and one warm complete-path observation for each of three pinned comparisons and
links the full immutable metrics and reports. Those wall times apply only to the
recorded machine and environment; they are not generalized performance claims.

The deterministic harness verifies accounting, artifact repeatability, and
selected golden assertions. It does not establish complete semantic correctness
or score model-authored prose. Published explanations use version-controlled
golden templates; prose-quality evaluation is separate, optional, and manual.
See the [benchmark methodology](benchmark-methodology.md).
