"""Opt-in harness for capturing real agent-authored explanations.

This module is never invoked by required CI (see ``scripts/agent_quality_benchmark.py``,
which only scores already-committed, already-audited candidates). It exists to make
capturing a genuinely blind real-agent run reproducible and honestly bounded:

- ``prepare_prompt_package`` materializes an isolated directory containing only the
  public case description, a real reconstructed git repository at the fixture's
  base/head commits, and the bundled ``SKILL.md`` instructions -- never anything
  under ``auditor/``. This is a real, unit-tested trust boundary: see
  ``tests/benchmark/test_agent_quality_harness.py``'s
  ``test_prepare_never_copies_auditor_content``. It is **not** a sandbox: if the
  generating agent has general filesystem/shell/network access, nothing here
  prevents it from reading the rubric directly from this repository. Every
  ``agent-run-v2`` record must state ``isolation_method: "protocol"`` and its
  ``generator_access_profile`` honestly, precisely because this limitation is real.
- ``capture_result`` reads whatever raw bytes the generating agent left behind,
  optionally after running a locally configured command under bounded,
  streamed-capped, process-group-killed execution (``shell=False``, an
  allow-listed environment, and no secrets ever placed on argv). The raw bytes are
  preserved verbatim as ``raw-response.txt``/``.bin`` (never given a ``.json``
  suffix, so they are never swept into ``scripts/validate_repository.py``'s blanket
  JSON parse of ``benchmarks/**/*.json``). ``explanation.json`` is written **only**
  when the entire raw response is itself valid JSON structurally resembling
  ``shiftory.explanation/v1`` -- no fence-stripping, no repair. Its bytes are always
  identical to ``raw-response.txt``'s bytes by construction, which is the concrete
  identity proof required by this benchmark layer's design. ``capture_result``'s
  ``invocation`` field is a tagged union (``local_process``/``copilot_task``): real
  generation timing is only ever recorded when actually observed (always true for
  ``local_process``, since this module itself starts and waits on the subprocess;
  only true for ``copilot_task`` when the caller genuinely obtained it from the
  sub-agent) -- never inferred from when this function happens to run its own
  bookkeeping, which is separately and honestly labeled ``capture_ingested_at_utc``.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.agent_quality import fixtures as fx
from benchmarks.agent_quality import validation as v
from benchmarks.runner import BenchmarkError

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "src" / "shiftory" / "skills" / "shiftory" / "SKILL.md"

# A deliberately small environment allowlist: only what a subprocess needs to run
# at all. Callers may extend this for a specific configured agent command, but
# secrets must never be placed on argv or copied wholesale from os.environ.
DEFAULT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")

INSTRUCTIONS = """\
# Agent-quality benchmark prompt package

This directory is the complete, isolated input for one benchmark case. It
contains:

- `case.json` -- the public case description (no expected answers).
- `repository/` -- a real git repository checked out at the case's HEAD commit,
  with the base commit reachable as history.
- `SKILL.md` -- Shiftory's bundled agent instructions.

Task: follow `SKILL.md` to explain the change between the fixture's base and
head commits in `repository/` (use `--range <base>..<head>`). When you have a
final `shiftory.explanation/v1` document, write **only** that JSON document,
with nothing else before or after it, to a file named `RAW_RESPONSE` at the top
of this directory. Do not wrap it in Markdown code fences or add commentary.
"""


def _manifest_for_dir(out_dir: Path) -> list[dict[str, str]]:
    """Build a ``{path, sha256}`` manifest for every non-.git file under
    ``out_dir``, sorted by path. Shared by ``prepare_prompt_package`` and
    ``reconstruct_full_prompt_manifest_at_commit`` so both compute the
    manifest identically -- any drift between them would silently weaken
    the protocol-commit verification this module supports.
    """
    manifest = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            manifest.append(
                {
                    "path": str(path.relative_to(out_dir)),
                    "sha256": v.sha256_file(path),
                }
            )
    return manifest


def prepare_prompt_package(case_dir: Path, out_dir: Path, case_id: str) -> dict[str, Any]:
    """Materialize an isolated prompt-package directory for one case.

    Returns a ``prompt_package_manifest``-shaped list of ``{path, sha256}``
    entries plus a combined digest, suitable for recording in ``agent-run-v2``.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    repository, resolved = fx.reconstruct_fixture(case_dir, out_dir, "repository")
    case = v.load_json_strict(case_dir / "case.json")
    v.validate_against_schema(case, "case-v1")
    (out_dir / "case.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(SKILL_PATH, out_dir / "SKILL.md")
    (out_dir / "INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")

    manifest = _manifest_for_dir(out_dir)
    digest_input = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "case_id": case_id,
        "resolved_commits": resolved,
        "prompt_package_manifest": manifest,
        "prompt_package_digest": v.sha256_bytes(digest_input),
    }


def _git_show(commit: str, path: str) -> bytes | None:
    """``git show <commit>:<path>`` against this repository; ``None`` if the
    commit or path does not exist rather than raising, so callers can treat
    an unreconstructable commit as an honest verification failure."""
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _extract_instructions_constant(harness_source: bytes) -> str | None:
    """Parse ``agent_harness.py`` source (as committed at some historical
    commit) and return the literal string value of its module-level
    ``INSTRUCTIONS`` assignment, without executing any of that source. Used
    to reconstruct the prompt package exactly as that commit's harness would
    have produced it, even though this module's own current ``INSTRUCTIONS``
    constant is what actually ran (this module is not re-executed
    per-commit) -- confirming the text is unchanged is part of the proof.
    """
    try:
        tree = ast.parse(harness_source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        is_instructions_assign = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "INSTRUCTIONS" for target in node.targets
        )
        if (
            is_instructions_assign
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    return None


def _reconstruct_prompt_package_state_at_commit(commit: str, case_id: str) -> dict[str, Any] | None:
    """Internal: reconstruct everything needed to verify BOTH the full
    prompt-package manifest and the protocol registry's config/case-revision
    claims, using ONLY files as committed at ``commit`` (never the current
    working tree). Returns a dict with ``manifest`` (the
    ``{path, sha256}`` list), ``case`` (the parsed ``case.json`` dict, so
    callers can check its ``version`` without a second ``git show``), and
    ``instructions_sha256``/``skill_sha256`` (of the exact bytes that went
    into the manifest). Returns ``None`` if any required file cannot be
    extracted or parsed from that commit -- an honest verification failure,
    not a fabricated pass. Shared by ``reconstruct_full_prompt_manifest_at_commit``
    and ``recompute_benchmark_protocol_commit_verification`` so both use
    identically-reconstructed content and this repository is not asked to
    run ``git show``/fixture reconstruction twice per verification.
    """
    case_json_bytes = _git_show(commit, f"benchmarks/agent_quality/cases/{case_id}/case.json")
    metadata_bytes = _git_show(commit, f"benchmarks/agent_quality/cases/{case_id}/metadata.json")
    history_bytes = _git_show(
        commit, f"benchmarks/agent_quality/cases/{case_id}/history.fast-import"
    )
    skill_bytes = _git_show(commit, "src/shiftory/skills/shiftory/SKILL.md")
    harness_source = _git_show(commit, "benchmarks/agent_quality/agent_harness.py")
    if None in (case_json_bytes, metadata_bytes, history_bytes, skill_bytes, harness_source):
        return None
    assert case_json_bytes is not None
    assert metadata_bytes is not None
    assert history_bytes is not None
    assert skill_bytes is not None
    assert harness_source is not None

    instructions_text = _extract_instructions_constant(harness_source)
    if instructions_text is None:
        return None

    with tempfile.TemporaryDirectory(prefix=f"protocol-verify-{case_id}-") as tmp:
        tmp_path = Path(tmp)
        source_case_dir = tmp_path / "source"
        source_case_dir.mkdir()
        (source_case_dir / "metadata.json").write_bytes(metadata_bytes)
        (source_case_dir / "history.fast-import").write_bytes(history_bytes)

        out_dir = tmp_path / "reconstructed"
        out_dir.mkdir()
        try:
            fx.reconstruct_fixture(source_case_dir, out_dir, "repository")
        except (BenchmarkError, v.AgentQualityError):
            return None

        try:
            case = v.parse_json_text(case_json_bytes.decode("utf-8"))
        except (v.AgentQualityError, UnicodeDecodeError):
            return None
        (out_dir / "case.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
        (out_dir / "SKILL.md").write_bytes(skill_bytes)
        (out_dir / "INSTRUCTIONS.md").write_text(instructions_text, encoding="utf-8")

        return {
            "manifest": _manifest_for_dir(out_dir),
            "case": case,
            "instructions_sha256": v.sha256_bytes(instructions_text.encode("utf-8")),
            "skill_sha256": v.sha256_bytes(skill_bytes),
        }


def reconstruct_full_prompt_manifest_at_commit(
    commit: str, case_id: str
) -> list[dict[str, str]] | None:
    """Reconstruct the ENTIRE prompt package -- case.json, SKILL.md,
    INSTRUCTIONS.md, and the reconstructed fixture repository -- using ONLY
    files as committed at ``commit`` (never the current working tree), and
    return its manifest. Returns ``None`` if any required file cannot be
    extracted from that commit (an honest verification failure, not a
    fabricated pass).

    This is strictly stronger than checking case.json alone: it proves the
    full committed protocol at that commit -- case content, the bundled
    SKILL.md, and the harness's own INSTRUCTIONS text -- reproduces
    byte-identical prompt-package content, not just that one file matches.
    """
    state = _reconstruct_prompt_package_state_at_commit(commit, case_id)
    return state["manifest"] if state is not None else None


def reconstruct_protocol_registry_at_commit(commit: str) -> dict[str, Any] | None:
    """Load and schema-validate ``protocol_registry.json`` as committed at
    ``commit`` -- never the current working tree. Returns ``None`` if the
    file is absent, unparsable, or schema-invalid at that commit: an absent
    or malformed registry is an honest verification failure, never a
    silent pass. This is what makes ``benchmark_protocol_commit`` mean "a
    commit that actually carries the config/prompt-digest/invocation-rule
    registry", not merely "a commit whose case.json happens to match"."""
    registry_bytes = _git_show(commit, "benchmarks/agent_quality/protocol_registry.json")
    if registry_bytes is None:
        return None
    try:
        registry = v.parse_json_text(registry_bytes.decode("utf-8"))
        v.validate_against_schema(registry, "protocol-registry-v1")
    except (v.AgentQualityError, UnicodeDecodeError):
        return None
    return registry


def _config_id_for_candidate(candidate_id: str) -> str | None:
    """``'captured_config_a'`` -> ``'config-a'``; ``None`` for any
    candidate id that does not follow this benchmark's fixed naming
    convention (e.g. ``synthetic_baseline``), which have no registry
    config to check against."""
    prefix = "captured_config_"
    if not candidate_id.startswith(prefix):
        return None
    suffix = candidate_id[len(prefix) :]
    if suffix not in ("a", "b"):
        return None
    return f"config-{suffix}"


def verify_config_registry_match(agent_run: dict[str, Any], registry: dict[str, Any]) -> bool:
    """Check that ``agent_run`` was actually generated under the
    predeclared config (provider/model/agent-type/tool/invocation-kind)
    that ``registry["configs"]`` declares for this capture's config id
    (derived from ``candidate_id``), and that the case revision the
    registry freezes for this ``case_id`` matches. This is what proves a
    capture used the *registered configuration*, not merely that its
    prompt bytes happen to match -- a capture could in principle
    reconstruct byte-identical prompt content while actually having been
    generated by an undeclared model/tool, and this check is what rules
    that out for every officially-verified capture.
    """
    config_id = _config_id_for_candidate(agent_run.get("candidate_id", ""))
    if config_id is None:
        return False
    config = registry.get("configs", {}).get(config_id)
    if config is None:
        return False
    if agent_run.get("provider") != config["provider"]:
        return False
    model = agent_run.get("model", {})
    if not isinstance(model, dict) or model.get("name") != config["model_name"]:
        return False
    invocation = agent_run.get("invocation", {})
    if not isinstance(invocation, dict) or invocation.get("kind") != config["invocation_kind"]:
        return False
    if config["invocation_kind"] == "copilot_task":
        if invocation.get("agent_type") != config["agent_type"]:
            return False
        if invocation.get("tool") != config["tool"]:
            return False
    return True


def recompute_benchmark_protocol_commit_verification(agent_run: dict[str, Any]) -> bool:
    """Independently recompute whether ``agent_run["benchmark_protocol_commit"]``'s
    ``verified: true`` claim is actually true, rather than trusting the
    self-reported flag a migration or capture script wrote.

    This check has two independent halves, and BOTH must hold:

    1. **Full prompt-package content-equality**: reconstructs the ENTIRE
       prompt package (case.json, SKILL.md, INSTRUCTIONS.md, and the
       reconstructed fixture repository) from ONLY files as committed at
       ``benchmark_protocol_commit.commit``, and compares the complete
       resulting manifest, path-for-path, against this same capture's own
       recorded ``prompt_package_manifest``.
    2. **Committed config/registry match**: loads
       ``protocol_registry.json`` as committed at that same commit (never
       the working tree; see ``reconstruct_protocol_registry_at_commit``),
       confirms it is schema-valid, confirms its ``case_revisions`` entry
       for this capture's case matches the ``version`` actually reconstructed
       from that commit's ``case.json``, and confirms this capture's
       provider/model/agent-type/tool/invocation-kind match the registry's
       declared config for this capture's config id (see
       ``verify_config_registry_match``).

    Returns ``True`` only when both halves hold. A commit whose case.json
    happens to match but which has no committed ``protocol_registry.json``
    at all (e.g. a commit predating this registry's introduction) fails
    here -- this is deliberate: proving byte-identical prompt content is
    not the same as proving the predeclared configuration was actually
    used, and this benchmark no longer conflates the two. This does not,
    and cannot, prove anything about the ordering between that commit's
    timestamp and this capture's own timestamps -- see
    ``verify_protocol_precommitment`` for that complementary, chronological
    check, and the methodology doc's "Provenance" section for why both
    checks, together, are what this benchmark treats as the load-bearing
    integrity property.
    """
    protocol = agent_run["benchmark_protocol_commit"]
    commit = protocol.get("commit")
    if commit is None:
        return False

    state = _reconstruct_prompt_package_state_at_commit(commit, agent_run["case_id"])
    if state is None:
        return False
    recorded = sorted(agent_run["prompt_package_manifest"], key=lambda entry: entry["path"])
    reconstructed = sorted(state["manifest"], key=lambda entry: entry["path"])
    if recorded != reconstructed:
        return False

    registry = reconstruct_protocol_registry_at_commit(commit)
    if registry is None:
        return False
    expected_version = registry.get("case_revisions", {}).get(agent_run["case_id"])
    if expected_version is None or state["case"].get("version") != expected_version:
        return False
    if registry.get("instructions_sha256") != state["instructions_sha256"]:
        return False
    if registry.get("skill_sha256") != state["skill_sha256"]:
        return False

    return verify_config_registry_match(agent_run, registry)


def get_commit_committer_date(commit: str) -> datetime | None:
    """The real, actual committer date of ``commit`` in this repository (not
    the author date, which is trivially forgeable by the committer), parsed
    as a timezone-aware UTC datetime. ``None`` if the commit does not exist.
    """
    result = subprocess.run(
        ["git", "show", "-s", "--format=%cI", commit],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8").strip()
    if not text:
        return None
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def verify_protocol_precommitment(agent_run: dict[str, Any]) -> bool:
    """Verify that ``benchmark_protocol_commit.commit`` was actually
    committed to this repository strictly BEFORE this capture's own
    ``capture_ingested_at_utc`` -- the chronological, precommitment half of
    protocol integrity, complementing (not replacing)
    ``recompute_benchmark_protocol_commit_verification``'s content-equality
    proof. Returns ``False`` if the commit does not exist, or if its
    committer date is not strictly earlier than the capture's own ingestion
    timestamp.
    """
    protocol = agent_run["benchmark_protocol_commit"]
    commit = protocol.get("commit")
    if commit is None:
        return False
    commit_time = get_commit_committer_date(commit)
    if commit_time is None:
        return False
    ingested_at = datetime.fromisoformat(agent_run["capture_ingested_at_utc"]).astimezone(
        timezone.utc
    )
    return commit_time < ingested_at


def build_allowlisted_env(extra: tuple[str, ...] = ()) -> dict[str, str]:
    names = set(DEFAULT_ENV_ALLOWLIST) | set(extra)
    return {name: os.environ[name] for name in names if name in os.environ}


def _drain(
    stream: Any,
    buffer: list[bytes],
    cap: int,
    lock: threading.Lock,
    truncated: list[bool],
) -> None:
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return
        with lock:
            current_len = len(buffer[0])
            if current_len < cap:
                remaining = cap - current_len
                buffer[0] += chunk[:remaining]
                if len(chunk) > remaining:
                    truncated[0] = True
            else:
                truncated[0] = True


def run_capped_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    max_bytes: int,
) -> dict[str, Any]:
    """Run argv with shell=False, a caller-supplied (allow-listed) environment,
    streamed/incrementally-capped stdout/stderr (never fully buffered before the
    cap is enforced), and a hard timeout that kills the whole process group.
    """
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        shell=False,
    )
    stdout_buf: list[bytes] = [b""]
    stderr_buf: list[bytes] = [b""]
    stdout_truncated = [False]
    stderr_truncated = [False]
    lock = threading.Lock()
    assert proc.stdout is not None
    assert proc.stderr is not None
    out_thread = threading.Thread(
        target=_drain,
        args=(proc.stdout, stdout_buf, max_bytes, lock, stdout_truncated),
        daemon=True,
    )
    err_thread = threading.Thread(
        target=_drain,
        args=(proc.stderr, stderr_buf, max_bytes, lock, stderr_truncated),
        daemon=True,
    )
    out_thread.start()
    err_thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        with suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
    out_thread.join(timeout=5)
    err_thread.join(timeout=5)

    return {
        "stdout": stdout_buf[0],
        "stderr": stderr_buf[0],
        "exit_status": proc.returncode,
        "truncated": stdout_truncated[0] or stderr_truncated[0],
        "timed_out": timed_out,
    }


def _read_capped_file(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` from ``path`` without ever loading a larger
    file fully into memory first. Returns ``(data, truncated)``."""
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data, truncated


def _looks_like_explanation(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema") == "shiftory.explanation/v1"
        and isinstance(value.get("items"), list)
        and isinstance(value.get("coverage_owners"), list)
    )


def capture_result(
    *,
    prompt_dir: Path,
    out_dir: Path,
    case_id: str,
    candidate_id: str,
    provider: str,
    model_name: str,
    model_version: str,
    agent_tool_version: str,
    evaluation_protocol: str,
    generator_access_profile: dict[str, Any],
    prompt_package_manifest: list[dict[str, str]],
    prompt_package_digest: str,
    evidence_sha256: str,
    engine_identity: dict[str, Any],
    benchmark_protocol_commit: dict[str, Any],
    command_argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    max_output_bytes: int = v.MAX_RAW_RESPONSE_BYTES,
    copilot_task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run (optionally) a configured agent command, then read and validate
    whatever raw response it (or a prior manual/interactive run) left at
    ``prompt_dir/RAW_RESPONSE``. Writes ``out_dir/raw-response.txt`` (or
    ``.bin``), ``out_dir/agent-run.json``, and ``out_dir/explanation.json``
    only if the raw response is strictly valid per the protocol above.
    Returns the written ``agent-run-v2`` record.

    Exactly one of two invocation shapes applies:

    - ``command_argv`` given: a real ``local_process`` invocation. This
      function itself starts and waits on the subprocess, so real generation
      timing (``generation_started_at_utc``/``generation_finished_at_utc``)
      is genuinely observed and recorded.
    - ``command_argv`` omitted: a ``copilot_task`` invocation -- the
      generating agent already ran to completion as a Copilot CLI task
      sub-agent in a separate conversation this function never controlled,
      and left ``RAW_RESPONSE`` behind. ``copilot_task_info`` (required in
      this case) must supply whatever was actually configured/observed for
      that invocation; real generation timing is only ever set from a value
      the caller genuinely obtained from that sub-agent (e.g. its own
      reported elapsed time), never fabricated from when this function
      happens to run its bookkeeping -- if unavailable, both timing fields
      must be ``None`` and ``generation_timing_unavailable_reason`` must
      explain why.
    """
    if command_argv is None and copilot_task_info is None:
        raise v.AgentQualityError(
            "capture_result requires copilot_task_info when command_argv is not given"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    stdout = b""
    stderr = b""
    exit_status: int | None = None
    truncated = False
    timed_out = False
    if command_argv is not None:
        generation_started_at = datetime.now(timezone.utc).isoformat()
        result = run_capped_subprocess(
            command_argv,
            cwd=prompt_dir,
            env=env or build_allowlisted_env(),
            timeout=timeout,
            max_bytes=max_output_bytes,
        )
        generation_finished_at = datetime.now(timezone.utc).isoformat()
        stdout, stderr = result["stdout"], result["stderr"]
        exit_status = result["exit_status"]
        truncated = result["truncated"]
        timed_out = result["timed_out"]

    capture_ingested_at = datetime.now(timezone.utc).isoformat()

    response_path = prompt_dir / "RAW_RESPONSE"
    if response_path.is_file():
        raw, response_truncated = _read_capped_file(response_path, max_output_bytes)
    else:
        raw, response_truncated = stdout, False

    raw_truncated = truncated or response_truncated
    if len(raw) > max_output_bytes:
        raw = raw[:max_output_bytes]
        raw_truncated = True

    try:
        raw_text: str | None = raw.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = None

    raw_filename = "raw-response.txt" if raw_text is not None else "raw-response.bin"
    (out_dir / raw_filename).write_bytes(raw)
    raw_sha = v.sha256_bytes(raw)

    protocol_violation: str | None = None
    parsed: Any = None
    if raw_text is None:
        protocol_violation = "raw response is not valid UTF-8"
    else:
        try:
            parsed = v.parse_json_text(raw_text)
        except v.AgentQualityError as error:
            protocol_violation = f"raw response is not exactly one valid JSON document: {error}"
        else:
            if not _looks_like_explanation(parsed):
                protocol_violation = (
                    "raw response is valid JSON but does not structurally resemble "
                    "shiftory.explanation/v1 (missing schema/items/coverage_owners)"
                )

    if protocol_violation is None:
        (out_dir / "explanation.json").write_bytes(raw)
        assert v.sha256_file(out_dir / "explanation.json") == raw_sha

    if command_argv is not None:
        invocation: dict[str, Any] = {
            "kind": "local_process",
            "command_argv": command_argv,
            "env_allowlist": sorted((env or {}).keys()),
            "exit_status": exit_status,
            "generation_started_at_utc": generation_started_at,
            "generation_finished_at_utc": generation_finished_at,
            "timed_out": timed_out,
            "stdout_sha256": v.sha256_bytes(stdout),
            "stderr_sha256": v.sha256_bytes(stderr),
        }
    else:
        assert copilot_task_info is not None
        invocation = {"kind": "copilot_task", **copilot_task_info}

    agent_run = {
        "schema": "shiftory.benchmark-agent-quality-agent-run/v2",
        "case_id": case_id,
        "candidate_id": candidate_id,
        "provider": provider,
        "model": {"name": model_name, "version": model_version},
        "agent_tool_version": agent_tool_version,
        "evaluation_protocol": evaluation_protocol,
        "invocation": invocation,
        "capture_ingested_at_utc": capture_ingested_at,
        "prompt_package_manifest": prompt_package_manifest,
        "prompt_package_digest": prompt_package_digest,
        "evidence_sha256": evidence_sha256,
        "engine_identity": engine_identity,
        "benchmark_protocol_commit": benchmark_protocol_commit,
        "generator_access_profile": generator_access_profile,
        "isolation_method": "protocol",
        "raw_response_sha256": raw_sha,
        "raw_response_bytes": len(raw),
        "truncated": raw_truncated,
        "migrated_from_v1_sha256": None,
    }
    v.validate_against_schema(agent_run, "agent-run-v2")
    (out_dir / "agent-run.json").write_text(
        json.dumps(agent_run, indent=2) + "\n", encoding="utf-8"
    )

    result_summary: dict[str, Any] = {
        "agent_run": agent_run,
        "protocol_violation": protocol_violation,
    }
    if protocol_violation is not None:
        result_summary["invalid_candidate"] = {
            "reason": "The raw agent response could not be used as a candidate explanation.",
            "raw_response_sha256": raw_sha,
            "protocol_violation": protocol_violation,
        }
    return result_summary
