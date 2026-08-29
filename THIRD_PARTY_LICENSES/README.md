# Third-party license inventory

This committed directory records the reviewed Graphora notice and license used
by Shiftory. It is not the complete release dependency closure. Every release
job generates and uploads the complete target-specific license/notice inventory
and wheel-extracted materials under `dist/license-materials/`. Shiftory installs
`graphora-kg==0.2.1` without any Graphora extras (`llm`, `mcp`, or `dev`).

The verified input is `graphora_kg-0.2.1-py3-none-any.whl` (SHA-256
`6b39eab0dc8aa7fc2aec9912d1506306556ca5cacd76447aa00e8afb6ef358d9`),
corresponding to tag `v0.2.1` at commit
`584e45e71dfeb9004ebfef7a187a6773ffb2db04`. Its metadata declares MIT, while
its bundled Apache-2.0 license is preserved at
`graphora-kg-0.2.1/LICENSE` (SHA-256
`c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`).
Its `tree-sitter!=0.26.0,>=0.23` dependency is constrained to 0.25.2.

For each release:

1. On each supported release platform and Python version, build the Shiftory
   wheel, then resolve the fully pinned `constraints-release.txt` closure:

   ```bash
   python -m pip install --dry-run --ignore-installed \
     --only-binary=:all: --constraint constraints-release.txt \
     --report dist/shiftory.install-report.json dist/shiftory-*.whl
   python -m pip download --constraint constraints-release.txt \
     --only-binary=:all: --dest dist/wheelhouse dist/shiftory-*.whl
   EXPECTED_WHEEL_COUNT=25  # use 24 for CPython 3.11.3+ and 3.12
   python scripts/validate_wheelhouse.py \
     --wheelhouse dist/wheelhouse \
     --report dist/shiftory.install-report.json \
     --expected-count "$EXPECTED_WHEEL_COUNT"
   python -m venv .release-venv
   .release-venv/bin/python -m pip install --no-index \
     --find-links dist/wheelhouse \
     dist/wheelhouse/shiftory-*.whl
   .release-venv/bin/python -m pip install --only-binary=:all: \
     --constraint constraints-dev.txt packaging
   .release-venv/bin/python scripts/generate_sbom.py \
     --wheelhouse dist/wheelhouse \
     --report dist/shiftory.install-report.json \
     --output dist/shiftory.cdx.json \
     --licenses-output dist/shiftory.notice-license-inventory.json \
     --license-materials-dir dist/license-materials
   ```

   The online dry-run report preserves each clean index artifact's direct URL;
   the subsequent download must contain the same filenames and hashes.
   Use 25 as the target count on CPython 3.10 and CPython 3.11.0-3.11.2:
   those closures include `async-timeout==5.0.1` for Redis. Use 24 on CPython
   3.11.3+ and 3.12. CPython 3.10 uses `rpds-py==0.30.0`; CPython 3.11+
   uses `rpds-py==2026.6.3`. These marker-selected packages and their artifact
   hashes must appear in the target's SBOM and dependency-license inventory.
   `--only-binary=:all:` makes a missing compatible wheel a hard failure. Never
   substitute a source build.
2. Record every wheel filename and SHA-256 in the target's release SBOM,
   including package URL, version, and license declarations from wheel metadata.
3. Extract every wheel's `licenses/`, `LICENSE*`, `COPYING*`, and `NOTICE*`
   material under `dist/license-materials/<distribution>-<version>/`. The
   generated inventory records each archive path, extracted path, size, kind,
   and SHA-256, and generation fails when any resolved wheel lacks such material
   or when extracted bytes do not match the wheel member.
4. Upload `shiftory.notice-license-inventory.json` and `license-materials/`
   beside the SBOM and wheelhouse. The committed top-level `NOTICE` and
   `THIRD_PARTY_LICENSES/graphora-kg-0.2.1/` document the reviewed Graphora
   artifact only; they do not replace the generated complete closure materials.
   This procedure verifies bytes against resolved wheels and does not claim or
   perform comparisons with upstream source tags.
5. The install report environment in SBOM metadata identifies the platform,
   architecture, implementation, and Python version. Native wheel filenames and
   hashes differ by target; do not claim one platform's SBOM for another.
   Re-resolve and regenerate an SBOM on every release platform/Python target
   whenever a wheel, version, dependency, platform, or Python version changes.
   Release only when each SBOM, inventory, and extracted-material directory
   describes that target's exact wheelhouse.
