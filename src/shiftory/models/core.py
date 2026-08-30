"""Shiftory-owned data models.

The models deliberately contain no Graphora implementation classes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

Side = Literal["before", "after"]
Confidence = Literal["extracted", "inferred", "ambiguous", "unresolved", "unavailable"]


@dataclass(frozen=True, slots=True)
class Comparison:
    repository_root: Path
    repository_id: str
    mode: str
    base_sha: str | None
    head_sha: str | None
    base_label: str
    head_label: str
    identity: str
    after_fingerprint: str | None = None
    parent: int | None = None
    paths: tuple[str, ...] = ()

    def portable(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("repository_root")
        if not self.paths:
            value.pop("paths")
        return value


@dataclass(frozen=True, slots=True)
class ChangedLine:
    id: str
    side: Side
    old_line: int | None
    new_line: int | None
    ordinal: int
    content: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ChangeSpan:
    id: str
    side: Side
    start_line: int
    end_line: int
    line_ids: tuple[str, ...]
    replacement_span_id: str | None = None


@dataclass(frozen=True, slots=True)
class TextHunk:
    id: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str
    span_ids: tuple[str, ...]
    lines: tuple[ChangedLine, ...]
    raw_patch_bytes: int


@dataclass(frozen=True, slots=True)
class ChangeUnit:
    id: str
    kind: Literal["text", "binary", "mode", "rename", "copy", "submodule", "unsupported"]
    hunk_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceCitation:
    id: str
    path: str
    side: Side
    start_line: int
    end_line: int
    text: str | None
    content_hash: str
    omitted: bool = False
    retrieval: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FileChange:
    old_path: str | None
    new_path: str | None
    status: str
    old_blob: str | None
    new_blob: str | None
    old_mode: str | None
    new_mode: str | None
    units: tuple[ChangeUnit, ...]
    hunks: tuple[TextHunk, ...]
    spans: tuple[ChangeSpan, ...]
    citations: tuple[SourceCitation, ...] = ()
    classification: str = "unresolved"
    classification_confidence: Confidence = "unresolved"


@dataclass(frozen=True, slots=True)
class GraphFact:
    id: str
    kind: str
    side: Side
    path: str
    line: int | None
    symbol: str | None
    target: str | None
    confidence: Confidence
    provenance: str


@dataclass(frozen=True, slots=True)
class GraphResult:
    status: Literal["available", "unavailable", "disabled"]
    provider: str
    version: str | None
    facts: tuple[GraphFact, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    cache_key: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    schema: str
    tool_version: str
    comparison: dict[str, Any]
    repository: dict[str, Any]
    files: tuple[FileChange, ...]
    graph: GraphResult
    groups: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    omissions: tuple[dict[str, Any], ...]
    metrics: dict[str, int | float]

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_value(asdict(self)))


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value
