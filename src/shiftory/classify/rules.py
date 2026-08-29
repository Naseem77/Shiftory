"""Explicit deterministic evidence-organization rules."""

from __future__ import annotations

from pathlib import PurePosixPath

from shiftory.models.core import Confidence, FileChange

_LOCKFILES = {
    "cargo.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}
_DEPENDENCIES = {
    "cargo.toml",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "go.sum",
}
_CONFIG_NAMES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "compose.yaml",
    "compose.yml",
    "dockerfile",
    "makefile",
    "tox.ini",
}
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
}


def _is_formatting_only(file: FileChange) -> bool:
    before = [line.content for hunk in file.hunks for line in hunk.lines if line.side == "before"]
    after = [line.content for hunk in file.hunks for line in hunk.lines if line.side == "after"]
    if not before or not after:
        return False
    raw_before = "\n".join(before)
    raw_after = "\n".join(after)
    return raw_before != raw_after and "".join(raw_before.split()) == "".join(raw_after.split())


def classify_file(file: FileChange) -> tuple[str, Confidence]:
    path = PurePosixPath(file.new_path or file.old_path or "")
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    suffix = path.suffix.lower()
    kinds = {unit.kind for unit in file.units}
    if "unsupported" in kinds:
        return "unsupported", "extracted"
    if "binary" in kinds:
        return "binary", "extracted"
    if "rename" in kinds:
        return "rename", "extracted"
    if file.status == "added":
        return "added", "extracted"
    if file.status == "deleted":
        return "deleted", "extracted"
    if kinds == {"mode"}:
        return "mode", "extracted"
    if not file.hunks:
        return "structural", "extracted"
    if name in _LOCKFILES or name in _DEPENDENCIES:
        return "dependency", "extracted"
    if "generated" in parts or name.endswith((".min.js", ".min.css")):
        return "generated", "inferred"
    if (
        suffix == ".schema"
        or "schema" in parts
        or ("schema" in name and suffix in {".json", ".yaml", ".yml", ".graphql"})
    ):
        return "schema", "inferred"
    if (
        name in _CONFIG_NAMES
        or ".github" in parts
        or suffix in {".ini", ".toml", ".yaml", ".yml"}
        or (name.startswith(".") and name.endswith("rc"))
    ):
        return "configuration", "inferred"
    if "test" in parts or "tests" in parts or name.startswith("test_") or ".test." in name:
        return "tests", "inferred"
    if "docs" in parts or suffix in {".md", ".rst", ".adoc"}:
        return "docs", "inferred"
    if _is_formatting_only(file):
        return "formatting", "inferred"
    if suffix in _SOURCE_SUFFIXES:
        return "behavioral", "inferred"
    return "unresolved", "unresolved"
