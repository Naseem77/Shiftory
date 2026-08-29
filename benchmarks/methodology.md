# Benchmark methodology

The normative methodology is
[`docs/benchmark-methodology.md`](../docs/benchmark-methodology.md). Public scenario
results are recorded only when `runner.py suite --publish` successfully completes
all three immutable network scenarios from a clean committed Shiftory worktree.
Use a non-publishing suite while changes are uncommitted, then commit, verify the
worktree is clean, and rerun with `--publish` before pushing. Generated reports
must not be edited by hand. Publication records only logical package identities,
repository-relative paths, and content digests; it rejects absolute local paths
and usernames. Semantic cold/warm comparison excludes location, timestamps, and
timings while retaining graph facts and verified code provenance.
