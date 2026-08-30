"""Thin command-line adapter over Shiftory's typed library."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator
from platformdirs import user_state_path

from shiftory import __version__
from shiftory.cache.store import CacheStore
from shiftory.errors import ScopeError, ShiftoryError, ValidationError
from shiftory.evidence.builder import AnalyzeOptions, analyze
from shiftory.git.repository import ScopeSpec, repository_identity, resolve_repository
from shiftory.models.json import canonical_json, load_json, pretty_json
from shiftory.render.evidence import render_evidence_markdown
from shiftory.render.report import build_report, render_report_markdown
from shiftory.schemas import SchemaName, load_schema


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shiftory", description="Explain Git changes, not review them."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze_parser = commands.add_parser("analyze", help="create deterministic evidence")
    _add_scope(analyze_parser)
    _add_analysis_options(analyze_parser)
    analyze_parser.add_argument("--format", choices=("json", "markdown"), default="json")
    analyze_parser.add_argument("--output", type=Path)

    verify_parser = commands.add_parser("verify", help="validate an explanation manifest")
    _add_manifest_args(verify_parser)

    render_parser = commands.add_parser("render", help="render a verified explanation")
    _add_manifest_args(render_parser)
    render_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    render_parser.add_argument("--output", type=Path)

    schema_parser = commands.add_parser("schema", help="print a bundled JSON Schema")
    schema_parser.add_argument("name", choices=("evidence", "explanation", "report"))

    cache_parser = commands.add_parser("cache", help="inspect or clear repository cache")
    cache_parser.add_argument("action", choices=("status", "clear"))
    cache_parser.add_argument("--repo", default=".")
    cache_parser.add_argument("--cache-dir", type=Path)

    skill_parser = commands.add_parser("install-skill", help="install the thin agent skill")
    skill_parser.add_argument(
        "--target", choices=("copilot", "claude", "generic"), default="copilot"
    )
    skill_parser.add_argument("--directory", type=Path)

    explain_parser = commands.add_parser(
        "explain", help="run the low-friction explanation workflow"
    )
    _add_scope(explain_parser)
    _add_analysis_options(explain_parser)
    explain_parser.add_argument("--resume", type=Path, help="run descriptor from an earlier phase")
    explain_parser.add_argument("--explanation", type=Path)
    explain_parser.add_argument("--output", type=Path)
    explain_parser.add_argument("--keep-artifacts", action="store_true")
    return parser


def _add_scope(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true")
    group.add_argument("--unstaged", action="store_true")
    group.add_argument("--commit")
    group.add_argument("--range", dest="range_value")
    group.add_argument("--branch")
    group.add_argument("--pr", type=int)
    parser.add_argument("--remote")
    parser.add_argument("--parent", type=int)
    parser.add_argument(
        "--path",
        dest="paths",
        action="append",
        default=[],
        metavar="PATH",
        help="changed file or recursive directory to include; repeat to union selections",
    )


def _add_analysis_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".")
    parser.add_argument("--graphora", choices=("auto", "off", "required"), default="auto")
    parser.add_argument("--max-evidence-bytes", type=int, default=1_000_000)
    parser.add_argument("--context-lines", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--no-cache", action="store_true")


def _add_manifest_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--explanation", type=Path, required=True)


def _scope(args: argparse.Namespace) -> ScopeSpec:
    return ScopeSpec(
        staged=args.staged,
        unstaged=args.unstaged,
        commit=args.commit,
        range=args.range_value,
        branch=args.branch,
        pr=args.pr,
        remote=args.remote if args.remote is not None else "origin",
        parent=args.parent,
        paths=tuple(args.paths),
    )


def _options(args: argparse.Namespace) -> AnalyzeOptions:
    return AnalyzeOptions(
        repo=args.repo,
        scope=_scope(args),
        graphora=args.graphora,
        max_evidence_bytes=args.max_evidence_bytes,
        context_lines=args.context_lines,
        cache_dir=args.cache_dir,
        no_cache=args.no_cache,
    )


def _write_or_print(payload: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8")


def _validate_schema(value: dict[str, Any], name: SchemaName) -> None:
    errors = sorted(
        Draft202012Validator(load_schema(name)).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise ValidationError(
            f"{name.title()} does not match its v1 schema",
            details={
                "errors": [
                    {
                        "path": "$"
                        + "".join(
                            f"[{part}]" if isinstance(part, int) else f".{part}"
                            for part in error.absolute_path
                        ),
                        "message": error.message,
                    }
                    for error in errors
                ]
            },
        )


def _analyze(args: argparse.Namespace) -> int:
    _validate_analysis_args(args)
    evidence = analyze(_options(args)).to_dict()
    _validate_schema(evidence, "evidence")
    payload = (
        canonical_json(evidence) if args.format == "json" else render_evidence_markdown(evidence)
    )
    _write_or_print(payload, args.output)
    return 0


def _load_manifests(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    return _load_manifest_paths(args.evidence, args.explanation)


def _load_manifest_paths(
    evidence_path: Path, explanation_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = load_json(evidence_path)
    explanation = load_json(explanation_path)
    _validate_schema(evidence, "evidence")
    _validate_schema(explanation, "explanation")
    return evidence, explanation


def _verify(args: argparse.Namespace) -> int:
    report = _verify_manifest_paths(args.evidence, args.explanation)
    sys.stdout.write(
        canonical_json(
            {
                "valid": True,
                "comparison_identity": report["comparison_identity"],
                "coverage": report["coverage"],
            }
        )
    )
    return 0


def _verify_manifest_paths(evidence_path: Path, explanation_path: Path) -> dict[str, Any]:
    evidence, explanation = _load_manifest_paths(evidence_path, explanation_path)
    return build_report(evidence, explanation)


def _render(args: argparse.Namespace) -> int:
    payload = _render_manifest_paths(args.evidence, args.explanation, args.format)
    _write_or_print(payload, args.output)
    return 0


def _render_manifest_paths(evidence_path: Path, explanation_path: Path, output_format: str) -> str:
    evidence, explanation = _load_manifest_paths(evidence_path, explanation_path)
    report = build_report(evidence, explanation)
    _validate_schema(report, "report")
    return pretty_json(report) if output_format == "json" else render_report_markdown(report)


def _cache(args: argparse.Namespace) -> int:
    root = resolve_repository(args.repo)
    store = CacheStore(repository_identity(root), cache_root=args.cache_dir)
    value = store.status()
    if args.action == "clear":
        value = {"cleared": str(store.clear())}
    sys.stdout.write(pretty_json(value))
    return 0


def _install_skill(args: argparse.Namespace) -> int:
    source = files("shiftory.skills.shiftory").joinpath("SKILL.md").read_text(encoding="utf-8")
    default_targets = {
        "copilot": Path(".github/skills/shiftory"),
        "claude": Path(".claude/skills/shiftory"),
        "generic": Path("skills/shiftory"),
    }
    directory = (args.directory or default_targets[args.target]).expanduser()
    target = directory / "SKILL.md"
    absolute_target = target if target.is_absolute() else Path.cwd() / target
    current = Path(absolute_target.anchor)
    for component in absolute_target.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValidationError(f"Refusing to install through a symbolic link at {current}")
    if directory.exists() and not directory.is_dir():
        raise ValidationError(f"Skill directory is not a directory: {directory}")
    if target.exists() and not target.is_file():
        raise ValidationError(f"Skill target is not a regular file: {target}")
    if target.exists() and target.read_text(encoding="utf-8") != source:
        raise ValidationError(f"Refusing to overwrite unrelated skill content at {target}")
    directory.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(source, encoding="utf-8")
    sys.stdout.write(pretty_json({"installed": str(target.resolve()), "target": args.target}))
    return 0


_RUN_NAME = re.compile(r"[0-9a-f]{32}")


def _require_private_directory(path: Path, label: str) -> None:
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValidationError(f"{label} is not a directory: {path}")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ValidationError(f"{label} is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValidationError(f"{label} must be owner-only (mode 0700): {path}")


def _require_private_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValidationError(f"{label} must not be a symbolic link: {path}")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValidationError(f"{label} is not a regular file: {path}")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ValidationError(f"{label} is not owned by the current user: {path}")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValidationError(f"{label} must be owner-only (mode 0600): {path}")


def _run_root() -> Path:
    configured = os.environ.get("SHIFTORY_RUN_DIR")
    candidate = (
        Path(configured).expanduser() if configured else user_state_path("shiftory") / "runs"
    )
    if candidate.is_symlink():
        raise ValidationError(f"Run root must not be a symbolic link: {candidate}")
    candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = candidate.resolve()
    if root == Path(root.anchor):
        raise ValidationError(f"Refusing to use a filesystem root for runs: {root}")
    _require_private_directory(root, "Run root")
    return root


def _new_run() -> Path:
    run = _run_root() / uuid.uuid4().hex
    run.mkdir(mode=0o700)
    run.chmod(stat.S_IRWXU)
    return run


def _write_private(path: Path, payload: str) -> None:
    if path.is_symlink():
        raise ValidationError(
            f"Refusing to write a private artifact through a symbolic link: {path}"
        )
    path.write_text(payload, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _resume_location(resume: Path) -> tuple[Path, Path]:
    root = _run_root()
    supplied = resume.expanduser()
    if supplied.is_symlink():
        raise ValidationError(f"Resume descriptor must not be a symbolic link: {supplied}")
    descriptor_path = supplied.resolve()
    try:
        relative = descriptor_path.relative_to(root)
    except ValueError as exc:
        raise ValidationError(
            f"Resume descriptor is outside the Shiftory run root: {descriptor_path}"
        ) from exc
    if (
        len(relative.parts) != 2
        or _RUN_NAME.fullmatch(relative.parts[0]) is None
        or relative.parts[1] != "run.json"
    ):
        raise ValidationError(
            f"Resume descriptor must be an immediate generated run/run.json path: {descriptor_path}"
        )
    run = root / relative.parts[0]
    if run.is_symlink():
        raise ValidationError(f"Run directory must not be a symbolic link: {run}")
    _require_private_directory(run, "Run directory")
    _require_private_file(descriptor_path, "Resume descriptor")
    return run, descriptor_path


def _descriptor_path(descriptor: dict[str, Any], key: str, run: Path, filename: str) -> Path:
    value = descriptor.get(key)
    if not isinstance(value, str):
        raise ValidationError(f"Resume descriptor field {key!r} must be a path string")
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValidationError(f"Resume descriptor field {key!r} targets a symbolic link")
    resolved = path.resolve()
    expected = (run / filename).resolve()
    if resolved != expected:
        raise ValidationError(
            f"Resume descriptor field {key!r} must target {expected}, not {resolved}"
        )
    return resolved


def _validate_analysis_args(args: argparse.Namespace) -> None:
    if args.remote is not None and args.pr is None:
        raise ScopeError("--remote is only valid with --pr")
    if args.max_evidence_bytes < 0:
        raise ValidationError("--max-evidence-bytes must be non-negative")
    if args.context_lines < 0:
        raise ValidationError("--context-lines must be non-negative")


def _validate_explain_args(args: argparse.Namespace) -> None:
    _validate_analysis_args(args)
    if args.explanation is not None and args.resume is None:
        raise ValidationError("--explanation requires --resume")
    if args.output is not None and args.resume is None:
        raise ValidationError("--output requires --resume")
    if args.resume is None:
        return
    incompatible = any(
        (
            _scope(args).selected(),
            args.parent is not None,
            args.remote is not None,
            args.repo != ".",
            args.graphora != "auto",
            args.max_evidence_bytes != 1_000_000,
            args.context_lines != 3,
            args.cache_dir is not None,
            args.no_cache,
            args.paths,
        )
    )
    if incompatible:
        raise ValidationError(
            "--resume uses its recorded evidence and cannot be combined with "
            "scope or analysis options"
        )


def _explain(args: argparse.Namespace) -> int:
    run: Path | None = None
    try:
        _validate_explain_args(args)
        if args.resume:
            run, descriptor_path = _resume_location(args.resume)
        else:
            run, descriptor_path = _new_run(), None
        return _explain_in_run(args, run, descriptor_path)
    except (ShiftoryError, OSError, ValueError) as error:
        if run is None:
            run = _new_run()
        diagnostic_path = run / "diagnostic.json"
        code = error.code if isinstance(error, ShiftoryError) else "validation_error"
        prior_details = error.details if isinstance(error, ShiftoryError) else None
        details = {
            **(prior_details or {}),
            "artifacts": str(run),
            "diagnostic": str(diagnostic_path),
        }
        _write_private(
            diagnostic_path,
            pretty_json(
                {
                    "schema": "shiftory.diagnostic/v1",
                    "error": code,
                    "message": str(error),
                    "details": details,
                }
            ),
        )
        if isinstance(error, ShiftoryError):
            error.details = details
            raise
        raise ValidationError(str(error), details=details) from error


def _explain_in_run(args: argparse.Namespace, run: Path, descriptor_path: Path | None) -> int:
    keep = args.keep_artifacts or os.environ.get("SHIFTORY_KEEP_ARTIFACTS") == "1"
    if args.resume:
        assert descriptor_path is not None
        descriptor = load_json(descriptor_path)
        if descriptor.get("schema") != "shiftory.run/v1":
            raise ValidationError("The resume descriptor is not shiftory.run/v1")
        if descriptor.get("status") != "awaiting_explanation":
            raise ValidationError("The resume descriptor is not awaiting an explanation")
        if args.explanation is None:
            raise ValidationError("--resume requires --explanation")
        evidence_path = _descriptor_path(descriptor, "evidence", run, "evidence.json")
        recorded_explanation = _descriptor_path(descriptor, "explanation", run, "explanation.json")
        _descriptor_path(descriptor, "report", run, "report.md")
        _require_private_file(evidence_path, "Evidence")
        recorded_identity = descriptor.get("comparison_identity")
        evidence_comparison = load_json(evidence_path).get("comparison", {})
        evidence_identity = evidence_comparison.get("identity")
        if not isinstance(recorded_identity, str) or evidence_identity != recorded_identity:
            raise ValidationError("Evidence does not match the descriptor comparison identity")
        recorded_paths = descriptor.get("path_scope")
        evidence_paths = evidence_comparison.get("paths", [])
        if (
            not isinstance(recorded_paths, list)
            or not all(isinstance(path, str) for path in recorded_paths)
            or recorded_paths != evidence_paths
        ):
            raise ValidationError("Evidence does not match the descriptor path scope")
        explanation_path = args.explanation.expanduser().resolve()
        if explanation_path != recorded_explanation:
            raise ValidationError(
                f"--explanation must be the run's recorded path: {recorded_explanation}"
            )
        _require_private_file(explanation_path, "Explanation")
    else:
        evidence_path = run / "evidence.json"
        evidence = analyze(_options(args)).to_dict()
        _validate_schema(evidence, "evidence")
        _write_private(evidence_path, canonical_json(evidence))
        template_path = run / "explanation.json"
        _write_private(
            template_path,
            pretty_json(
                {
                    "schema": "shiftory.explanation/v1",
                    "summary": "",
                    "items": [],
                    "coverage_owners": [],
                }
            ),
        )
        descriptor_path = run / "run.json"
        descriptor = {
            "schema": "shiftory.run/v1",
            "status": "awaiting_explanation",
            "comparison_identity": evidence["comparison"]["identity"],
            "path_scope": evidence["comparison"].get("paths", []),
            "evidence": str(evidence_path),
            "explanation": str(template_path),
            "report": str(run / "report.md"),
            "evidence_budget": {
                "requested_bytes": args.max_evidence_bytes,
                "actual_bytes": evidence["metrics"]["evidence_bytes"],
            },
            "schema_command": ["shiftory", "schema", "explanation"],
            "verify_command": [
                "shiftory",
                "verify",
                "--evidence",
                str(evidence_path),
                "--explanation",
                str(template_path),
            ],
            "render_command": [
                "shiftory",
                "render",
                "--evidence",
                str(evidence_path),
                "--explanation",
                str(template_path),
            ],
            "resume_command": [
                "shiftory",
                "explain",
                "--resume",
                str(descriptor_path),
                "--explanation",
                str(template_path),
            ],
        }
        _write_private(descriptor_path, pretty_json(descriptor))
        sys.stdout.write(pretty_json({**descriptor, "descriptor": str(descriptor_path)}))
        return 0
    report = _verify_manifest_paths(evidence_path, explanation_path)
    verification_path = run / "verification.json"
    _write_private(
        verification_path,
        canonical_json(
            {
                "valid": True,
                "comparison_identity": report["comparison_identity"],
                "coverage": report["coverage"],
            }
        ),
    )
    payload = _render_manifest_paths(evidence_path, explanation_path, "markdown")
    report_path = run / "report.md"
    _write_private(report_path, payload)
    _write_or_print(payload, args.output)
    if not keep:
        shutil.rmtree(run)
    else:
        assert descriptor_path is not None
        descriptor["status"] = "complete"
        descriptor["verification"] = str(verification_path)
        _write_private(descriptor_path, pretty_json(descriptor))
        sys.stderr.write(f"Shiftory artifacts retained at {run}\n")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "analyze": _analyze,
        "verify": _verify,
        "render": _render,
        "schema": lambda value: _print_schema(value.name),
        "cache": _cache,
        "install-skill": _install_skill,
        "explain": _explain,
    }
    return handlers[args.command](args)


def _print_schema(name: str) -> int:
    sys.stdout.write(pretty_json(load_schema(cast(SchemaName, name))))
    return 0


def _fail(error: Exception) -> NoReturn:
    if isinstance(error, ShiftoryError):
        payload: dict[str, Any] = {"error": error.code, "message": str(error)}
        if error.details:
            payload["details"] = error.details
        exit_code = error.exit_code
    else:
        payload = {"error": "internal_error", "message": str(error)}
        exit_code = 1
    sys.stderr.write(pretty_json(payload))
    raise SystemExit(exit_code)


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(_parser().parse_args(argv))
    except (ShiftoryError, OSError, ValueError, json.JSONDecodeError) as error:
        _fail(error)


if __name__ == "__main__":
    main()
