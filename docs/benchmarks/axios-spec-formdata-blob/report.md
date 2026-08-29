# Axios spec-compliant FormData and Blob request bodies

Scenario: `axios-spec-formdata-blob`

Comparison: `65e8d1e28ce829f47a837e45129730e541950d3c..6ac574e00a06731288347acea1e8246091196953`

## Measured results

| Measure | Cold | Warm |
|---|---:|---:|
| Complete path wall time (seconds) | 3.569890 | 1.290953 |
| Evidence JSON (bytes) | 230567 | 230567 |
| Evidence Markdown (bytes) | 119540 | 119540 |
| Shiftory report Markdown (bytes) | 25775 | 25775 |

## Input and accounting

- 9 files, 22 hunks, 48 spans, 304 added and 55 deleted lines
- Raw Git patch: 18001 bytes
- Coverage: lines 359/359, hunks 22/22, units 11/11
- Assertions: 71 passed, 0 failed, 0 skipped
- Canonical repeatability (environment and timing excluded): **passed**

## Cache facts

| Run | Before files | After files | Graph status |
|---|---:|---:|---|
| Cold | 0 | 440 | available |
| Warm | 440 | 440 | available |

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
| `adapter-formdata-dispatch:before:file-exists` | before-source | **pass** |
| `adapter-formdata-dispatch:before:contains:1` | before-source | **pass** |
| `adapter-formdata-dispatch:before:not-contains:1` | before-source | **pass** |
| `adapter-formdata-dispatch:after:file-exists` | after-source | **pass** |
| `adapter-formdata-dispatch:after:contains:1` | after-source | **pass** |
| `adapter-formdata-dispatch:after:contains:2` | after-source | **pass** |
| `adapter-formdata-dispatch:evidence-citations` | evidence | **pass** |
| `adapter-formdata-dispatch:report-section` | report | **pass** |
| `adapter-blob-dispatch:before:file-exists` | before-source | **pass** |
| `adapter-blob-dispatch:before:contains:1` | before-source | **pass** |
| `adapter-blob-dispatch:before:not-contains:1` | before-source | **pass** |
| `adapter-blob-dispatch:after:file-exists` | after-source | **pass** |
| `adapter-blob-dispatch:after:contains:1` | after-source | **pass** |
| `adapter-blob-dispatch:after:contains:2` | after-source | **pass** |
| `adapter-blob-dispatch:after:contains:3` | after-source | **pass** |
| `adapter-blob-dispatch:evidence-citations` | evidence | **pass** |
| `adapter-blob-dispatch:report-section` | report | **pass** |
| `native-formdata-selection:before:file-exists` | before-source | **pass** |
| `native-formdata-selection:before:contains:1` | before-source | **pass** |
| `native-formdata-selection:before:contains:2` | before-source | **pass** |
| `native-formdata-selection:after:file-exists` | after-source | **pass** |
| `native-formdata-selection:after:contains:1` | after-source | **pass** |
| `native-formdata-selection:evidence-citations` | evidence | **pass** |
| `native-formdata-selection:report-section` | report | **pass** |
| `multipart-stream-encoding:before:file-exists` | before-source | **pass** |
| `multipart-stream-encoding:after:file-exists` | after-source | **pass** |
| `multipart-stream-encoding:after:contains:1` | after-source | **pass** |
| `multipart-stream-encoding:after:contains:2` | after-source | **pass** |
| `multipart-stream-encoding:after:contains:3` | after-source | **pass** |
| `multipart-stream-encoding:evidence-citations` | evidence | **pass** |
| `multipart-stream-encoding:report-section` | report | **pass** |
| `blob-reader-fallbacks:before:file-exists` | before-source | **pass** |
| `blob-reader-fallbacks:after:file-exists` | after-source | **pass** |
| `blob-reader-fallbacks:after:contains:1` | after-source | **pass** |
| `blob-reader-fallbacks:after:contains:2` | after-source | **pass** |
| `blob-reader-fallbacks:after:contains:3` | after-source | **pass** |
| `blob-reader-fallbacks:evidence-citations` | evidence | **pass** |
| `blob-reader-fallbacks:report-section` | report | **pass** |
| `shared-formdata-predicate:before:file-exists` | before-source | **pass** |
| `shared-formdata-predicate:before:contains:1` | before-source | **pass** |
| `shared-formdata-predicate:before:not-contains:1` | before-source | **pass** |
| `shared-formdata-predicate:after:file-exists` | after-source | **pass** |
| `shared-formdata-predicate:after:contains:1` | after-source | **pass** |
| `shared-formdata-predicate:after:contains:2` | after-source | **pass** |
| `shared-formdata-predicate:evidence-citations` | evidence | **pass** |
| `shared-formdata-predicate:report-section` | report | **pass** |
| `node-formdata-construction:before:file-exists` | before-source | **pass** |
| `node-formdata-construction:before:contains:1` | before-source | **pass** |
| `node-formdata-construction:before:not-contains:1` | before-source | **pass** |
| `node-formdata-construction:after:file-exists` | after-source | **pass** |
| `node-formdata-construction:after:contains:1` | after-source | **pass** |
| `node-formdata-construction:after:contains:2` | after-source | **pass** |
| `node-formdata-construction:evidence-citations` | evidence | **pass** |
| `node-formdata-construction:report-section` | report | **pass** |
| `adapter-observers:before:file-exists` | before-source | **pass** |
| `adapter-observers:before:contains:1` | before-source | **pass** |
| `adapter-observers:before:not-contains:1` | before-source | **pass** |
| `adapter-observers:after:file-exists` | after-source | **pass** |
| `adapter-observers:after:contains:1` | after-source | **pass** |
| `adapter-observers:after:contains:2` | after-source | **pass** |
| `adapter-observers:after:contains:3` | after-source | **pass** |
| `adapter-observers:evidence-citations` | evidence | **pass** |
| `adapter-observers:report-section` | report | **pass** |
| `graph-fact-integrity` | graph | **pass** |

## Reproduction envelope

- Recorded: 2026-08-29T14:31:57.423571+00:00
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

The Node adapter accepts spec-compliant FormData and Blob bodies, streams their bytes with computed headers, and retains the legacy form-data path.

### Behavioral before to after

#### Route FormData and Blob bodies through Node streams

**Before:** The adapter recognized the package-specific form-data API and ordinary buffered or streamed bodies.

**After:** The adapter separately recognizes spec-compliant FormData, legacy form-data, and Blob bodies and supplies corresponding headers and streams.

Body-type dispatch now selects multipart encoding or Blob reading before the existing buffered-body path.

Evidence: `hunk_26724fb70efdd23a4622442a`, `hunk_77865a6441644d2168682ab6`, `hunk_256f677bbb19fd8c5773bbff`, `hunk_35c2f04b8346481a771a1004`

Confidence: **extracted**

#### Encode multipart parts and Blob bytes incrementally

**Before:** No internal helpers serialized spec-compliant FormData or adapted Blob reading strategies.

**After:** New helpers encode multipart boundaries, part headers, content length, and Blob bytes as readable streams.

Multipart output is assembled from each form entry and exposes computed content headers to the adapter.

Evidence: `hunk_bea98f40ae8db4cb556bb19d`, `hunk_e5c18c442bd037a94af6076e`

Confidence: **extracted**

### Structural and non-behavioral changes

#### Share FormData detection and platform construction

**Before:** toFormData owned its spec-compliance predicate and imported the environment FormData class directly.

**After:** The predicate and boundary alphabet utilities are shared, while Node FormData construction uses the platform class.

Form conversion and HTTP dispatch use the same spec-compliant FormData test.

Evidence: `hunk_8b117b8b4ac972dd62d10e45`, `hunk_a315162b719aa6795531aa79`, `hunk_ec2ac586b953e2bcdfbf4bb2`, `hunk_367c1e503905ebfd5ac3ea06`, `hunk_b1aa25872e920919317c9418`, `hunk_831dac4c868450dedd2a4041`, `hunk_a75ceeae0f687be9f00ea9c1`

Confidence: **extracted**

#### Wire the test and browser environments

**Before:** The tool configuration targeted ES2017 and did not include the FormData polyfill or browser mapping for the Node FormData class.

**After:** The configuration targets ES2018, provides a FormData test polyfill, and maps the Node-only class away in browser builds.

Development and package metadata support the new runtime paths without vendoring their source.

Evidence: `hunk_20d2b6a164ca013da6a67017`, `hunk_c8fe7df0f6c878b162893f9f`, `hunk_a5d8c9092a60090379671c47`, `hunk_7b146d3392b369d9ba08bb83`

Confidence: **extracted**

### Who observes the changes

#### Observe legacy FormData, spec FormData, and Blob uploads

**Before:** The adapter test covered the package-specific form-data body.

**After:** The test distinguishes the legacy body and observes multipart fields, a file part, and a standalone Blob body.

HTTP test servers parse or echo each body form and compare the received values.

Evidence: `hunk_3f8a358cd8e7369d956529d8`, `hunk_53f6d8cc32d3fa3eec432193`, `hunk_0afcf0aa56b0b0de088b6147`, `hunk_a45d5eb8b54d7f844947b6a4`, `hunk_dd5672da659bc59f862ca23e`

Confidence: **extracted**

### Ambiguity and unresolved notes

_None._

### Complete source-cited coverage appendix

- Changed lines: 359/359 (100%)
- Change spans: 48/48 (100%)
- Textual hunks: 22/22 (100%)
- Change units: 11/11 (100%)
- Valid citation references: 22

Spans without a direct entry inherit the unanimous owner of their changed lines.

| Directly owned evidence | Coverage owner |
|---|---|
| `line_00b5d775b3fbaeea9cb97f6b` | `multipart-encoding` |
| `line_00e9ac3975317fad6f3f39cd` | `request-body-observers` |
| `line_02bb9e342d03d4318cf652ef` | `request-body-observers` |
| `line_03a0e9506b5ecff02d774b03` | `request-body-observers` |
| `line_044ee4b1bfc4a7f44b127ba0` | `multipart-encoding` |
| `line_04652bdc03679e0c9ce12524` | `request-body-observers` |
| `line_04d52da3c75f0f4907d1cee0` | `multipart-encoding` |
| `line_053c3a729e5c5b49e41b0274` | `multipart-encoding` |
| `line_06ea84998385c4b1ab46f127` | `formdata-normalization` |
| `line_07f06923886fbb1ffc9df7d9` | `multipart-encoding` |
| `line_08895b6d3991d479fc62a9c4` | `request-body-observers` |
| `line_0991eca871623a9f641fe6bc` | `multipart-encoding` |
| `line_0a1f9eeaa0ca9975460a21e0` | `multipart-encoding` |
| `line_0a462e404397f553b166120d` | `formdata-normalization` |
| `line_0ac5d903c6dba0c93b11746c` | `multipart-encoding` |
| `line_0b73840f3b42cf21e8a8f455` | `formdata-normalization` |
| `line_0c5857a21ef51952bd9348fa` | `formdata-normalization` |
| `line_0d696f1356060b3cb4ec551b` | `multipart-encoding` |
| `line_0da2fbc39cc394789de17aed` | `multipart-encoding` |
| `line_0f0d513b4e50fe3840303a03` | `request-body-dispatch` |
| `line_0f20656a8005730c6b1fbd11` | `multipart-encoding` |
| `line_0f684d8ed993409485a88d1b` | `multipart-encoding` |
| `line_10870bfa8e7761b208fb2cff` | `multipart-encoding` |
| `line_11197db0b66dc6c36299f23f` | `multipart-encoding` |
| `line_11ba1ab9fbc69a30da134fd1` | `formdata-normalization` |
| `line_11c76ba000415ca8ad349c94` | `request-body-observers` |
| `line_1221b623ca6c89fbbc367417` | `multipart-encoding` |
| `line_1336223390a9b3e47c657167` | `formdata-normalization` |
| `line_133eaa3ce62ce5bc517269cd` | `multipart-encoding` |
| `line_13cb6372f4c6ede8d8eb1cd6` | `request-body-observers` |
| `line_15beda18167b270f03aa08b1` | `request-body-dispatch` |
| `line_1745fbc84b775355a7f39674` | `request-body-dispatch` |
| `line_1753bd6d0c2f35288bb4ad94` | `request-body-observers` |
| `line_179e3a31d82639c419a22a65` | `multipart-encoding` |
| `line_17dabbbcfcfa9816e2acd27c` | `multipart-encoding` |
| `line_199d0e8804384766b5115a30` | `multipart-encoding` |
| `line_19c6db9c02b28eb80cd41687` | `request-body-observers` |
| `line_1a12de0677c0bd99c8086e53` | `multipart-encoding` |
| `line_1ab2023bfb34b70185a421f6` | `multipart-encoding` |
| `line_1bb1dae70a6c48409964ca8e` | `request-body-observers` |
| `line_1c4ef5229b0c520b7af9a668` | `request-body-dispatch` |
| `line_1ce8c23dc49d4007875adb3f` | `request-body-observers` |
| `line_1f835cf2ec9f8a40c854e606` | `request-body-observers` |
| `line_21034c9137fba534a4eeb088` | `formdata-normalization` |
| `line_2464d3ff6d374a76fdff2179` | `multipart-encoding` |
| `line_2573cf0bbd9716018dcd1d2e` | `formdata-normalization` |
| `line_25ebf023e1a81ddc68be1e7f` | `multipart-encoding` |
| `line_266c55380ac3f3868eceeb50` | `multipart-encoding` |
| `line_28424c4630c262fa2329b196` | `multipart-encoding` |
| `line_28b452d1368b38483f6fa2b6` | `request-body-observers` |
| `line_28e9a884afdce69f2a1706c1` | `request-body-observers` |
| `line_2943b9fce4705bd0add55540` | `multipart-encoding` |
| `line_2951bece5694e24e2661d906` | `environment-wiring` |
| `line_295cda020c17047dc6ac0c0d` | `multipart-encoding` |
| `line_2a1c6c077ad9a591c6136cd1` | `formdata-normalization` |
| `line_2ab42018145e5bef2da93aa0` | `multipart-encoding` |
| `line_2dc8e9a6de2f70a38ecf9706` | `request-body-observers` |
| `line_2e1c8e4156ae58edc2c80d7d` | `request-body-dispatch` |
| `line_2e2f777dda2a59964f120efa` | `formdata-normalization` |
| `line_2e6fab04992dc712509f30be` | `request-body-observers` |
| `line_2e710b6c242a356a62b7adbb` | `request-body-observers` |
| `line_326e8409320cee9ce6b852c2` | `request-body-observers` |
| `line_32cfb7f2b01b233f0554dc33` | `multipart-encoding` |
| `line_34d0448faf118ccdb24a0a78` | `request-body-observers` |
| `line_35110333d3353ad7f9c1def0` | `request-body-observers` |
| `line_3555f84f65ec504f828a91ad` | `request-body-dispatch` |
| `line_35bacce7fad4a2ec6f7a38b1` | `request-body-observers` |
| `line_35c92cef0d0227af34053213` | `environment-wiring` |
| `line_377da647e35493ca877d9097` | `multipart-encoding` |
| `line_37b70f68edcb28836f59c759` | `formdata-normalization` |
| `line_37f957047fa91d97879082a8` | `formdata-normalization` |
| `line_38c30116d31845e51b7abc41` | `multipart-encoding` |
| `line_38fa8fd26d57479bde8094cf` | `request-body-dispatch` |
| `line_390d808c3f19fc94666a676e` | `request-body-observers` |
| `line_393294bbadfdaa3d51f2ba24` | `multipart-encoding` |
| `line_3936617f4aefcd6351d918d6` | `formdata-normalization` |
| `line_3b8b28b2f24d410a937cad68` | `formdata-normalization` |
| `line_3cb7628228da5b1025911fd0` | `request-body-observers` |
| `line_3cbbc4ab6147751e22b00721` | `multipart-encoding` |
| `line_3d01da5b3c8e0e72c97e0b50` | `request-body-observers` |
| `line_3d278bdd7240732fcf325e10` | `request-body-observers` |
| `line_3d6dfdec4a46f89f241941ca` | `formdata-normalization` |
| `line_3ed83b50f21bf9d36863c992` | `multipart-encoding` |
| `line_3fa948a18a38dd208f6dbc13` | `multipart-encoding` |
| `line_4062f150e446a60f7a534909` | `request-body-observers` |
| `line_41d87f6e29e0c676f797b95b` | `request-body-observers` |
| `line_42f425148d3ad5be2d427a9a` | `request-body-dispatch` |
| `line_4361cf1126d1152f1299ce7d` | `formdata-normalization` |
| `line_439285ca88e54e3275666a01` | `formdata-normalization` |
| `line_439de687743e0ad096e36e90` | `request-body-observers` |
| `line_44d75287aaa80a7b95fc0d03` | `multipart-encoding` |
| `line_4518db4b700e9c1a41dee001` | `multipart-encoding` |
| `line_462d88ce3a9f2ddf0a44ffe0` | `request-body-observers` |
| `line_46627ff786a6e133d4232b15` | `environment-wiring` |
| `line_46ed6a35395076fed9b93d78` | `request-body-observers` |
| `line_4835234ab3776c07ac153e0a` | `request-body-observers` |
| `line_48358d1db809f24eaeff6b49` | `multipart-encoding` |
| `line_491874cbd8ef48e017103150` | `request-body-observers` |
| `line_4b5c4259f275ae22842c4d8b` | `multipart-encoding` |
| `line_4b6b650304c8028804e841c7` | `environment-wiring` |
| `line_4d25234ecfcca2c36f46b058` | `request-body-observers` |
| `line_4d270ebc311635c99c3f182f` | `multipart-encoding` |
| `line_4e1115b52dff620a97af4e60` | `request-body-observers` |
| `line_51bb1abdababf4046c2ef9a8` | `request-body-observers` |
| `line_522a403b409304bcde8bfeea` | `formdata-normalization` |
| `line_522ecc91d768cc404fa0007d` | `multipart-encoding` |
| `line_538388469c4db84470b51ba0` | `multipart-encoding` |
| `line_5422499e0c06dcf3227ce9a9` | `multipart-encoding` |
| `line_5435f23490fb68f58607b390` | `request-body-observers` |
| `line_54f2809c307cbf93fc37d931` | `request-body-dispatch` |
| `line_550365cacf503a19763985d3` | `formdata-normalization` |
| `line_56414fb7f5fe08b4e01c5c65` | `request-body-observers` |
| `line_56af3bd36221cc18c82bc35b` | `multipart-encoding` |
| `line_56e33c9eba364772929be366` | `request-body-observers` |
| `line_579aebb5dbf1b82091b3b7f6` | `request-body-observers` |
| `line_57d67b1dca9f3e4d2f83f74e` | `formdata-normalization` |
| `line_57f8de97cda99b04c879a8f9` | `multipart-encoding` |
| `line_587e98bd31360369844ea6ef` | `formdata-normalization` |
| `line_589aeaa35f4b3c7dd1e5be16` | `request-body-observers` |
| `line_591519addef8f4838a51d664` | `request-body-observers` |
| `line_59c0a3af7066cc3f6718a615` | `multipart-encoding` |
| `line_59c52094dc7d9e7bb58f69c9` | `formdata-normalization` |
| `line_5a41efada53bcdb637b3626d` | `multipart-encoding` |
| `line_5a5ecda9f36542264156cc44` | `request-body-observers` |
| `line_5a63bbe2629f6dd433e356d5` | `multipart-encoding` |
| `line_5a9439b95442bb2c2a76a06e` | `request-body-dispatch` |
| `line_5a9aa6aac0820fc9506b212d` | `request-body-observers` |
| `line_5cb3c5bd381de474e501700f` | `multipart-encoding` |
| `line_5da40ed9327ccf70239c1e58` | `request-body-observers` |
| `line_5e2efabe507498bd2ed356bf` | `formdata-normalization` |
| `line_5f657381dc1d69d488074e87` | `request-body-observers` |
| `line_60009c8f345d82a820fe7202` | `request-body-observers` |
| `line_60786ba718de018fe1c58c2f` | `multipart-encoding` |
| `line_6121a312a4b5d41996d3c2e6` | `multipart-encoding` |
| `line_61e4c23aa7467edb9e3129aa` | `request-body-observers` |
| `line_6217f675940aea2709b98e6a` | `request-body-dispatch` |
| `line_627ce831210ef6c169a5ccb2` | `environment-wiring` |
| `line_6342d4798da260e0f907c478` | `request-body-observers` |
| `line_63c20b015201dc6973e03ae5` | `request-body-observers` |
| `line_63e28646875c2d9479b1572d` | `request-body-observers` |
| `line_63fb6ca1df85dd718661d815` | `request-body-observers` |
| `line_63fe7ce80e2b7ac2cea65026` | `environment-wiring` |
| `line_6457b086290f121959a7e43c` | `multipart-encoding` |
| `line_64e7460dbff11280e5bc0bad` | `formdata-normalization` |
| `line_64e90eb15bfad16a93b9a468` | `request-body-observers` |
| `line_6691ec8a603da9a69634869e` | `request-body-observers` |
| `line_66df8334951b93ff9095d373` | `request-body-observers` |
| `line_687f1767aec3a7acdc0f9c9d` | `multipart-encoding` |
| `line_68a9e8e2226a84bab3915ae3` | `formdata-normalization` |
| `line_69e9326cba551dabf40c0528` | `multipart-encoding` |
| `line_6a60d0b6cf4e9cdf1468d1f7` | `request-body-observers` |
| `line_6ab00f383ae5121ff0bbe0cd` | `request-body-observers` |
| `line_6b16710aed8c5da4fd521179` | `multipart-encoding` |
| `line_6bd16caf1356fcf4ed9e4c30` | `multipart-encoding` |
| `line_6f6764baa046b4c9328d9e7a` | `request-body-observers` |
| `line_70c13dbecb281a430a6fcdc7` | `multipart-encoding` |
| `line_71b104cbdf5e0ce3d4e320c6` | `request-body-dispatch` |
| `line_73b638bf41500e15e15f4719` | `request-body-observers` |
| `line_7471d74f5aa344c5f7b75d6c` | `multipart-encoding` |
| `line_752c0f839a1a086faf7b321a` | `request-body-dispatch` |
| `line_7571e15806b88550bdd8556c` | `formdata-normalization` |
| `line_7581197e1f4192d32340e55d` | `request-body-observers` |
| `line_76fc27bef4561f0c98f88719` | `request-body-observers` |
| `line_776885fb6554833ab5a31b11` | `environment-wiring` |
| `line_794a6902b780162a95c4e425` | `request-body-observers` |
| `line_7b3d287329ddc4237957bc1a` | `request-body-observers` |
| `line_7bfd6de2f40676380b34881e` | `request-body-observers` |
| `line_7c8543e2d6e6d5629b34c110` | `formdata-normalization` |
| `line_7ca61873a4afd7b34a864a6c` | `multipart-encoding` |
| `line_7cbe9aa9ee89d8dd7f9901bf` | `formdata-normalization` |
| `line_7d7222625d540c21e1365d20` | `multipart-encoding` |
| `line_7e097f15f8a441f12a936fd1` | `multipart-encoding` |
| `line_7e4bfda8cf859fbaca14a2d0` | `multipart-encoding` |
| `line_7eef1de44826ba0a017d3d5a` | `request-body-dispatch` |
| `line_7f172ef0e6d8accbf6f95e12` | `multipart-encoding` |
| `line_7fd2876ba9bb46cc7c88d6bb` | `multipart-encoding` |
| `line_7fe6ef3bd0d264cca246df56` | `multipart-encoding` |
| `line_804638b10a21a9f97fd89330` | `multipart-encoding` |
| `line_807d8ba6bcbd196e41a1c127` | `request-body-observers` |
| `line_8193ea8073454bf840c4632b` | `multipart-encoding` |
| `line_827d7a2db804abd520ad60d1` | `multipart-encoding` |
| `line_8335291a5268a2c7c119fc5a` | `request-body-observers` |
| `line_843f39c1b560ae402a09a846` | `formdata-normalization` |
| `line_84828a1af1964a8d7e9c302d` | `multipart-encoding` |
| `line_84b45d02eb189d6575a6b925` | `formdata-normalization` |
| `line_85f74d072b074ade62c50889` | `request-body-dispatch` |
| `line_85f84bfc27e57603d814f812` | `request-body-observers` |
| `line_860e11e49158158f91efba55` | `request-body-observers` |
| `line_8757ba4241e9d9ebfb6ab637` | `request-body-dispatch` |
| `line_875de8bc89d28d7045143ae0` | `request-body-observers` |
| `line_87d7d42fd3ec071d0e2047ec` | `request-body-observers` |
| `line_88a9e5fc4af7ee95a716adf3` | `request-body-observers` |
| `line_88b442f631b55d62dc86e368` | `request-body-observers` |
| `line_892df410eb59463c2cf1c424` | `formdata-normalization` |
| `line_8ae962972e75569d7a16f255` | `request-body-observers` |
| `line_8bc6a7e2c43ab6f3b0322d90` | `request-body-observers` |
| `line_8c6f3a31c15127db5e5cf3c8` | `environment-wiring` |
| `line_8c8ae8eab428eabf7b7860f9` | `request-body-observers` |
| `line_8cc6e918aaab705fa7ab49a7` | `request-body-observers` |
| `line_8dd73b3fa99c811741ce1709` | `multipart-encoding` |
| `line_8f085ff89d5861053e0c5b18` | `multipart-encoding` |
| `line_8f399500dca31fcfe748bb6e` | `multipart-encoding` |
| `line_8f7e9cef743309f0e9c6009e` | `request-body-observers` |
| `line_8fab4f013b76187b7097d7dc` | `formdata-normalization` |
| `line_91605412e192aa47e69d08e1` | `multipart-encoding` |
| `line_917c0186dc088117fdab8359` | `multipart-encoding` |
| `line_923695fa7b001a22f9c5a41d` | `request-body-observers` |
| `line_925003f2219aa49104609d0c` | `request-body-observers` |
| `line_939a5c1758f16f2dc9e82921` | `request-body-observers` |
| `line_95ab79ceaff63c844fc839c5` | `multipart-encoding` |
| `line_964652650a9776f44ad53c57` | `request-body-dispatch` |
| `line_96c2186d2e4428ea8d47adba` | `request-body-observers` |
| `line_96ccd140e779e10980a04f31` | `request-body-observers` |
| `line_97c891c1b9a1e37d386b6e3f` | `formdata-normalization` |
| `line_982360c3f0ef9a1e1011a7ec` | `request-body-observers` |
| `line_98315988a7a6f020ae17dbd9` | `request-body-observers` |
| `line_98df7c9bd0c991fa6eeac499` | `multipart-encoding` |
| `line_99f7e5b765a8dda5167ac716` | `request-body-observers` |
| `line_9ad94443623e062ea4788b4c` | `multipart-encoding` |
| `line_9af800a44d592e7505b08526` | `request-body-observers` |
| `line_9ded1ab110893da8bcc57256` | `request-body-observers` |
| `line_9e182fb56aa771b5f9c44198` | `multipart-encoding` |
| `line_9f492688abedaf839ad87087` | `multipart-encoding` |
| `line_a013b24bafeaf7bda4f1c3ac` | `request-body-observers` |
| `line_a07a3a42a372bd009f923ade` | `multipart-encoding` |
| `line_a0b6e0d44103a43a53538b1b` | `request-body-observers` |
| `line_a14b635bf19f8d9109143b80` | `request-body-observers` |
| `line_a16003c246bfc8b215acfe49` | `request-body-observers` |
| `line_a163ae0c83502954ab60d290` | `request-body-observers` |
| `line_a1c3128ddd739a679862cf78` | `request-body-observers` |
| `line_a38440b91c87285f1f2aa9e3` | `multipart-encoding` |
| `line_a5c320a00eb6e8cd37c2fa9c` | `multipart-encoding` |
| `line_a746efd4b6163dd78f6cb707` | `multipart-encoding` |
| `line_a8797a9821a76bc1aa4389ca` | `multipart-encoding` |
| `line_a8c13bd7f46d33ba4e910fda` | `request-body-observers` |
| `line_a8d82fd14393453077f06aa5` | `request-body-observers` |
| `line_a915b8688b83cb34ebdb4a6e` | `formdata-normalization` |
| `line_aa0d8e17031360c6c2aaa66d` | `request-body-observers` |
| `line_ab57291bc9e742261ffc5ccf` | `request-body-observers` |
| `line_abe85c02e7b98455769bd571` | `multipart-encoding` |
| `line_ac964e465520189e764dab0c` | `request-body-dispatch` |
| `line_acb13ec328fc0ecb9bd4ff87` | `request-body-observers` |
| `line_ad8d76d129aac8c095f80d77` | `request-body-dispatch` |
| `line_addf91de2c04e7b668e529ac` | `formdata-normalization` |
| `line_ae96b6a1067199b4e92e59e8` | `request-body-observers` |
| `line_aeb76171ce5f1e0912adc50e` | `multipart-encoding` |
| `line_aefe94a960774821926ff816` | `request-body-observers` |
| `line_af1aae48ddedba28af04e8f4` | `request-body-observers` |
| `line_af41f4acd2c5ec6e4496e0a1` | `environment-wiring` |
| `line_afb5f0b6353a3846131690b1` | `multipart-encoding` |
| `line_b054930cca30de308a01f9e5` | `request-body-observers` |
| `line_b060237dcc3a7599312058f4` | `request-body-observers` |
| `line_b0648485a5056d432a224a36` | `multipart-encoding` |
| `line_b0c9fc6e69a633ea29f24031` | `request-body-dispatch` |
| `line_b12e77a4d968acf9f0e8f037` | `multipart-encoding` |
| `line_b16e656e37cfc9669cc62dec` | `formdata-normalization` |
| `line_b1cd27b27d684c35327b70e6` | `multipart-encoding` |
| `line_b24622b5225165eabc5acc48` | `multipart-encoding` |
| `line_b288d6de5a7cba701282dd28` | `multipart-encoding` |
| `line_b2b37832345db7c64f92a630` | `request-body-observers` |
| `line_b303d2e1e80ac3840f79adf1` | `formdata-normalization` |
| `line_b3056d207049d10aa5dd2954` | `request-body-dispatch` |
| `line_b3b4583d9c795db78ddf8b84` | `multipart-encoding` |
| `line_b45c747da4d01708b48b9eb8` | `formdata-normalization` |
| `line_b54eaec48b99df5b48be07ec` | `multipart-encoding` |
| `line_b57b61b55c9c0f00128b3b8f` | `formdata-normalization` |
| `line_b587c5520b3c44c0a7309dda` | `request-body-observers` |
| `line_b5a130090692fe616d3ca4b5` | `request-body-observers` |
| `line_b64ed99e626af5ca8dcd877a` | `request-body-observers` |
| `line_b655e71ae1e19e46c3aa7935` | `multipart-encoding` |
| `line_b72c551a579469ec084d412e` | `multipart-encoding` |
| `line_b77a058977e755259ca59ca7` | `multipart-encoding` |
| `line_b9f395613d42226c6a9faec4` | `request-body-observers` |
| `line_bbb44ca3f6e9b1a5797f4134` | `request-body-observers` |
| `line_bda5a2212917c3857a7b480e` | `request-body-observers` |
| `line_bdda20b8657b1ceb05653a3d` | `formdata-normalization` |
| `line_bde46d9b599f767e63023836` | `request-body-observers` |
| `line_bf19d90a6247c62872f99850` | `request-body-observers` |
| `line_bf32602189f564e43a4dc91c` | `request-body-dispatch` |
| `line_c0349e36c9022374ca1609a6` | `request-body-observers` |
| `line_c0ca9b552e32b165a094c0f1` | `formdata-normalization` |
| `line_c0f02948fae5dbf47237b3c3` | `multipart-encoding` |
| `line_c153c8addcf517f1075ac6e8` | `request-body-observers` |
| `line_c18c1c9d9e1f00e7d1b48111` | `multipart-encoding` |
| `line_c36f296c63b9f7a00462686f` | `request-body-dispatch` |
| `line_c414816e8ca24330fea830bb` | `multipart-encoding` |
| `line_c6bc789b24eafda5f543373d` | `request-body-dispatch` |
| `line_c79b775ec5e6e7f2daf1cced` | `request-body-observers` |
| `line_c8882c54e6c64aea5ff77f6b` | `multipart-encoding` |
| `line_cba30dd25cf1a99d0a8e7326` | `request-body-observers` |
| `line_cbe3b5ef9f11538be6ab5ace` | `formdata-normalization` |
| `line_cc33746fcbd30eeb1a33e831` | `request-body-observers` |
| `line_cd017de0089f2505e93fb100` | `multipart-encoding` |
| `line_cdcd2a6a42778a805967530a` | `request-body-observers` |
| `line_cdfb743be308f75f628429b7` | `multipart-encoding` |
| `line_ce1901a7407d6499f19002ae` | `multipart-encoding` |
| `line_ce72cbab8d0a760016f08ba2` | `request-body-observers` |
| `line_d0910371fb9b7d3012d58b50` | `multipart-encoding` |
| `line_d26b18566189d546339d95ff` | `multipart-encoding` |
| `line_d327820884f586d280c2ba8e` | `formdata-normalization` |
| `line_d40d0bcf4164301222f09651` | `request-body-observers` |
| `line_d4af7d0364faff5b67858706` | `request-body-observers` |
| `line_d50abb87ecb296887081e991` | `request-body-observers` |
| `line_d5fef6a8f52fa4940d24d92f` | `request-body-observers` |
| `line_d7ac9236fa07396a625758f9` | `multipart-encoding` |
| `line_d8a925ff89c0e8a1ea9204e3` | `environment-wiring` |
| `line_d8f81f811d8247d9929ea107` | `request-body-observers` |
| `line_da87330995af7cbe9f98c5db` | `multipart-encoding` |
| `line_dc352e28455e424004ef3b83` | `multipart-encoding` |
| `line_dc61338534cb694e4bcd528e` | `multipart-encoding` |
| `line_dc8379bfc2e8c64bd5751182` | `formdata-normalization` |
| `line_dd304a51ad3832007f10a017` | `request-body-dispatch` |
| `line_df3788cda0158837c6d4d3f8` | `multipart-encoding` |
| `line_dfb58bf4f144b4bb517c9c3b` | `request-body-observers` |
| `line_e129cb6bf69004783ac7d350` | `multipart-encoding` |
| `line_e14d8fc1b67dbdd9abeae046` | `multipart-encoding` |
| `line_e1bbee4c17f5607c78e7a5d2` | `request-body-observers` |
| `line_e39abfafa5f865c6a7d18370` | `request-body-observers` |
| `line_e47c843a5a6579cc794f0ad5` | `request-body-observers` |
| `line_e5a41287a44de1ef2c6d3846` | `multipart-encoding` |
| `line_e5c3e3da5006a1f06d675de7` | `request-body-observers` |
| `line_e5d3202c13c4f473382497d6` | `formdata-normalization` |
| `line_e5d795411e912f7df4bd9cdf` | `formdata-normalization` |
| `line_e6317b0e6534e623c02571b1` | `request-body-dispatch` |
| `line_e64ea77013615275bb9155ad` | `request-body-observers` |
| `line_e88b136ed3164e2b3e12a005` | `multipart-encoding` |
| `line_e92582c1088b1a382dcb5aa2` | `multipart-encoding` |
| `line_e9cdb5545f1108023f8657bc` | `formdata-normalization` |
| `line_ea0ddff85edd7e6c5a9ab506` | `multipart-encoding` |
| `line_ea2838faefbb74332f7a02ac` | `formdata-normalization` |
| `line_ea4f5d1484c6216b53dcce97` | `multipart-encoding` |
| `line_ea5cf400efa1f4dc698d0819` | `multipart-encoding` |
| `line_ead79ee6eb3ded7b894fb26b` | `request-body-dispatch` |
| `line_eb50004332c349757fbffcb5` | `request-body-observers` |
| `line_ec5c0199be0ecf627cdba419` | `formdata-normalization` |
| `line_edc9b46c19a987f3e93c4f7a` | `request-body-observers` |
| `line_ee3f2e235ead2d8aade62d27` | `formdata-normalization` |
| `line_f08754d212baaef1804f774d` | `multipart-encoding` |
| `line_f15c7d5c3efa88183ba386f3` | `request-body-observers` |
| `line_f1fc53e85990958055e4d6f7` | `formdata-normalization` |
| `line_f3a077ce3e5502e6ced738ea` | `multipart-encoding` |
| `line_f3c84ed98e2832824f0e340b` | `multipart-encoding` |
| `line_f581e0a2996564af6faa5122` | `multipart-encoding` |
| `line_f5e0f8631c11b959c91f0ca2` | `request-body-observers` |
| `line_f5f2aa70d02f6f55abf52943` | `request-body-observers` |
| `line_f867238f4f09afd729948a35` | `request-body-observers` |
| `line_f8af390f4e1f371815d43b42` | `request-body-observers` |
| `line_f8d74eaab019595902a9df77` | `request-body-observers` |
| `line_f93461f839ee33c1cd7518bc` | `request-body-observers` |
| `line_f96ff040b500430e900542f0` | `multipart-encoding` |
| `line_faf3a87c1084a4b2d3de8d11` | `request-body-dispatch` |
| `line_fd71cbf13d702868bc6c424f` | `formdata-normalization` |
| `line_fdd34c417b535872100dbaa7` | `request-body-dispatch` |
| `line_fddf7187586212827f849e05` | `formdata-normalization` |
| `line_fe28c93e7d5918f60fd4bdc5` | `request-body-dispatch` |
| `line_fe87bf6ea8df1307b1244721` | `request-body-observers` |
| `line_febda304eaee3e9382107cee` | `request-body-observers` |
| `line_ff50abff86ca12ef1d54d540` | `formdata-normalization` |
| `line_fff2dcaad4244eeffa1fe4fc` | `multipart-encoding` |
| `unit_19360fc8d32c6d68b9caf015` | `multipart-encoding` |
| `unit_c13c070d60d44b84bad9349b` | `multipart-encoding` |

> Shiftory verified accounting and citation references; it does not verify semantic correctness.
