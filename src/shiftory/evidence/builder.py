"""Evidence construction over Git truth and optional Graphora enrichment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from shiftory import __version__
from shiftory.cache.store import CacheStore
from shiftory.classify.rules import classify_file
from shiftory.diff.identity import stable_id
from shiftory.diff.parser import parse_patch
from shiftory.errors import CoverageError
from shiftory.git.repository import (
    ScopeSpec,
    acquire_patch,
    assert_comparison_consistent,
    resolve_comparison,
    resolve_repository,
)
from shiftory.git.source import materialize_snapshot, source_bytes
from shiftory.graph.provider import GraphoraMode, GraphoraProvider, enrich_with_graphora
from shiftory.models.core import Evidence, FileChange, GraphFact, GraphResult, SourceCitation
from shiftory.models.json import canonical_json


@dataclass(frozen=True, slots=True)
class AnalyzeOptions:
    repo: str | Path = "."
    scope: ScopeSpec = field(default_factory=ScopeSpec)
    graphora: GraphoraMode = "auto"
    max_evidence_bytes: int = 1_000_000
    context_lines: int = 3
    cache_dir: Path | None = None
    no_cache: bool = False


@dataclass(frozen=True, slots=True)
class _CachedSource:
    content: bytes | None
    lines: tuple[bytes, ...]


@dataclass(slots=True)
class _AnalysisSourceCache:
    comparison: Any
    entries: dict[tuple[str, str, str, str], _CachedSource] = field(default_factory=dict)

    def read(self, path: str, side: str) -> _CachedSource:
        revision, source_fingerprint = self._source_identity(side)
        key = (side, revision, path, source_fingerprint)
        cached = self.entries.get(key)
        if cached is not None:
            return cached
        content = source_bytes(self.comparison, path, side)
        cached = _CachedSource(
            content,
            tuple(_exact_source_lines(content)) if content is not None else (),
        )
        self.entries[key] = cached
        return cached

    def _source_identity(self, side: str) -> tuple[str, str]:
        comparison = self.comparison
        if side == "before":
            if comparison.mode == "unstaged":
                return "index", comparison.after_fingerprint or comparison.identity
            revision = comparison.base_sha or "none"
            return revision, revision
        if side != "after":
            raise ValueError(f"Unknown source side: {side}")
        if comparison.head_sha:
            return comparison.head_sha, comparison.head_sha
        revision = "index" if comparison.mode == "staged" else "working-tree"
        return revision, comparison.after_fingerprint or comparison.identity


def analyze(
    options: AnalyzeOptions | None = None,
    *,
    provider: GraphoraProvider | None = None,
) -> Evidence:
    options = options or AnalyzeOptions()
    evidence = analyze_complete(options, provider=provider)
    return _apply_evidence_budget(evidence, options.max_evidence_bytes)


def analyze_complete(
    options: AnalyzeOptions | None = None,
    *,
    provider: GraphoraProvider | None = None,
) -> Evidence:
    """Build the complete private ledger before agent-payload budgeting."""
    options = options or AnalyzeOptions()
    root = resolve_repository(options.repo)
    comparison = resolve_comparison(root, options.scope)
    patch = acquire_patch(comparison, context_lines=options.context_lines)
    return build_complete_evidence(comparison, patch, options=options, provider=provider)


def build_evidence(
    comparison: Any,
    patch: bytes,
    *,
    options: AnalyzeOptions,
    provider: GraphoraProvider | None = None,
) -> Evidence:
    evidence = build_complete_evidence(comparison, patch, options=options, provider=provider)
    return _apply_evidence_budget(evidence, options.max_evidence_bytes)


def build_complete_evidence(
    comparison: Any,
    patch: bytes,
    *,
    options: AnalyzeOptions,
    provider: GraphoraProvider | None = None,
) -> Evidence:
    assert_comparison_consistent(comparison, operation="evidence construction")
    parsed = tuple(_normalize_non_text_changes(file) for file in parse_patch(patch))
    source_cache = _AnalysisSourceCache(comparison)
    _validate_source_ranges(comparison, parsed, source_cache)
    files = tuple(
        _with_classification_and_citations(comparison, file, source_cache) for file in parsed
    )
    _validate_hierarchy(files)
    cache = CacheStore(
        comparison.repository_id,
        cache_root=options.cache_dir,
        enabled=not options.no_cache,
    )
    before_snapshot: Path | None = None
    after_snapshot: Path | None = None
    before_key = "disabled"
    after_key = "disabled"
    if options.graphora != "off":
        before_snapshot, before_key = materialize_snapshot(comparison, cache, "before")
        after_snapshot, after_key = materialize_snapshot(comparison, cache, "after")
    changed_paths = {
        side: tuple(
            sorted(
                path
                for file in files
                for path in (file.old_path if side == "before" else file.new_path,)
                if path is not None
            )
        )
        for side in ("before", "after")
    }
    changed_lines = {
        side: {
            path: tuple(
                coordinate
                for hunk in file.hunks
                for line in hunk.lines
                if line.side == side
                for coordinate in (line.old_line if side == "before" else line.new_line,)
                if coordinate is not None
            )
            for file in files
            for path in (file.old_path if side == "before" else file.new_path,)
            if path is not None
        }
        for side in ("before", "after")
    }
    with cache.lock():
        before_graph = enrich_with_graphora(
            provider,
            options.graphora,
            snapshot=before_snapshot,
            project=f"shiftory-{before_key[:24]}",
            data_dir=cache.root / "graphora" / before_key,
            patch=patch.decode("utf-8", "replace"),
            changed_paths=changed_paths["before"],
            side="before",
            changed_lines=changed_lines["before"],
        )
        after_graph = enrich_with_graphora(
            provider,
            options.graphora,
            snapshot=after_snapshot,
            project=f"shiftory-{after_key[:24]}",
            data_dir=cache.root / "graphora" / after_key,
            patch=patch.decode("utf-8", "replace"),
            changed_paths=changed_paths["after"],
            side="after",
            changed_lines=changed_lines["after"],
        )
    graph = _merge_graph_results(before_graph, after_graph)
    groups = _groups(files)
    diagnostics: list[dict[str, Any]] = [*graph.diagnostics]
    if not files:
        diagnostics.append(
            {
                "code": "no_changes",
                "message": "The Git comparison contains no changed files or change units.",
            }
        )
    metrics = _metrics(
        files,
        patch,
        graph_fact_count=len(graph.facts),
        unresolved_graph_fact_count=sum(
            fact.confidence in {"unresolved", "unavailable"} or fact.kind == "unsupported"
            for fact in graph.facts
        ),
        group_count=len(groups),
    )
    evidence = Evidence(
        "shiftory.evidence/v1",
        __version__,
        comparison.portable(),
        {"id": comparison.repository_id},
        files,
        graph,
        groups,
        tuple(diagnostics),
        (),
        metrics,
    )
    result = _set_evidence_bytes(evidence)
    assert_comparison_consistent(comparison, operation="evidence construction")
    return result


def apply_evidence_budget(evidence: Evidence, requested_bytes: int) -> Evidence:
    """Apply the public evidence/v1 citation-omission budget."""
    return _apply_evidence_budget(evidence, requested_bytes)


def _normalize_non_text_changes(file: FileChange) -> FileChange:
    if not any(unit.kind == "submodule" for unit in file.units):
        return file
    return replace(
        file,
        units=tuple(unit for unit in file.units if unit.kind != "text"),
        hunks=(),
        spans=(),
    )


def _with_classification_and_citations(
    comparison: Any,
    file: FileChange,
    source_cache: _AnalysisSourceCache,
) -> FileChange:
    category, confidence = classify_file(file)
    citations: list[SourceCitation] = []
    for span in file.spans:
        citation_path = file.old_path if span.side == "before" else file.new_path
        if citation_path is None:
            raise CoverageError(f"Changed span {span.id} has no {span.side} source path")
        source = source_cache.read(citation_path, span.side)
        if source.content is None:
            assert_comparison_consistent(comparison, operation="source citation acquisition")
            raise CoverageError(
                f"Changed span {span.id} has no readable {span.side} source snapshot",
                details={"path": citation_path, "side": span.side},
            )
        raw_text = b"\n".join(source.lines[span.start_line - 1 : span.end_line])
        text = raw_text.decode("utf-8", "backslashreplace")
        citations.append(
            SourceCitation(
                stable_id(
                    "source",
                    {
                        "path": citation_path,
                        "side": span.side,
                        "start": span.start_line,
                        "end": span.end_line,
                        "lines": list(span.line_ids),
                    },
                ),
                citation_path,
                span.side,
                span.start_line,
                span.end_line,
                text,
                hashlib.sha256(raw_text).hexdigest(),
            )
        )
    return replace(
        file,
        citations=tuple(citations),
        classification=category,
        classification_confidence=confidence,
    )


def _groups(files: tuple[FileChange, ...]) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, list[str]] = {}
    for file in files:
        for unit in file.units:
            classification = (
                unit.kind
                if unit.kind in {"binary", "mode", "rename", "unsupported"}
                else "structural"
                if unit.kind in {"copy", "submodule"}
                else file.classification
            )
            grouped.setdefault(classification, []).append(unit.id)
    return tuple(
        {
            "id": stable_id("group", {"classification": key}),
            "classification": key,
            "unit_ids": sorted(ids),
        }
        for key, ids in sorted(grouped.items())
    )


def _merge_graph_results(before: GraphResult, after: GraphResult) -> GraphResult:
    status: Literal["available", "unavailable", "disabled"] = (
        "disabled"
        if before.status == after.status == "disabled"
        else "available"
        if before.status == after.status == "available"
        else "unavailable"
    )
    facts: dict[str, GraphFact] = {}
    for fact in (*before.facts, *after.facts):
        previous = facts.get(fact.id)
        if previous is not None and previous != fact:
            raise CoverageError(
                f"Graph fact identity collision detected for {fact.id}",
                details={"fact_id": fact.id},
            )
        facts[fact.id] = fact
    diagnostics = [
        {**diagnostic, "side": side}
        for side, result in (("before", before), ("after", after))
        for diagnostic in result.diagnostics
    ]
    for side, result in (("before", before), ("after", after)):
        if result.status == "unavailable" and not result.diagnostics:
            diagnostics.append(
                {
                    "code": "graphora_unavailable",
                    "message": f"Graph enrichment is unavailable for the {side} snapshot.",
                    "side": side,
                }
            )
    diagnostics.sort(key=canonical_json)
    cache_key = (
        None
        if before.cache_key is None and after.cache_key is None
        else f"{before.cache_key or 'none'}:{after.cache_key or 'none'}"
    )
    return GraphResult(
        status,
        (
            after.provider
            if before.provider == after.provider
            else f"{before.provider}+{after.provider}"
        ),
        after.version if before.version == after.version else None,
        tuple(
            sorted(
                facts.values(),
                key=lambda fact: (
                    0 if fact.side == "before" else 1,
                    fact.path,
                    fact.line or 0,
                    fact.kind,
                    fact.id,
                ),
            )
        ),
        tuple(diagnostics),
        cache_key,
    )


def _metrics(
    files: tuple[FileChange, ...],
    patch: bytes,
    *,
    graph_fact_count: int,
    unresolved_graph_fact_count: int,
    group_count: int,
) -> dict[str, int | float]:
    units = [unit for file in files for unit in file.units]
    hunks = [hunk for file in files for hunk in file.hunks]
    spans = [span for file in files for span in file.spans]
    lines = [line for hunk in hunks for line in hunk.lines]
    citations = [citation for file in files for citation in file.citations]
    return {
        "files": len(files),
        "units": len(units),
        "hunks": len(hunks),
        "spans": len(spans),
        "added_lines": sum(line.side == "after" for line in lines),
        "deleted_lines": sum(line.side == "before" for line in lines),
        "changed_lines": len(lines),
        "added_files": sum(file.status == "added" for file in files),
        "deleted_files": sum(file.status == "deleted" for file in files),
        "renamed_files": sum(file.status == "renamed" for file in files),
        "binary_units": sum(unit.kind == "binary" for unit in units),
        "unsupported_units": sum(unit.kind == "unsupported" for unit in units),
        "source_citations": len(citations),
        "omitted_source_contexts": sum(citation.omitted for citation in citations),
        "raw_patch_bytes": len(patch),
        "graph_facts": graph_fact_count,
        "unresolved_graph_facts": unresolved_graph_fact_count,
        "classification_groups": group_count,
        "line_coverage_ratio": 1.0,
        "hunk_coverage_ratio": 1.0,
        "unit_coverage_ratio": 1.0,
        "evidence_bytes": 0,
    }


_BUDGET_DIAGNOSTIC_CODES = {"evidence_budget_exceeded", "evidence_context_omitted"}


def _set_evidence_bytes(evidence: Evidence) -> Evidence:
    diagnostics = tuple(
        {**item, "actual_bytes": 0} if item.get("code") in _BUDGET_DIAGNOSTIC_CODES else item
        for item in evidence.diagnostics
    )
    zeroed = replace(
        evidence,
        diagnostics=diagnostics,
        metrics={**evidence.metrics, "evidence_bytes": 0},
    )
    base_size = len(canonical_json(zeroed.to_dict()).encode("utf-8"))
    dynamic_values = 1 + sum(item.get("code") in _BUDGET_DIAGNOSTIC_CODES for item in diagnostics)
    actual = base_size
    while True:
        adjusted = base_size + dynamic_values * (len(str(actual)) - 1)
        if adjusted == actual:
            break
        actual = adjusted
    finalized = replace(
        zeroed,
        diagnostics=tuple(
            {**item, "actual_bytes": actual}
            if item.get("code") in _BUDGET_DIAGNOSTIC_CODES
            else item
            for item in diagnostics
        ),
        metrics={**zeroed.metrics, "evidence_bytes": actual},
    )
    measured = len(canonical_json(finalized.to_dict()).encode("utf-8"))
    if measured != actual:
        raise AssertionError("Evidence byte accounting did not converge")
    return finalized


def _retrieval(citation: SourceCitation) -> dict[str, Any]:
    return {
        "kind": "git_source_range",
        "path": citation.path,
        "side": citation.side,
        "start_line": citation.start_line,
        "end_line": citation.end_line,
        "content_hash": citation.content_hash,
    }


def _omitted_citation(
    citation: SourceCitation,
) -> tuple[SourceCitation, dict[str, Any]]:
    omitted = replace(
        citation,
        text=None,
        omitted=True,
        retrieval=_retrieval(citation),
    )
    omission = {
        "id": stable_id("omission", {"citation_id": omitted.id}),
        "kind": "source_context",
        "citation_id": omitted.id,
        "retrieval": omitted.retrieval,
    }
    return omitted, omission


def _omit_citations(evidence: Evidence, citation_ids: frozenset[str]) -> Evidence:
    omissions = list(evidence.omissions)
    files: list[FileChange] = []
    for file in evidence.files:
        citations: list[SourceCitation] = []
        for citation in file.citations:
            if citation.id in citation_ids:
                omitted, omission = _omitted_citation(citation)
                citations.append(omitted)
                omissions.append(omission)
            else:
                citations.append(citation)
        files.append(replace(file, citations=tuple(citations)))
    metrics = {
        **evidence.metrics,
        "omitted_source_contexts": int(evidence.metrics["omitted_source_contexts"])
        + len(citation_ids),
    }
    return replace(
        evidence,
        files=tuple(files),
        omissions=tuple(
            sorted(
                omissions,
                key=lambda item: str(item["citation_id"]),
            )
        ),
        metrics=metrics,
    )


def _with_budget_diagnostic(
    evidence: Evidence,
    *,
    requested_bytes: int,
    exceeded: bool,
) -> Evidence:
    code = "evidence_budget_exceeded" if exceeded else "evidence_context_omitted"
    message = (
        "The mandatory evidence ledger exceeds the requested budget; identities and "
        "ownership inputs were retained."
        if exceeded
        else (
            "Source context was omitted to satisfy the evidence budget; "
            "retrieval data was retained."
        )
    )
    diagnostics = tuple(
        diagnostic
        for diagnostic in evidence.diagnostics
        if diagnostic.get("code") not in _BUDGET_DIAGNOSTIC_CODES
    )
    return replace(
        evidence,
        diagnostics=(
            *diagnostics,
            {
                "code": code,
                "message": message,
                "requested_bytes": requested_bytes,
                "actual_bytes": 0,
                "omitted_contexts": len(evidence.omissions),
            },
        ),
    )


@dataclass(frozen=True, slots=True)
class _OmissionCandidate:
    citation_id: str
    contribution_bytes: int


def _component_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _omission_candidates(evidence: Evidence) -> tuple[_OmissionCandidate, ...]:
    candidates: list[_OmissionCandidate] = []
    for file in evidence.files:
        for citation in file.citations:
            if citation.text is None:
                continue
            omitted, omission = _omitted_citation(citation)
            contribution = (
                _component_size(asdict(citation))
                - _component_size(asdict(omitted))
                - _component_size(omission)
                - 1
            )
            if contribution > 0:
                candidates.append(_OmissionCandidate(citation.id, contribution))
    return tuple(sorted(candidates, key=lambda item: (-item.contribution_bytes, item.citation_id)))


def _diagnostic_size_increase(
    evidence: Evidence,
    *,
    omitted_contexts: int,
    actual_bytes: int,
    requested_bytes: int,
) -> int:
    diagnostic = {
        "code": "evidence_context_omitted",
        "message": (
            "Source context was omitted to satisfy the evidence budget; "
            "retrieval data was retained."
        ),
        "requested_bytes": requested_bytes,
        "actual_bytes": actual_bytes,
        "omitted_contexts": omitted_contexts,
    }
    return _component_size(diagnostic) + (1 if evidence.diagnostics else 0)


def _apply_evidence_budget(evidence: Evidence, requested_bytes: int) -> Evidence:
    if requested_bytes < 0:
        raise ValueError("max_evidence_bytes must be non-negative")
    evidence = _set_evidence_bytes(evidence)
    if int(evidence.metrics["evidence_bytes"]) <= requested_bytes:
        return evidence
    candidates = _omission_candidates(evidence)
    all_ids = frozenset(candidate.citation_id for candidate in candidates)
    mandatory_floor = _set_evidence_bytes(
        _with_budget_diagnostic(
            _omit_citations(evidence, all_ids),
            requested_bytes=requested_bytes,
            exceeded=True,
        )
    )
    if int(mandatory_floor.metrics["evidence_bytes"]) > requested_bytes:
        return mandatory_floor

    original_size = int(evidence.metrics["evidence_bytes"])
    base_omitted = int(evidence.metrics["omitted_source_contexts"])
    cumulative_savings = 0
    selected_count = len(candidates)
    for index, candidate in enumerate(candidates, 1):
        cumulative_savings += candidate.contribution_bytes
        omitted_count = base_omitted + index
        estimated_size = (
            original_size
            - cumulative_savings
            - (1 if index and not evidence.omissions else 0)
            + _diagnostic_size_increase(
                evidence,
                omitted_contexts=omitted_count,
                actual_bytes=original_size,
                requested_bytes=requested_bytes,
            )
            + len(str(omitted_count))
            - len(str(base_omitted))
        )
        if estimated_size <= requested_bytes:
            selected_count = index
            break

    selected_ids = frozenset(candidate.citation_id for candidate in candidates[:selected_count])
    selected = _set_evidence_bytes(
        _with_budget_diagnostic(
            _omit_citations(evidence, selected_ids),
            requested_bytes=requested_bytes,
            exceeded=False,
        )
    )
    if int(selected.metrics["evidence_bytes"]) <= requested_bytes:
        return selected
    return _set_evidence_bytes(
        _with_budget_diagnostic(
            _omit_citations(evidence, all_ids),
            requested_bytes=requested_bytes,
            exceeded=False,
        )
    )


def _validate_hierarchy(files: tuple[FileChange, ...]) -> None:
    identities: set[str] = set()
    for file in files:
        units = {unit.id: unit for unit in file.units}
        hunks = {hunk.id: hunk for hunk in file.hunks}
        spans = {span.id: span for span in file.spans}
        lines = {line.id: line for hunk in file.hunks for line in hunk.lines}
        if len(units) != len(file.units) or not units:
            raise CoverageError("Every changed file must contain uniquely identified change units")
        if any(set(unit.hunk_ids) - hunks.keys() for unit in file.units):
            raise CoverageError("A change unit references an unknown hunk")
        if any(set(hunk.span_ids) - spans.keys() for hunk in file.hunks):
            raise CoverageError("A hunk references an unknown span")
        if any(set(span.line_ids) - lines.keys() for span in file.spans):
            raise CoverageError("A span references an unknown changed line")
        owned_hunks = [
            hunk_id for unit in file.units if unit.kind == "text" for hunk_id in unit.hunk_ids
        ]
        owned_spans = [span_id for hunk in file.hunks for span_id in hunk.span_ids]
        owned_lines = [line_id for span in file.spans for line_id in span.line_ids]
        if len(owned_hunks) != len(hunks) or set(owned_hunks) != hunks.keys():
            raise CoverageError("Every hunk must belong to exactly one text unit")
        if len(owned_spans) != len(spans) or set(owned_spans) != spans.keys():
            raise CoverageError("Every span must belong to exactly one hunk")
        if len(owned_lines) != len(lines) or set(owned_lines) != lines.keys():
            raise CoverageError("Every changed line must belong to exactly one span")
        current = {*units, *hunks, *spans, *lines}
        if len(current) != len(units) + len(hunks) + len(spans) + len(lines):
            raise CoverageError("Evidence hierarchy identities are not unique")
        if identities & current:
            raise CoverageError("Evidence hierarchy identity collision detected")
        identities.update(current)


def _validate_source_ranges(
    comparison: Any,
    files: tuple[FileChange, ...],
    source_cache: _AnalysisSourceCache,
) -> None:
    for file in files:
        sources = {
            "before": (source_cache.read(file.old_path, "before").lines if file.old_path else ()),
            "after": (source_cache.read(file.new_path, "after").lines if file.new_path else ()),
        }
        for span in file.spans:
            if span.end_line > len(sources[span.side]):
                raise CoverageError(
                    f"Changed span {span.id} exceeds the {span.side} source snapshot",
                    details={
                        "path": file.old_path if span.side == "before" else file.new_path,
                        "end_line": span.end_line,
                        "source_lines": len(sources[span.side]),
                    },
                )
        for hunk in file.hunks:
            for line in hunk.lines:
                coordinate = line.old_line if line.side == "before" else line.new_line
                assert coordinate is not None
                actual = sources[line.side][coordinate - 1]
                if hashlib.sha256(actual).hexdigest() != line.content_hash:
                    raise CoverageError(
                        f"Changed line {line.id} does not match the {line.side} source snapshot",
                        details={
                            "path": (file.old_path if line.side == "before" else file.new_path),
                            "line": coordinate,
                        },
                    )


def _exact_source_lines(content: bytes) -> list[bytes]:
    lines = content.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines
