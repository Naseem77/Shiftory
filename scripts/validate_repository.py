#!/usr/bin/env python3
"""Validate repository assets that are not exercised by unit tests."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft202012Validator

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
GRAPHORA_REQUIREMENT = "graphora-kg==0.2.1"
TREE_SITTER_CONSTRAINT = "tree-sitter==0.25.2"
RELEASE_TARGET = re.compile(
    r"- runner: (?P<runner>\S+)\s+"
    r'python-version: "(?P<python>3\.\d+)"\s+'
    r"expected-wheel-count: (?P<count>\d+)"
)


def github_anchor(heading: str) -> str:
    value = heading.strip().lower()
    value = re.sub(r"[^\w\-\s]", "", value)
    return re.sub(r"[\s]+", "-", value)


def validate_json_and_schemas() -> None:
    json_paths = [
        *sorted((ROOT / "src" / "shiftory" / "schemas").glob("*.json")),
        *sorted((ROOT / "benchmarks").rglob("*.json")),
    ]
    for path in json_paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if path.parent.name == "schemas":
            Draft202012Validator.check_schema(value)


def validate_markdown_links() -> None:
    errors: list[str] = []
    markdown_paths = sorted(
        {
            *ROOT.glob("*.md"),
            *(ROOT / "docs").rglob("*.md"),
            *(ROOT / "benchmarks").rglob("*.md"),
            *(ROOT / "skills").rglob("*.md"),
            *(ROOT / "src" / "shiftory" / "skills").rglob("*.md"),
        }
    )
    for source in markdown_paths:
        text = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_text, separator, fragment = target.partition("#")
            destination = (
                source if not path_text else (source.parent / unquote(path_text)).resolve()
            )
            if not destination.exists() or (fragment and not destination.is_file()):
                errors.append(f"{source.relative_to(ROOT)}: missing link target {target}")
                continue
            if separator and destination.suffix.lower() == ".md":
                headings = {
                    github_anchor(match.group(1))
                    for match in HEADING.finditer(destination.read_text(encoding="utf-8"))
                }
                if unquote(fragment).lower() not in headings:
                    errors.append(f"{source.relative_to(ROOT)}: missing anchor {target}")
    if errors:
        raise SystemExit("\n".join(errors))


def validate_bundled_assets() -> None:
    source_skill = ROOT / "skills" / "shiftory" / "SKILL.md"
    bundled_skill = ROOT / "src" / "shiftory" / "skills" / "shiftory" / "SKILL.md"
    if source_skill.read_bytes() != bundled_skill.read_bytes():
        raise SystemExit("The source and bundled Shiftory skills differ")


def validate_graphora_contract() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = project.get("dependencies", [])
    if dependencies.count(GRAPHORA_REQUIREMENT) != 1:
        raise SystemExit(f"pyproject.toml must contain exactly one {GRAPHORA_REQUIREMENT}")

    for filename in ("constraints-dev.txt", "constraints-release.txt"):
        lines = {
            line.strip()
            for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if GRAPHORA_REQUIREMENT not in lines or TREE_SITTER_CONSTRAINT not in lines:
            raise SystemExit(
                f"{filename} must pin {GRAPHORA_REQUIREMENT} and {TREE_SITTER_CONSTRAINT}"
            )

    stale: list[str] = []
    text_suffixes = {"", ".json", ".md", ".py", ".toml", ".txt", ".yml", ".yaml"}
    ignored_parts = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        if any(part in ignored_parts for part in path.relative_to(ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "0.2." + "0" in text:
            stale.append(path.relative_to(ROOT).as_posix())
    if stale:
        raise SystemExit(f"Stale active Graphora prior-version references: {', '.join(stale)}")


def validate_release_supply_chain() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    targets = {
        (match["runner"], match["python"], int(match["count"]))
        for match in RELEASE_TARGET.finditer(workflow)
    }
    expected = {
        (runner, version, 25 if version == "3.10" else 24)
        for runner in ("ubuntu-latest", "macos-latest")
        for version in ("3.10", "3.11", "3.12")
    }
    if targets != expected:
        raise SystemExit(f"Release wheel matrix is incomplete: {sorted(targets)}")
    required = (
        "--only-binary=:all:",
        "--license-materials-dir dist/license-materials",
        "--licenses-output dist/shiftory.notice-license-inventory.json",
        "release-${{ runner.os }}-${{ runner.arch }}-python-${{ matrix.python-version }}",
    )
    if any(value not in workflow for value in required):
        raise SystemExit("Release workflow does not preserve exact binary license artifacts")


def main() -> None:
    validate_json_and_schemas()
    validate_markdown_links()
    validate_bundled_assets()
    validate_graphora_contract()
    validate_release_supply_chain()
    print("Repository schemas, links, skills, and license materials are valid.")


if __name__ == "__main__":
    main()
