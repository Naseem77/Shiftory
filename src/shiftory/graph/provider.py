"""Typed boundary around Graphora's package-root public API."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast

from shiftory.cache.store import CACHE_SCHEMA
from shiftory.diff.identity import stable_id
from shiftory.errors import GraphoraError
from shiftory.models.core import GraphFact, GraphResult, Side
from shiftory.models.json import canonical_json

GRAPHORA_VERSION = "0.2.1"
GRAPHORA_WHEEL_SHA256 = "6b39eab0dc8aa7fc2aec9912d1506306556ca5cacd76447aa00e8afb6ef358d9"
GRAPHORA_PACKAGE_CODE_SHA256 = "74d1761010cc20ad27d3bb61f30a2e01ad19c1b0a4f6737edb8e2951183cc4e4"
GRAPH_CACHE_SCHEMA = "shiftory.graphora-cache/v1"
GRAPH_WORKER_REQUEST_SCHEMA = "shiftory.graphora-worker-request/v1"
GRAPH_WORKER_RESULT_SCHEMA = "shiftory.graphora-worker-result/v1"
GRAPH_WORKER_TIMEOUT_SECONDS = 300.0
GraphoraMode = Literal["auto", "off", "required"]
FactConfidence = Literal["extracted", "inferred", "ambiguous", "unresolved", "unavailable"]
_WORKER_BOOTSTRAP = """
import pathlib
import runpy
import sys

source = pathlib.Path(sys.argv.pop(1)).resolve(strict=True)
sys.path.insert(0, str(source))
runpy.run_module("shiftory.graph.worker", run_name="__main__", alter_sys=True)
"""

_SUPPORTED_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".go",
    ".h",
    ".hh",
    ".hpp",
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
_BRACE_DELIMITED_SUFFIXES = _SUPPORTED_SUFFIXES - {".py", ".rb"}
_INDEX_IGNORES = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}


class GraphoraProvider(Protocol):
    name: str
    version: str

    def enrich(
        self,
        snapshot: Path,
        *,
        project: str,
        data_dir: Path,
        patch: str,
        changed_paths: tuple[str, ...],
        side: Side = "after",
        changed_lines: dict[str, tuple[int, ...]] | None = None,
    ) -> GraphResult: ...


def _confidence(value: object) -> FactConfidence:
    normalized = str(value or "").strip().lower()
    if normalized in {"extracted", "inferred", "ambiguous", "unresolved", "unavailable"}:
        return cast(FactConfidence, normalized)
    return "unresolved"


def _normalized_path(value: object) -> str | None:
    raw = str(value or "").replace("\\", "/")
    if not raw:
        return None
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    normalized = candidate.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized if normalized not in {"", "."} else None


def graph_cache_identity(repository_id: str, snapshot_id: str) -> str:
    payload = (
        f"{GRAPH_CACHE_SCHEMA}\0{CACHE_SCHEMA}\0{GRAPHORA_VERSION}\0{repository_id}\0{snapshot_id}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class _GraphoraWorkerEngine:
    name = "graphora"
    version = GRAPHORA_VERSION

    def __init__(self) -> None:
        installed = importlib.metadata.version("graphora-kg")
        if installed != GRAPHORA_VERSION:
            raise GraphoraError(f"graphora-kg=={GRAPHORA_VERSION} is required; found {installed}")

    def enrich(
        self,
        snapshot: Path,
        *,
        project: str,
        data_dir: Path,
        patch: str,
        changed_paths: tuple[str, ...],
        side: Side = "after",
        changed_lines: dict[str, tuple[int, ...]] | None = None,
    ) -> GraphResult:
        # Side-specific changed lines are more precise than Graphora's two-sided diff helper.
        del patch
        import graphora

        if getattr(graphora, "__version__", None) != GRAPHORA_VERSION:
            raise GraphoraError("Graphora runtime version does not match its package metadata")

        snapshot = snapshot.resolve()
        if not snapshot.is_dir():
            raise GraphoraError(f"Graphora snapshot is not a directory: {snapshot}")
        paths = self._changed_paths(changed_paths)
        lines = {
            normalized: tuple(sorted(set(values)))
            for path, values in (changed_lines or {}).items()
            if (normalized := _normalized_path(path)) is not None
        }
        repository_id, snapshot_id = self._cache_components(snapshot, data_dir, project)
        snapshot_hash = self._snapshot_hash(snapshot, data_dir.resolve())
        cache_key = graph_cache_identity(repository_id, f"{snapshot_id}:{snapshot_hash}")
        artifact_dir = data_dir.resolve() / cache_key
        graph_project = f"shiftory-{cache_key}"

        with self._cache_lock(data_dir.resolve()):
            store = self._open_index(
                graphora,
                snapshot,
                artifact_dir,
                graph_project,
                cache_key,
                repository_id,
                snapshot_id,
                snapshot_hash,
            )
            facts, selected_names, parsed_definitions = self._parse_changed_files(
                graphora, snapshot, paths, lines, side
            )
            impact = graphora.blast_radius(store, sorted(selected_names))
            facts.extend(self._normalize_impact(impact, snapshot, paths, side, parsed_definitions))

        existing_paths = tuple(
            path for path in paths if self._valid_reference(snapshot, path, None)
        )
        if not facts and existing_paths:
            facts.extend(
                self._fact(
                    "changed_file",
                    side,
                    path,
                    None,
                    None,
                    None,
                    "unresolved",
                    "graphora:no-resolved-symbol",
                )
                for path in existing_paths
            )
        unique = {fact.id: fact for fact in facts}
        return GraphResult(
            "available",
            self.name,
            self.version,
            tuple(
                sorted(
                    unique.values(),
                    key=lambda fact: (
                        fact.path,
                        fact.line or 0,
                        fact.kind,
                        fact.symbol or "",
                        fact.target or "",
                        fact.id,
                    ),
                )
            ),
            cache_key=cache_key,
        )

    @staticmethod
    def _changed_paths(changed_paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for path in changed_paths:
            value = _normalized_path(path)
            if value is None:
                raise GraphoraError(f"Unsafe changed path supplied to Graphora: {path!r}")
            normalized.append(value)
        return tuple(sorted(set(normalized)))

    @staticmethod
    def _cache_components(snapshot: Path, data_dir: Path, project: str) -> tuple[str, str]:
        candidate_repository = data_dir.resolve().parent.parent.name
        repository_id = (
            candidate_repository
            if len(candidate_repository) == 64
            and all(character in "0123456789abcdef" for character in candidate_repository)
            else hashlib.sha256(f"repository\0{snapshot}".encode()).hexdigest()
        )
        snapshot_id = data_dir.name
        if not snapshot_id or snapshot_id in {".", ".."}:
            snapshot_id = hashlib.sha256(f"snapshot\0{project}\0{snapshot}".encode()).hexdigest()
        return repository_id, snapshot_id

    @staticmethod
    def _snapshot_hash(snapshot: Path, data_dir: Path) -> str:
        excluded_top_level: str | None = None
        with contextlib.suppress(IndexError, ValueError):
            excluded_top_level = data_dir.relative_to(snapshot).parts[0]
        digest = hashlib.sha256()
        for path in sorted(snapshot.rglob("*")):
            try:
                relative = path.relative_to(snapshot)
            except ValueError:
                continue
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in _SUPPORTED_SUFFIXES
                or any(part in _INDEX_IGNORES for part in relative.parts)
                or (excluded_top_level is not None and relative.parts[0] == excluded_top_level)
            ):
                continue
            content = path.read_bytes()
            digest.update(relative.as_posix().encode("utf-8", "surrogateescape"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(content).digest())
            digest.update(b"\0")
        return digest.hexdigest()

    def _open_index(
        self,
        graphora: Any,
        snapshot: Path,
        artifact_dir: Path,
        project: str,
        cache_key: str,
        repository_id: str,
        snapshot_id: str,
        snapshot_hash: str,
    ) -> Any:
        graph_path = artifact_dir / f"{project}.json"
        manifest_path = artifact_dir / "manifest.json"
        expected = {
            "schema": GRAPH_CACHE_SCHEMA,
            "cache_schema": CACHE_SCHEMA,
            "graphora_version": GRAPHORA_VERSION,
            "cache_key": cache_key,
            "project": project,
            "repository_id": repository_id,
            "source_snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_hash,
        }
        if not self._valid_cached_graph(manifest_path, graph_path, expected):
            self._safe_rebuild_directory(artifact_dir)
            artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            store = graphora.open_store(
                project,
                backend="embedded",
                data_dir=str(artifact_dir),
            )
            if getattr(store, "backend", None) != "embedded":
                raise GraphoraError("Graphora did not honor the required embedded backend")
            graphora.index_repository(snapshot, project=project, store=store)
            if not graph_path.is_file():
                raise GraphoraError("Graphora embedded backend did not persist its index")
            graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
            self._atomic_json_write(manifest_path, {**expected, "graph_sha256": graph_hash})
            return store
        store = graphora.open_store(
            project,
            backend="embedded",
            data_dir=str(artifact_dir),
        )
        if getattr(store, "backend", None) != "embedded":
            raise GraphoraError("Graphora did not honor the required embedded backend")
        return store

    @staticmethod
    def _valid_cached_graph(
        manifest_path: Path,
        graph_path: Path,
        expected: dict[str, str],
    ) -> bool:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return False
        return (
            isinstance(manifest, dict)
            and all(manifest.get(key) == value for key, value in expected.items())
            and isinstance(manifest.get("graph_sha256"), str)
            and manifest["graph_sha256"] == graph_hash
        )

    @staticmethod
    def _safe_rebuild_directory(path: Path) -> None:
        if path.exists():
            if path.is_symlink() or not path.is_dir() or len(path.name) != 64:
                raise GraphoraError(f"Refusing unsafe Graphora cache replacement: {path}")
            shutil.rmtree(path)

    @staticmethod
    @contextlib.contextmanager
    def _cache_lock(data_dir: Path) -> Iterator[None]:
        data_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = data_dir.parent / ".lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_json_write(path: Path, value: dict[str, str]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
        try:
            temporary.write_text(canonical_json(value), encoding="utf-8")
            temporary.chmod(0o600)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            with contextlib.suppress(OSError):
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def _parse_changed_files(
        self,
        graphora: Any,
        snapshot: Path,
        paths: tuple[str, ...],
        changed_lines: dict[str, tuple[int, ...]],
        side: Side,
    ) -> tuple[list[GraphFact], set[str], set[tuple[str, str, int]]]:
        facts: list[GraphFact] = []
        selected_names: set[str] = set()
        definitions: set[tuple[str, str, int]] = set()
        for path in paths:
            candidate = snapshot / path
            if not candidate.is_file():
                continue
            if not self._valid_reference(snapshot, path, None):
                raise GraphoraError(f"Changed path escapes the Graphora snapshot: {path!r}")
            if not changed_lines.get(path):
                facts.append(
                    self._fact(
                        "changed_file",
                        side,
                        path,
                        None,
                        None,
                        None,
                        "unresolved",
                        "graphora:no-changed-lines",
                    )
                )
                continue
            if candidate.suffix.lower() not in _SUPPORTED_SUFFIXES:
                facts.append(
                    self._fact(
                        "unsupported",
                        side,
                        path,
                        None,
                        None,
                        None,
                        "unavailable",
                        "graphora:unsupported-language",
                    )
                )
                continue
            try:
                content = candidate.read_text(encoding="utf-8")
                parsed = graphora.parse_code_file(path, content)
                symbols = list(parsed.symbols)
            except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as exc:
                facts.append(
                    self._fact(
                        "changed_file",
                        side,
                        path,
                        None,
                        None,
                        None,
                        "unavailable",
                        f"graphora:parse-unavailable:{type(exc).__name__}",
                    )
                )
                continue
            parser_used = str(getattr(parsed, "parser_used", "")).strip().lower()
            provenance = (
                f"graphora:{parser_used}"
                if parser_used in {"tree-sitter", "regex"}
                else "graphora:unknown-parser"
            )
            requested_lines = tuple(sorted(set(changed_lines.get(path, ()))))
            file_line_count = len(content.splitlines())
            valid_lines = tuple(line for line in requested_lines if 1 <= line <= file_line_count)
            symbol_ranges = self._symbol_ranges(candidate.suffix.lower(), content, symbols)
            selected, unresolved_lines = self._enclosing_symbols(
                symbols, valid_lines, symbol_ranges
            )
            for symbol, direct in selected:
                name = str(getattr(symbol, "name", "")).strip()
                line = self._line_number(getattr(symbol, "line", None))
                if not name or line is None:
                    raise GraphoraError("Graphora returned a malformed parsed symbol")
                confidence = _confidence(getattr(symbol, "confidence", "unresolved"))
                if parser_used == "regex" and confidence == "extracted":
                    confidence = "inferred"
                definitions.add((name, path, line))
                selected_names.add(name)
                facts.append(
                    self._fact(
                        "definition",
                        side,
                        path,
                        line,
                        name,
                        None,
                        confidence,
                        provenance,
                    )
                )
                facts.append(
                    self._fact(
                        "changed_symbol" if direct else "enclosing_symbol",
                        side,
                        path,
                        line,
                        name,
                        None,
                        confidence if direct else "inferred",
                        provenance,
                    )
                )
            facts.extend(
                self._fact(
                    "changed_file",
                    side,
                    path,
                    line,
                    None,
                    None,
                    "unresolved",
                    f"{provenance}:no-verified-containment",
                )
                for line in unresolved_lines
            )
            if len(valid_lines) != len(requested_lines):
                facts.append(
                    self._fact(
                        "changed_file",
                        side,
                        path,
                        None,
                        None,
                        None,
                        "unresolved",
                        f"{provenance}:invalid-changed-line",
                    )
                )
        return facts, selected_names, definitions

    @staticmethod
    def _enclosing_symbols(
        symbols: list[object],
        changed: tuple[int, ...],
        ranges: dict[tuple[str, int], tuple[int, int]],
    ) -> tuple[list[tuple[object, bool]], tuple[int, ...]]:
        ordered = sorted(
            symbols,
            key=lambda symbol: (
                GraphoraAdapter._line_number(getattr(symbol, "line", None)) or 0,
                str(getattr(symbol, "name", "")),
            ),
        )
        selected: dict[tuple[str, int], tuple[object, bool]] = {}
        unresolved: list[int] = []
        for changed_line in sorted(set(changed)):
            direct = [
                symbol
                for symbol in ordered
                if GraphoraAdapter._line_number(getattr(symbol, "line", None)) == changed_line
            ]
            if direct:
                candidates = direct
                is_direct = True
            else:
                containing = [
                    symbol
                    for symbol in ordered
                    if (identity := GraphoraAdapter._symbol_identity(symbol)) in ranges
                    and ranges[identity][0] < changed_line <= ranges[identity][1]
                ]
                candidates = GraphoraAdapter._innermost_symbols(containing, ranges)
                is_direct = False
            if not candidates:
                unresolved.append(changed_line)
                continue
            for symbol in candidates:
                symbol_line = GraphoraAdapter._line_number(getattr(symbol, "line", None))
                assert symbol_line is not None
                key = (str(getattr(symbol, "name", "")), symbol_line)
                previous = selected.get(key)
                selected[key] = (symbol, is_direct or (previous[1] if previous else False))
        return (
            [selected[key] for key in sorted(selected, key=lambda item: (item[1], item[0]))],
            tuple(unresolved),
        )

    @staticmethod
    def _innermost_symbols(
        symbols: list[object],
        ranges: dict[tuple[str, int], tuple[int, int]],
    ) -> list[object]:
        if not symbols:
            return []
        ranked = sorted(
            symbols,
            key=lambda symbol: (
                -ranges[GraphoraAdapter._symbol_identity(symbol)][0],
                ranges[GraphoraAdapter._symbol_identity(symbol)][1],
                GraphoraAdapter._symbol_identity(symbol),
            ),
        )
        best_range = ranges[GraphoraAdapter._symbol_identity(ranked[0])]
        best = [
            symbol
            for symbol in ranked
            if ranges[GraphoraAdapter._symbol_identity(symbol)] == best_range
        ]
        return best if len(best) == 1 else []

    @staticmethod
    def _symbol_identity(symbol: object) -> tuple[str, int]:
        return (
            str(getattr(symbol, "name", "")).strip(),
            GraphoraAdapter._line_number(getattr(symbol, "line", None)) or 0,
        )

    @staticmethod
    def _symbol_ranges(
        suffix: str,
        content: str,
        symbols: list[object],
    ) -> dict[tuple[str, int], tuple[int, int]]:
        if suffix == ".py":
            return GraphoraAdapter._python_symbol_ranges(content)
        if suffix in _BRACE_DELIMITED_SUFFIXES:
            return GraphoraAdapter._brace_symbol_ranges(content, symbols)
        return {}

    @staticmethod
    def _python_symbol_ranges(content: str) -> dict[tuple[str, int], tuple[int, int]]:
        try:
            tree = ast.parse(content)
        except (SyntaxError, ValueError):
            return {}
        ranges: dict[tuple[str, int], tuple[int, int]] = {}
        ambiguous: set[tuple[str, int]] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            end_line = GraphoraAdapter._line_number(getattr(node, "end_lineno", None))
            if end_line is None:
                continue
            identity = (node.name, node.lineno)
            if identity in ranges:
                ambiguous.add(identity)
            ranges[identity] = (node.lineno, end_line)
        for identity in ambiguous:
            ranges.pop(identity, None)
        return ranges

    @staticmethod
    def _brace_symbol_ranges(
        content: str,
        symbols: list[object],
    ) -> dict[tuple[str, int], tuple[int, int]]:
        lines = content.splitlines()
        brace_ends = GraphoraAdapter._brace_ends(content)
        line_offsets: list[int] = []
        offset = 0
        for source_line in content.splitlines(keepends=True):
            line_offsets.append(offset)
            offset += len(source_line)

        ranges: dict[tuple[str, int], tuple[int, int]] = {}
        ambiguous: set[tuple[str, int]] = set()
        for symbol in symbols:
            identity = GraphoraAdapter._symbol_identity(symbol)
            line_number = identity[1]
            signature = str(getattr(symbol, "signature", "")).strip()
            if not signature or "{" not in signature or not 1 <= line_number <= len(lines):
                continue
            declaration = lines[line_number - 1]
            opening_column = declaration.find("{")
            signature_opening = signature.find("{")
            if (
                opening_column < 0
                or declaration[: opening_column + 1].strip()
                != signature[: signature_opening + 1].strip()
            ):
                continue
            opening = line_offsets[line_number - 1] + opening_column
            end_line = brace_ends.get(opening)
            if end_line is None:
                continue
            if identity in ranges:
                ambiguous.add(identity)
            ranges[identity] = (line_number, end_line)
        for identity in ambiguous:
            ranges.pop(identity, None)
        return ranges

    @staticmethod
    def _brace_ends(content: str) -> dict[int, int]:
        pairs: dict[int, int] = {}
        stack: list[int] = []
        state = "code"
        quote = ""
        escaped = False
        index = 0
        while index < len(content):
            character = content[index]
            following = content[index + 1] if index + 1 < len(content) else ""
            if state == "line-comment":
                if character == "\n":
                    state = "code"
            elif state == "block-comment":
                if character == "*" and following == "/":
                    state = "code"
                    index += 1
            elif state == "string":
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    state = "code"
            elif character == "/" and following == "/":
                state = "line-comment"
                index += 1
            elif character == "/" and following == "*":
                state = "block-comment"
                index += 1
            elif character in {'"', "'", "`"}:
                state = "string"
                quote = character
            elif character == "{":
                stack.append(index)
            elif character == "}":
                if not stack:
                    return {}
                opening = stack.pop()
                pairs[opening] = content.count("\n", 0, index) + 1
            index += 1
        return pairs if not stack and state not in {"block-comment", "string"} else {}

    def _normalize_impact(
        self,
        impact: object,
        snapshot: Path,
        changed_paths: tuple[str, ...],
        side: Side,
        parsed_definitions: set[tuple[str, str, int]],
    ) -> list[GraphFact]:
        try:
            impact_value = cast(Any, impact)
            unresolved = list(impact_value.unresolved)
            impacts = list(impact_value.symbols)
        except (AttributeError, TypeError) as exc:
            raise GraphoraError("Graphora returned a malformed blast radius") from exc

        facts: list[GraphFact] = []
        for symbol in unresolved:
            name = str(symbol).strip()
            if not name:
                raise GraphoraError("Graphora returned an empty unresolved symbol")
            facts.append(self._fact("symbol", side, "", None, name, None, "unresolved", "graphora"))

        names = Counter(str(getattr(item, "name", "")).strip() for item in impacts)
        for item in impacts:
            symbol = str(getattr(item, "name", "")).strip()
            raw_path = getattr(item, "path", None)
            line = self._line_number(getattr(item, "line", None))
            if not symbol or raw_path is None or line is None:
                raise GraphoraError("Graphora returned a malformed symbol impact")
            path = _normalized_path(raw_path)
            valid_definition = path is not None and self._valid_reference(snapshot, path, line)
            if not valid_definition:
                facts.append(
                    self._fact(
                        "definition",
                        side,
                        "",
                        None,
                        symbol,
                        None,
                        "unresolved",
                        "graphora:invalid-reference",
                    )
                )
            definition_path = path or ""
            definition_key = (symbol, definition_path, line)
            if valid_definition and definition_key not in parsed_definitions:
                assert path is not None
                facts.append(
                    self._fact(
                        "definition",
                        side,
                        path,
                        line,
                        symbol,
                        None,
                        "ambiguous" if names[symbol] > 1 else "unresolved",
                        "graphora:unknown-parser",
                    )
                )
            for attribute, kind in (
                ("callers", "caller"),
                ("callees", "callee"),
                ("tests", "static_test"),
                ("importers", "importer"),
            ):
                related_items = getattr(item, attribute, None)
                if related_items is None:
                    raise GraphoraError(f"Graphora symbol impact is missing {attribute}")
                for related in related_items:
                    target, related_path, related_confidence = self._relation(
                        related, kind, definition_path
                    )
                    normalized_path = _normalized_path(related_path)
                    provenance = "graphora:unknown-parser"
                    if normalized_path is None or not self._valid_reference(
                        snapshot, normalized_path, None
                    ):
                        normalized_path = ""
                        related_confidence = "unresolved"
                        provenance = "graphora:invalid-reference"
                    facts.append(
                        self._fact(
                            kind,
                            side,
                            normalized_path,
                            None,
                            symbol,
                            target,
                            related_confidence,
                            provenance,
                        )
                    )
        return list({fact.id: fact for fact in facts}.values())

    @staticmethod
    def _relation(
        related: object,
        kind: str,
        definition_path: str,
    ) -> tuple[str | None, object, FactConfidence]:
        if kind == "importer":
            if not isinstance(related, str) or not related:
                raise GraphoraError("Graphora returned a malformed importer")
            return None, related, "unresolved"
        if not isinstance(related, tuple | list) or len(related) != 3:
            raise GraphoraError(f"Graphora returned a malformed {kind} relationship")
        target = str(related[0]).strip()
        if not target:
            raise GraphoraError(f"Graphora returned an empty {kind} target")
        return target, related[1] or definition_path, _confidence(related[2])

    @staticmethod
    def _valid_reference(snapshot: Path, path: str, line: object) -> bool:
        normalized = _normalized_path(path)
        if normalized is None:
            return False
        candidate = (snapshot / normalized).resolve()
        try:
            candidate.relative_to(snapshot.resolve())
        except ValueError:
            return False
        if not candidate.is_file():
            return False
        if line is None:
            return True
        line_number = GraphoraAdapter._line_number(line)
        if line_number is None:
            return False
        try:
            return 1 <= line_number <= len(candidate.read_bytes().splitlines())
        except OSError:
            return False

    @staticmethod
    def _line_number(line: object) -> int | None:
        if isinstance(line, bool):
            return None
        try:
            number = int(str(line))
        except (TypeError, ValueError):
            return None
        return number if number >= 1 else None

    @staticmethod
    def _fact(
        kind: str,
        side: Side,
        path: str,
        line: object,
        symbol: str | None,
        target: str | None,
        confidence: FactConfidence,
        provenance: str,
    ) -> GraphFact:
        normalized_path = _normalized_path(path) if path else ""
        if path and normalized_path is None:
            raise GraphoraError(f"Refusing an unsafe Graphora fact path: {path!r}")
        assert normalized_path is not None
        line_number = GraphoraAdapter._line_number(line)
        payload = {
            "kind": kind,
            "side": side,
            "path": normalized_path,
            "line": line_number,
            "symbol": symbol,
            "target": target,
            "confidence": confidence,
            "provenance": provenance,
        }
        return GraphFact(
            stable_id("fact", payload),
            kind,
            side,
            normalized_path,
            line_number,
            symbol,
            target,
            confidence,
            provenance,
        )


class GraphoraAdapter(_GraphoraWorkerEngine):
    """Crash-isolated adapter for the pinned Graphora public API."""

    def __init__(self, *, timeout_seconds: float = GRAPH_WORKER_TIMEOUT_SECONDS) -> None:
        installed = importlib.metadata.version("graphora-kg")
        if installed != GRAPHORA_VERSION:
            raise GraphoraError(f"graphora-kg=={GRAPHORA_VERSION} is required; found {installed}")
        if not math.isfinite(timeout_seconds) or not (
            0 < timeout_seconds <= GRAPH_WORKER_TIMEOUT_SECONDS
        ):
            raise GraphoraError(
                f"Graphora worker timeout must be between 0 and "
                f"{GRAPH_WORKER_TIMEOUT_SECONDS:g} seconds"
            )
        self.timeout_seconds = timeout_seconds

    def enrich(
        self,
        snapshot: Path,
        *,
        project: str,
        data_dir: Path,
        patch: str,
        changed_paths: tuple[str, ...],
        side: Side = "after",
        changed_lines: dict[str, tuple[int, ...]] | None = None,
    ) -> GraphResult:
        del patch
        resolved_snapshot = snapshot.expanduser().resolve()
        resolved_data_dir = data_dir.expanduser().resolve()
        paths = self._changed_paths(changed_paths)
        lines = {
            normalized: tuple(sorted(set(values)))
            for path, values in (changed_lines or {}).items()
            if (normalized := _normalized_path(path)) is not None
        }
        request = {
            "schema": GRAPH_WORKER_REQUEST_SCHEMA,
            "operation": "probe",
            "snapshot": str(resolved_snapshot),
            "project": project,
            "data_dir": str(resolved_data_dir),
            "changed_paths": list(paths),
            "side": side,
            "changed_lines": {path: list(lines[path]) for path in sorted(lines)},
            "expected_provenance_sha256": None,
        }
        provenance = self._probe_provider(request, resolved_snapshot)
        if not provenance["artifact_verified"]:
            raise GraphoraError(
                "Graphora provider is editable or does not match its installed artifact",
                details={"provider_provenance": provenance},
            )
        request["operation"] = "enrich"
        request["expected_provenance_sha256"] = hashlib.sha256(
            canonical_json(provenance).encode("utf-8")
        ).hexdigest()
        try:
            payload = self._run_worker(request, resolved_snapshot)
            raw_result, confirmed_provenance = self._decode_worker_envelope(payload)
            if confirmed_provenance != provenance:
                raise GraphoraError(
                    "Graphora worker returned different provider provenance after verification",
                    details={"provider_provenance": confirmed_provenance},
                )
            if raw_result is None:
                raise GraphoraError("Graphora worker did not return a graph result")
            result = self._graph_result_from_json(raw_result)
            if any(
                fact.path and not self._valid_reference(resolved_snapshot, fact.path, fact.line)
                for fact in result.facts
            ):
                raise GraphoraError("Graphora worker returned a fact outside its source snapshot")
        except GraphoraError as exc:
            details = dict(exc.details or {})
            details.setdefault("provider_provenance", provenance)
            raise GraphoraError(str(exc), details=details) from exc
        provenance_diagnostic = {
            "code": "graphora_provider_provenance",
            "message": "Graphora provider origin and installed artifact were verified.",
            **provenance,
        }
        return GraphResult(
            result.status,
            result.provider,
            result.version,
            result.facts,
            (provenance_diagnostic,),
            result.cache_key,
        )

    def _probe_provider(self, request: dict[str, Any], cwd: Path) -> dict[str, Any]:
        probe_payload = self._run_worker(request, cwd)
        probe_result, provenance = self._decode_worker_envelope(probe_payload)
        if probe_result is not None:
            raise GraphoraError("Graphora provenance probe returned an unexpected graph result")
        return provenance

    def _run_worker(self, request: dict[str, Any], cwd: Path) -> str:
        command = [
            sys.executable,
            "-I",
            "-B",
            "-X",
            "faulthandler",
            "-c",
            _WORKER_BOOTSTRAP,
            str(Path(__file__).resolve().parents[2]),
        ]
        try:
            process = subprocess.run(
                command,
                input=canonical_json(request),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._worker_environment(),
                cwd=cwd,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GraphoraError(
                f"Graphora worker timed out after {self.timeout_seconds:g} seconds",
                details={"timeout_seconds": self.timeout_seconds},
            ) from exc
        except OSError as exc:
            raise GraphoraError(f"Unable to start Graphora worker: {exc}") from exc

        if process.returncode:
            detail = self._worker_exit_detail(process.returncode)
            raise GraphoraError(
                f"Graphora worker {detail}",
                details={
                    "returncode": process.returncode,
                    "stderr": process.stderr[-4000:],
                },
            )
        return process.stdout

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        environment = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if sys.platform == "win32":
            system_root = os.environ.get("SYSTEMROOT")
            if system_root:
                environment["SYSTEMROOT"] = system_root
        return environment

    @staticmethod
    def _worker_exit_detail(returncode: int) -> str:
        if returncode < 0:
            number = -returncode
            with contextlib.suppress(ValueError):
                return f"terminated by signal {signal.Signals(number).name} ({number})"
            return f"terminated by signal {number}"
        return f"exited with status {returncode}"

    @staticmethod
    def _decode_worker_envelope(payload: str) -> tuple[object | None, dict[str, Any]]:
        try:
            value = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise GraphoraError("Graphora worker returned malformed JSON") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "ok",
            "result",
            "provenance",
            "error",
        }:
            raise GraphoraError("Graphora worker returned an invalid result envelope")
        if value["schema"] != GRAPH_WORKER_RESULT_SCHEMA or not isinstance(value["ok"], bool):
            raise GraphoraError("Graphora worker returned an unsupported result schema")
        if not value["ok"]:
            error = value["error"]
            if (
                not isinstance(error, dict)
                or set(error) != {"code", "message", "exception"}
                or not all(isinstance(error.get(key), str) for key in error)
                or value["result"] is not None
            ):
                raise GraphoraError("Graphora worker returned an invalid failure result")
            provenance = (
                GraphoraAdapter._validate_provider_provenance(value["provenance"])
                if value["provenance"] is not None
                else None
            )
            raise GraphoraError(
                f"Graphora worker failed: {error['message']}",
                details={
                    **cast(dict[str, Any], error),
                    **({"provider_provenance": provenance} if provenance is not None else {}),
                },
            )
        provenance = GraphoraAdapter._validate_provider_provenance(value["provenance"])
        if value["error"] is not None:
            raise GraphoraError("Graphora worker returned a contradictory result")
        return value["result"], provenance

    @staticmethod
    def _validate_provider_provenance(value: object) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "distribution",
            "distribution_version",
            "module_file",
            "direct_url",
            "artifact_sha256",
            "metadata_license",
            "editable",
            "package_code_sha256",
            "artifact_verified",
            "artifact_errors",
        }:
            raise GraphoraError("Graphora worker returned invalid provider provenance")
        digest = value["package_code_sha256"]
        errors = value["artifact_errors"]
        if (
            value["schema"] != "shiftory.graphora-provider-provenance/v1"
            or value["distribution"] != "graphora-kg"
            or value["distribution_version"] != GRAPHORA_VERSION
            or not isinstance(value["module_file"], str)
            or not Path(value["module_file"]).is_absolute()
            or (value["direct_url"] is not None and not isinstance(value["direct_url"], dict))
            or (
                value["artifact_sha256"] is not None
                and (
                    not isinstance(value["artifact_sha256"], str)
                    or value["artifact_sha256"] != GRAPHORA_WHEEL_SHA256
                )
            )
            or value["metadata_license"] != "MIT"
            or not isinstance(value["editable"], bool)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(value["artifact_verified"], bool)
            or not isinstance(errors, list)
            or any(not isinstance(error, str) for error in errors)
            or errors != sorted(set(errors))
            or value["artifact_verified"] != (not errors)
            or (value["editable"] and value["artifact_verified"])
            or (
                value["artifact_verified"]
                and value["package_code_sha256"] != GRAPHORA_PACKAGE_CODE_SHA256
            )
        ):
            raise GraphoraError("Graphora worker returned malformed provider provenance")
        return cast(dict[str, Any], value)

    @staticmethod
    def _graph_result_from_json(value: object) -> GraphResult:
        if not isinstance(value, dict) or set(value) != {
            "status",
            "provider",
            "version",
            "facts",
            "diagnostics",
            "cache_key",
        }:
            raise GraphoraError("Graphora worker returned an invalid graph result")
        if (
            value["status"] != "available"
            or value["provider"] != "graphora"
            or value["version"] != GRAPHORA_VERSION
            or not isinstance(value["facts"], list)
            or not isinstance(value["diagnostics"], list)
            or value["diagnostics"]
            or not isinstance(value["cache_key"], str)
            or len(value["cache_key"]) != 64
            or any(character not in "0123456789abcdef" for character in value["cache_key"])
        ):
            raise GraphoraError("Graphora worker returned invalid graph result metadata")
        facts = tuple(GraphoraAdapter._fact_from_json(fact) for fact in value["facts"])
        expected_order = tuple(
            sorted(
                facts,
                key=lambda fact: (
                    fact.path,
                    fact.line or 0,
                    fact.kind,
                    fact.symbol or "",
                    fact.target or "",
                    fact.id,
                ),
            )
        )
        if facts != expected_order or len({fact.id for fact in facts}) != len(facts):
            raise GraphoraError("Graphora worker returned unordered or duplicate facts")
        return GraphResult(
            "available",
            "graphora",
            GRAPHORA_VERSION,
            facts,
            (),
            value["cache_key"],
        )

    @staticmethod
    def _fact_from_json(value: object) -> GraphFact:
        if not isinstance(value, dict) or set(value) != {
            "id",
            "kind",
            "side",
            "path",
            "line",
            "symbol",
            "target",
            "confidence",
            "provenance",
        }:
            raise GraphoraError("Graphora worker returned an invalid fact")
        if (
            not isinstance(value["id"], str)
            or not value["id"]
            or not isinstance(value["kind"], str)
            or value["kind"]
            not in {
                "definition",
                "changed_symbol",
                "enclosing_symbol",
                "changed_file",
                "unsupported",
                "symbol",
                "caller",
                "callee",
                "static_test",
                "importer",
            }
            or value["side"] not in {"before", "after"}
            or not isinstance(value["path"], str)
            or (value["path"] != "" and _normalized_path(value["path"]) != value["path"])
            or (
                value["line"] is not None
                and (not isinstance(value["line"], int) or isinstance(value["line"], bool))
            )
            or (isinstance(value["line"], int) and value["line"] < 1)
            or (value["symbol"] is not None and not isinstance(value["symbol"], str))
            or (value["target"] is not None and not isinstance(value["target"], str))
            or value["confidence"]
            not in {"extracted", "inferred", "ambiguous", "unresolved", "unavailable"}
            or not isinstance(value["provenance"], str)
            or not value["provenance"]
        ):
            raise GraphoraError("Graphora worker returned invalid fact fields")
        fact = GraphFact(
            value["id"],
            value["kind"],
            value["side"],
            value["path"],
            value["line"],
            value["symbol"],
            value["target"],
            value["confidence"],
            value["provenance"],
        )
        expected = _GraphoraWorkerEngine._fact(
            fact.kind,
            fact.side,
            fact.path,
            fact.line,
            fact.symbol,
            fact.target,
            fact.confidence,
            fact.provenance,
        )
        if fact.id != expected.id:
            raise GraphoraError("Graphora worker returned a fact with an invalid identity")
        return fact


def enrich_with_graphora(
    provider: GraphoraProvider | None,
    mode: GraphoraMode,
    *,
    snapshot: Path | None,
    project: str,
    data_dir: Path,
    patch: str,
    changed_paths: tuple[str, ...],
    side: Side = "after",
    changed_lines: dict[str, tuple[int, ...]] | None = None,
) -> GraphResult:
    if mode == "off":
        return GraphResult("disabled", "graphora", GRAPHORA_VERSION)
    if snapshot is None:
        error = GraphoraError("No source snapshot is available for Graphora")
        if mode == "required":
            raise error
        return GraphResult(
            "unavailable",
            "graphora",
            GRAPHORA_VERSION,
            diagnostics=({"code": error.code, "message": str(error)},),
        )
    try:
        active = provider or GraphoraAdapter()
        result = active.enrich(
            snapshot,
            project=project,
            data_dir=data_dir,
            patch=patch,
            changed_paths=changed_paths,
            side=side,
            changed_lines=changed_lines,
        )
        if mode == "required":
            failure_diagnostics = tuple(
                diagnostic
                for diagnostic in result.diagnostics
                if diagnostic.get("code") != "graphora_provider_provenance"
            )
            incomplete = next(
                (
                    fact
                    for fact in result.facts
                    if fact.kind == "unsupported"
                    or fact.confidence in {"unresolved", "unavailable"}
                ),
                None,
            )
            if result.status != "available" or failure_diagnostics or incomplete is not None:
                detail = (
                    f"{incomplete.kind} fact for "
                    f"{incomplete.path or incomplete.symbol or 'unknown'}"
                    if incomplete is not None
                    else result.status
                )
                raise GraphoraError(f"Required Graphora enrichment is incomplete: {detail}")
        return result
    except Exception as exc:
        if mode == "required":
            if isinstance(exc, GraphoraError):
                raise
            raise GraphoraError(f"Graphora enrichment failed: {exc}") from exc
        return GraphResult(
            "unavailable",
            "graphora",
            GRAPHORA_VERSION,
            diagnostics=(
                {
                    "code": "graphora_unavailable",
                    "message": str(exc),
                    "exception": type(exc).__name__,
                    **(
                        {
                            key: value
                            for key, value in exc.details.items()
                            if key not in {"code", "message", "exception"}
                        }
                        if isinstance(exc, GraphoraError) and exc.details is not None
                        else {}
                    ),
                },
            ),
            cache_key=hashlib.sha256(project.encode()).hexdigest(),
        )
