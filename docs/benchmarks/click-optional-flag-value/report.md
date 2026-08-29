# Click optional values for non-flag options

Scenario: `click-optional-flag-value`

Comparison: `7f7bbe4569ea68e8dabee232eade069ef3310aea..91de59c6c8abc8251e7af551cd4546cc964288af`

## Measured results

| Measure | Cold | Warm |
|---|---:|---:|
| Complete path wall time (seconds) | 3.124221 | 1.115434 |
| Evidence JSON (bytes) | 67082 | 67082 |
| Evidence Markdown (bytes) | 33774 | 33774 |
| Shiftory report Markdown (bytes) | 5320 | 5320 |

## Input and accounting

- 3 files, 5 hunks, 8 spans, 49 added and 3 deleted lines
- Raw Git patch: 3672 bytes
- Coverage: lines 52/52, hunks 5/5, units 3/3
- Assertions: 31 passed, 0 failed, 0 skipped
- Canonical repeatability (environment and timing excluded): **passed**

## Cache facts

| Run | Before files | After files | Graph status |
|---|---:|---:|---|
| Cold | 0 | 294 | available |
| Warm | 294 | 294 | available |

## Assertion results

| Assertion | Domain | Status |
|---|---|---|
| `evidence-base-sha` | evidence | **pass** |
| `evidence-head-sha` | evidence | **pass** |
| `verify-valid` | report | **pass** |
| `evidence-inventory-files` | evidence | **pass** |
| `evidence-inventory-hunks` | evidence | **pass** |
| `evidence-inventory-added_lines` | evidence | **pass** |
| `evidence-inventory-deleted_lines` | evidence | **pass** |
| `optional-value-decision:before:file-exists` | before-source | **pass** |
| `optional-value-decision:before:contains:1` | before-source | **pass** |
| `optional-value-decision:before:not-contains:1` | before-source | **pass** |
| `optional-value-decision:after:file-exists` | after-source | **pass** |
| `optional-value-decision:after:contains:1` | after-source | **pass** |
| `optional-value-decision:after:not-contains:1` | after-source | **pass** |
| `optional-value-decision:evidence-citations` | evidence | **pass** |
| `optional-value-decision:report-section` | report | **pass** |
| `optional-value-contract-test:before:file-exists` | before-source | **pass** |
| `optional-value-contract-test:before:contains:1` | before-source | **pass** |
| `optional-value-contract-test:before:not-contains:1` | before-source | **pass** |
| `optional-value-contract-test:after:file-exists` | after-source | **pass** |
| `optional-value-contract-test:after:contains:1` | after-source | **pass** |
| `optional-value-contract-test:after:contains:2` | after-source | **pass** |
| `optional-value-contract-test:evidence-citations` | evidence | **pass** |
| `optional-value-contract-test:report-section` | report | **pass** |
| `optional-value-type-conversion:before:file-exists` | before-source | **pass** |
| `optional-value-type-conversion:before:not-contains:1` | before-source | **pass** |
| `optional-value-type-conversion:after:file-exists` | after-source | **pass** |
| `optional-value-type-conversion:after:contains:1` | after-source | **pass** |
| `optional-value-type-conversion:after:contains:2` | after-source | **pass** |
| `optional-value-type-conversion:evidence-citations` | evidence | **pass** |
| `optional-value-type-conversion:report-section` | report | **pass** |
| `graph-fact-integrity` | graph | **pass** |

## Reproduction envelope

- Recorded: 2026-08-29T14:32:02.806696+00:00
- OS: `macOS-15.6.1-arm64-arm-64bit`
- Python: `3.11.6 (main, Jan 28 2025, 18:28:48) [Clang 16.0.0 (clang-1600.0.26.6)]`
- Git: `git version 2.53.0`
- Shiftory: `shiftory 0.1.0` at `bc7c1219265963f2d29a6bd93ca2964900b12c7b`
- Clean committed source: `True`
- Implementation manifest: `2e7817ae6081efe95c3f97c063f0a5e1d388612935987ac66d811fb3aad11f11`
- Executed package code: `d26e37f5836f3d3c55fe19aaef94b2c7ead34f7012038fcee9c40d8096f4cec0`
- Imported package: `shiftory` from `repository:src` (`src/shiftory/__init__.py`)
- Evidence Markdown renderer: `shiftory.render.evidence` (`src/shiftory/render/evidence.py`; `aca36323dfd709308c375325bc52544073cc06fd7162f098f0fc6cbab6b23f00`)
- Benchmark runner: `19e274efa61890b76d759bec059788e6fab4d9e6dace25727a80ddb154437073`
- Golden inputs: `f1fd72c75085f3a989cb9b11438baa764a103eece3148e9b55ccc062866a25ac`

## Rendered Shiftory report
## Shiftory explanation

A non-flag option with a flag value can be invoked without a following argument, and the contract is documented and exercised.

### Behavioral before to after

#### Recognize the flag value as an argument-free invocation

**Before:** The needs-value decision depended only on whether the default was unset.

**After:** A configured flag value also marks the option as usable without a following argument.

Option initialization now includes flag_value in the decision that controls argument consumption.

Evidence: `hunk_93bb7674389ee282406eba81`

Confidence: **extracted**

### Structural and non-behavioral changes

#### Describe the optional-value contract

**Before:** The changelog and Option API notes did not describe this invocation form.

**After:** The changelog and Option API notes state when a flag can be accepted without an argument.

Release-facing and API-facing text records the same option behavior.

Evidence: `hunk_c161004f571abe7de89d0c92`, `hunk_61a5a6bceb0b40f5d37580ca`

Confidence: **extracted**

### Who observes the changes

#### Exercise string and converted flag values

**Before:** The option suite did not contain these two optional-value examples.

**After:** The suite invokes a string flag value and an integer-converted flag value without following arguments.

The CLI runner observes both direct substitution and type conversion.

Evidence: `hunk_6935d92d966a2baf5b8ca493`, `hunk_ff4e4f46e256eae15a480540`

Confidence: **extracted**

### Ambiguity and unresolved notes

_None._

### Complete source-cited coverage appendix

- Changed lines: 52/52 (100%)
- Change spans: 8/8 (100%)
- Textual hunks: 5/5 (100%)
- Change units: 3/3 (100%)
- Valid citation references: 5

Spans without a direct entry inherit the unanimous owner of their changed lines.

| Directly owned evidence | Coverage owner |
|---|---|
| `line_142d549fa3b5c0e27a451d9f` | `optional-value-contract` |
| `line_1b49830682daebb5001bd522` | `optional-value-contract` |
| `line_2a995b8267350d6866c0f760` | `optional-value-contract` |
| `line_30837327cf971a5c33a4b33d` | `optional-value-contract` |
| `line_32483ca21c556966e8a16f10` | `optional-value-documentation` |
| `line_34a51acc86ce1032482cce2a` | `optional-value-documentation` |
| `line_34ffb20b7cc86c907987a377` | `optional-value-contract` |
| `line_3957d0a63fd3f082f3426362` | `optional-value-contract` |
| `line_395b55dd6e31ae3f8d3e55b3` | `optional-value-parsing` |
| `line_3c0f4b827273926fbba8ca69` | `optional-value-contract` |
| `line_3cdc18842ba0c7557f8b9c3e` | `optional-value-contract` |
| `line_3ed24f15cb71a5363b6b0ac4` | `optional-value-parsing` |
| `line_43af1ccb61f0169d62d106e8` | `optional-value-documentation` |
| `line_46e05ace545f93a62b5cf6b7` | `optional-value-contract` |
| `line_47d63b4c8ea90f67d2022b04` | `optional-value-contract` |
| `line_4864b332d1abea5020b5eb0b` | `optional-value-parsing` |
| `line_4b8a0df85bf840b5e0e398d2` | `optional-value-documentation` |
| `line_4ffd3959f0e1b7940bd259e8` | `optional-value-contract` |
| `line_5159ed0bab8cb591624002ed` | `optional-value-contract` |
| `line_5180ff6287004b48cebc14c1` | `optional-value-contract` |
| `line_5729c4e9a3b107431603d5fa` | `optional-value-contract` |
| `line_5acbb3e05a32335c3c05adcd` | `optional-value-contract` |
| `line_5da112bb12bd156aaffcdf30` | `optional-value-contract` |
| `line_5e3eb559099b94c171eaf691` | `optional-value-contract` |
| `line_63cc1c4e4854f1bc72735684` | `optional-value-parsing` |
| `line_63fd6eb4fc4c62e7b051813a` | `optional-value-contract` |
| `line_656fad5b94586f1c2599875d` | `optional-value-contract` |
| `line_6817d9f47ce47e1a7eff55d8` | `optional-value-contract` |
| `line_7e3022b439e962185b1d0cb2` | `optional-value-parsing` |
| `line_809ccb515b47483af091a64c` | `optional-value-contract` |
| `line_80b8efffbc3ba0b474e9b219` | `optional-value-contract` |
| `line_871192dc53147a466f330917` | `optional-value-parsing` |
| `line_93b04fde629c1f562c4ef8f1` | `optional-value-parsing` |
| `line_97f16276faad7761e0dc6acb` | `optional-value-contract` |
| `line_9bad1a57e84204ebef0db21b` | `optional-value-documentation` |
| `line_ac401bc3f1e9f5a7dfff3125` | `optional-value-contract` |
| `line_ae92619bd5d0e0ab09e479e6` | `optional-value-parsing` |
| `line_b299aa7c930cc972ed57d565` | `optional-value-documentation` |
| `line_b44b418554fb1637c1f702f3` | `optional-value-contract` |
| `line_bb9343218c2107bb0e2ef464` | `optional-value-contract` |
| `line_c32b3d5491c1e99b825ed141` | `optional-value-contract` |
| `line_c9a43f81516a1c26bebd1fa9` | `optional-value-contract` |
| `line_ca9bb098038ebe690b342d86` | `optional-value-documentation` |
| `line_cf258cee5a95fc8f868ffbde` | `optional-value-contract` |
| `line_d02b5073fe50bec415a01d55` | `optional-value-contract` |
| `line_d2189e5bca52f5c1a78d49c6` | `optional-value-contract` |
| `line_d82e6202d483600ae43eec6a` | `optional-value-parsing` |
| `line_dac8d2d8b13aaeb39ffd113f` | `optional-value-contract` |
| `line_eeec276d953e82781a0565f7` | `optional-value-documentation` |
| `line_f95277658b344b6c975bae10` | `optional-value-documentation` |
| `line_f9f405a7095e35c566ea05f5` | `optional-value-contract` |
| `line_fd375cbe11b556d7adcd2671` | `optional-value-contract` |

> Shiftory verified accounting and citation references; it does not verify semantic correctness.
