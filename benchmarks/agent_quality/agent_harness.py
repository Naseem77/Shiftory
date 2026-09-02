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
  ``agent-run-v1`` record must state ``isolation_method: "protocol"`` and its
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
  identity proof required by this benchmark layer's design.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.agent_quality import fixtures as fx
from benchmarks.agent_quality import validation as v

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


def prepare_prompt_package(case_dir: Path, out_dir: Path, case_id: str) -> dict[str, Any]:
    """Materialize an isolated prompt-package directory for one case.

    Returns a ``prompt_package_manifest``-shaped list of ``{path, sha256}``
    entries plus a combined digest, suitable for recording in ``agent-run-v1``.
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

    manifest = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            manifest.append(
                {
                    "path": str(path.relative_to(out_dir)),
                    "sha256": v.sha256_file(path),
                }
            )
    digest_input = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "case_id": case_id,
        "resolved_commits": resolved,
        "prompt_package_manifest": manifest,
        "prompt_package_digest": v.sha256_bytes(digest_input),
    }


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
    shiftory_commit: str,
    command_argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    max_output_bytes: int = v.MAX_RAW_RESPONSE_BYTES,
) -> dict[str, Any]:
    """Run (optionally) a configured agent command, then read and validate
    whatever raw response it (or a prior manual/interactive run) left at
    ``prompt_dir/RAW_RESPONSE``. Writes ``out_dir/raw-response.txt`` (or
    ``.bin``), ``out_dir/agent-run.json``, and ``out_dir/explanation.json``
    only if the raw response is strictly valid per the protocol above.
    Returns the written ``agent-run-v1`` record.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()

    stdout = b""
    stderr = b""
    exit_status: int | None = None
    truncated = False
    timed_out = False
    if command_argv is not None:
        result = run_capped_subprocess(
            command_argv,
            cwd=prompt_dir,
            env=env or build_allowlisted_env(),
            timeout=timeout,
            max_bytes=max_output_bytes,
        )
        stdout, stderr = result["stdout"], result["stderr"]
        exit_status = result["exit_status"]
        truncated = result["truncated"]
        timed_out = result["timed_out"]

    finished_at = datetime.now(timezone.utc).isoformat()

    response_path = prompt_dir / "RAW_RESPONSE"
    raw = response_path.read_bytes() if response_path.is_file() else stdout

    raw_truncated = truncated
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

    agent_run = {
        "schema": "shiftory.benchmark-agent-quality-agent-run/v1",
        "case_id": case_id,
        "candidate_id": candidate_id,
        "provider": provider,
        "model": {"name": model_name, "version": model_version},
        "agent_tool_version": agent_tool_version,
        "evaluation_protocol": evaluation_protocol,
        "prompt_package_manifest": prompt_package_manifest,
        "prompt_package_digest": prompt_package_digest,
        "evidence_sha256": evidence_sha256,
        "shiftory_commit": shiftory_commit,
        "command_argv": command_argv or [],
        "env_allowlist": sorted((env or {}).keys()) if command_argv is not None else [],
        "generator_access_profile": generator_access_profile,
        "isolation_method": "protocol",
        "raw_response_sha256": raw_sha,
        "raw_response_bytes": len(raw),
        "stdout_sha256": v.sha256_bytes(stdout) if command_argv is not None else None,
        "stderr_sha256": v.sha256_bytes(stderr) if command_argv is not None else None,
        "truncated": raw_truncated,
        "timed_out": timed_out,
        "exit_status": exit_status,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
    }
    v.validate_against_schema(agent_run, "agent-run-v1")
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
