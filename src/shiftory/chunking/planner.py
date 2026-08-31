"""Strict, deterministic chunk planning over a complete evidence ledger."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from itertools import chain
from typing import Any, Literal, cast

from shiftory.diff.identity import stable_id
from shiftory.errors import ChunkBudgetError, CoverageError
from shiftory.models.json import canonical_json

TOKEN_ESTIMATE_BYTES = 4
TOKEN_ESTIMATE_FORMULA = "ceil(canonical_utf8_bytes/4)"


def estimate_tokens(byte_count: int) -> int:
    if byte_count < 0:
        raise ValueError("byte_count must be non-negative")
    return (byte_count + TOKEN_ESTIMATE_BYTES - 1) // TOKEN_ESTIMATE_BYTES


def canonical_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))


def _component_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentBudget:
    max_bytes: int
    max_estimated_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        if self.max_estimated_tokens is not None and self.max_estimated_tokens < 0:
            raise ValueError("max_estimated_tokens must be non-negative")

    @property
    def effective_max_bytes(self) -> int:
        if self.max_estimated_tokens is None:
            return self.max_bytes
        return min(self.max_bytes, self.max_estimated_tokens * TOKEN_ESTIMATE_BYTES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_bytes": self.max_bytes,
            "requested_estimated_tokens": self.max_estimated_tokens,
            "effective_max_bytes": self.effective_max_bytes,
            "token_estimate_formula": TOKEN_ESTIMATE_FORMULA,
        }


@dataclass(frozen=True, slots=True)
class PlannedChunks:
    plan: dict[str, Any]
    chunks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _Atom:
    id: str
    classification: str
    path: str
    order: tuple[Any, ...]
    value: dict[str, Any]


class _DisjointSet:
    def __init__(self, values: set[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        root = value
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[value] != value:
            parent = self._parent[value]
            self._parent[value] = root
            value = parent
        return root

    def union(self, left: str, right: str) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        first, second = sorted((left_root, right_root))
        self._parent[second] = first
        return True


def chunk_identity(chunk: dict[str, Any]) -> str:
    semantic = deepcopy(chunk)
    semantic.pop("id", None)
    budget = semantic.get("budget")
    if isinstance(budget, dict):
        budget.pop("actual_bytes", None)
        budget.pop("estimated_tokens", None)
    return stable_id("chunk", semantic)


def plan_identity(plan: dict[str, Any]) -> str:
    semantic = deepcopy(plan)
    semantic.pop("id", None)
    return stable_id("chunk-plan", semantic)


def source_range_identity(record: dict[str, Any]) -> str:
    return stable_id(
        "source-range",
        {
            key: record.get(key)
            for key in (
                "citation_id",
                "path",
                "side",
                "start_line",
                "end_line",
                "content_hash",
            )
        },
    )


def plan_chunks(evidence: dict[str, Any], budget: AgentBudget) -> PlannedChunks:
    comparison = evidence.get("comparison")
    if not isinstance(comparison, dict) or not isinstance(comparison.get("identity"), str):
        raise CoverageError("Complete evidence has no comparison identity")
    comparison_identity = cast(str, comparison["identity"])
    ledger_sha256 = sha256_json(evidence)
    retrievals: dict[str, dict[str, Any]] = {}
    citation_text: dict[str, str] = {}
    atoms = _atoms(
        evidence,
        comparison_identity=comparison_identity,
        ledger_sha256=ledger_sha256,
        budget=budget,
        retrievals=retrievals,
        citation_text=citation_text,
    )
    if not atoms:
        raise ChunkBudgetError(
            "The complete empty-comparison evidence cannot fit the effective agent budget",
            details={
                "required_bytes": canonical_size(evidence),
                "effective_max_bytes": budget.effective_max_bytes,
            },
        )
    strategy, component_by_path = _graph_components(evidence, {atom.path for atom in atoms})
    ordered = tuple(
        sorted(
            atoms,
            key=lambda atom: (
                component_by_path.get(atom.path, atom.path),
                atom.classification,
                *atom.order,
                atom.id,
            ),
        )
    )
    chunks = _pack(
        ordered,
        evidence=evidence,
        comparison_identity=comparison_identity,
        ledger_sha256=ledger_sha256,
        strategy=strategy,
        budget=budget,
        citation_text=citation_text,
    )
    used_retrievals = {
        range_id
        for chunk in chunks
        for item in chunk["work_items"]
        for context in item["contexts"]
        for range_id in context["retrieval_range_ids"]
    }
    plan = {
        "schema": "shiftory.chunk-plan/v1",
        "id": "",
        "comparison_identity": comparison_identity,
        "ledger_sha256": ledger_sha256,
        "grouping_strategy": strategy,
        "budget": budget.to_dict(),
        "chunks": [
            {
                "id": chunk["id"],
                "index": chunk["index"],
                "payload_sha256": sha256_json(chunk),
                "ownership_target_ids": sorted(
                    target["evidence_id"]
                    for item in chunk["work_items"]
                    for target in item["ownership_targets"]
                ),
                "retrieval_range_ids": sorted(
                    range_id
                    for item in chunk["work_items"]
                    for context in item["contexts"]
                    for range_id in context["retrieval_range_ids"]
                ),
            }
            for chunk in chunks
        ],
        "retrieval_ranges": [retrievals[range_id] for range_id in sorted(used_retrievals)],
        "coverage": {
            "span_targets": sum(
                target["kind"] == "span"
                for chunk in chunks
                for item in chunk["work_items"]
                for target in item["ownership_targets"]
            ),
            "non_text_unit_targets": sum(
                target["kind"] == "non_text_unit"
                for chunk in chunks
                for item in chunk["work_items"]
                for target in item["ownership_targets"]
            ),
        },
    }
    plan["id"] = plan_identity(plan)
    return PlannedChunks(plan, chunks)


def _atoms(
    evidence: dict[str, Any],
    *,
    comparison_identity: str,
    ledger_sha256: str,
    budget: AgentBudget,
    retrievals: dict[str, dict[str, Any]],
    citation_text: dict[str, str],
) -> tuple[_Atom, ...]:
    group_by_unit = {
        unit_id: group["classification"]
        for group in evidence.get("groups", [])
        for unit_id in group.get("unit_ids", [])
        if isinstance(group, dict) and isinstance(unit_id, str)
    }
    atoms: list[_Atom] = []
    for file_index, file in enumerate(evidence.get("files", [])):
        old_path = file["old_path"]
        new_path = file["new_path"]
        path = new_path or old_path
        if not isinstance(path, str):
            raise CoverageError("A changed file has no path during chunk planning")
        hunks = {hunk["id"]: hunk for hunk in file["hunks"]}
        spans = {span["id"]: span for span in file["spans"]}
        citations = {
            (citation["side"], citation["start_line"], citation["end_line"]): citation
            for citation in file["citations"]
        }
        visited: set[str] = set()
        for unit_index, unit in enumerate(file["units"]):
            classification = str(group_by_unit.get(unit["id"], file["classification"]))
            if unit["kind"] != "text":
                value = _non_text_atom(file, path, unit, classification)
                atoms.append(
                    _Atom(
                        value["id"],
                        classification,
                        path,
                        (path, file_index, unit_index, -1, -1),
                        value,
                    )
                )
                continue
            for hunk_index, hunk_id in enumerate(unit["hunk_ids"]):
                hunk = hunks[hunk_id]
                hunk_span_ids = set(hunk["span_ids"])
                for span_index, span_id in enumerate(hunk["span_ids"]):
                    if span_id in visited:
                        continue
                    span = spans[span_id]
                    paired_id = span.get("replacement_span_id")
                    paired = (
                        spans.get(paired_id)
                        if isinstance(paired_id, str) and paired_id in hunk_span_ids
                        else None
                    )
                    selected = [span]
                    if paired is not None and paired["id"] not in visited:
                        selected.append(paired)
                    selected.sort(
                        key=lambda value: (
                            0 if value["side"] == "before" else 1,
                            value["start_line"],
                            value["id"],
                        )
                    )
                    visited.update(value["id"] for value in selected)
                    contexts = []
                    for selected_span in selected:
                        key = (
                            selected_span["side"],
                            selected_span["start_line"],
                            selected_span["end_line"],
                        )
                        citation = citations.get(key)
                        if citation is None:
                            raise CoverageError(
                                f"Span {selected_span['id']} has no exact source citation"
                            )
                        text = citation.get("text")
                        if not isinstance(text, str):
                            raise CoverageError(
                                f"Complete citation {citation['id']} has no source text"
                            )
                        citation_text[citation["id"]] = text
                        range_ids = _retrieval_ranges(
                            citation,
                            text=text,
                            comparison_identity=comparison_identity,
                            ledger_sha256=ledger_sha256,
                            budget=budget,
                            retrievals=retrievals,
                        )
                        retrieval_context = {
                            "citation_id": citation["id"],
                            "path": citation["path"],
                            "side": citation["side"],
                            "start_line": citation["start_line"],
                            "end_line": citation["end_line"],
                            "content_hash": citation["content_hash"],
                            "text": None,
                            "retrieval_range_ids": list(range_ids),
                        }
                        inline_context = {
                            **retrieval_context,
                            "text": text,
                            "retrieval_range_ids": [],
                        }
                        contexts.append(
                            inline_context
                            if _component_size(inline_context) <= _component_size(retrieval_context)
                            else retrieval_context
                        )
                    value = {
                        "id": stable_id(
                            "chunk-atom",
                            {"ownership_targets": sorted(item["id"] for item in selected)},
                        ),
                        "classification": classification,
                        "file": {
                            "old_path": old_path,
                            "new_path": new_path,
                            "status": file["status"],
                            "classification": file["classification"],
                            "classification_confidence": file["classification_confidence"],
                        },
                        "unit": {
                            "id": unit["id"],
                            "kind": unit["kind"],
                            "metadata": unit["metadata"],
                        },
                        "hunk": {
                            key: hunk[key]
                            for key in (
                                "id",
                                "old_start",
                                "old_count",
                                "new_start",
                                "new_count",
                                "heading",
                            )
                        },
                        "ownership_targets": [
                            {
                                "evidence_id": selected_span["id"],
                                "kind": "span",
                                "side": selected_span["side"],
                                "start_line": selected_span["start_line"],
                                "end_line": selected_span["end_line"],
                                "changed_line_count": len(selected_span["line_ids"]),
                            }
                            for selected_span in selected
                        ],
                        "contexts": contexts,
                    }
                    atoms.append(
                        _Atom(
                            value["id"],
                            classification,
                            path,
                            (
                                path,
                                file_index,
                                unit_index,
                                hunk_index,
                                span_index,
                            ),
                            value,
                        )
                    )
    return tuple(atoms)


def _non_text_atom(
    file: dict[str, Any],
    path: str,
    unit: dict[str, Any],
    classification: str,
) -> dict[str, Any]:
    return {
        "id": stable_id("chunk-atom", {"ownership_targets": [unit["id"]]}),
        "classification": classification,
        "file": {
            "old_path": file["old_path"],
            "new_path": file["new_path"],
            "status": file["status"],
            "classification": file["classification"],
            "classification_confidence": file["classification_confidence"],
        },
        "unit": {
            "id": unit["id"],
            "kind": unit["kind"],
            "metadata": unit["metadata"],
        },
        "hunk": None,
        "ownership_targets": [
            {
                "evidence_id": unit["id"],
                "kind": "non_text_unit",
                "side": None,
                "start_line": None,
                "end_line": None,
                "changed_line_count": 0,
            }
        ],
        "contexts": [],
    }


def _retrieval_ranges(
    citation: dict[str, Any],
    *,
    text: str,
    comparison_identity: str,
    ledger_sha256: str,
    budget: AgentBudget,
    retrievals: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    lines = text.split("\n")
    json_content_prefix = [0]
    for line in lines:
        encoded = json.dumps(line, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        json_content_prefix.append(json_content_prefix[-1] + len(encoded) - 2)
    selected: list[str] = []
    citation_start = int(citation["start_line"])
    offset = 0
    while offset < len(lines):
        low, high = offset + 1, len(lines)
        best_end = offset
        smallest_response_bytes = 0
        while low <= high:
            end = (low + high) // 2
            line_count = end - offset
            text_content_bytes = (
                json_content_prefix[end] - json_content_prefix[offset] + 2 * (line_count - 1)
            )
            response_bytes = _retrieval_response_size(
                citation,
                start_line=citation_start + offset,
                end_line=citation_start + end - 1,
                text_content_bytes=text_content_bytes,
                comparison_identity=comparison_identity,
                ledger_sha256=ledger_sha256,
            )
            if line_count == 1:
                smallest_response_bytes = response_bytes
            if response_bytes <= budget.effective_max_bytes:
                best_end = end
                low = end + 1
            else:
                high = end - 1
        if best_end == offset:
            raise ChunkBudgetError(
                "A single recorded source line cannot fit the effective agent budget",
                details={
                    "citation_id": citation["id"],
                    "line": citation_start + offset,
                    "required_bytes": smallest_response_bytes,
                    "effective_max_bytes": budget.effective_max_bytes,
                },
            )
        best_record, response = _retrieval_record(
            citation,
            start_line=citation_start + offset,
            end_line=citation_start + best_end - 1,
            text="\n".join(lines[offset:best_end]),
            comparison_identity=comparison_identity,
            ledger_sha256=ledger_sha256,
        )
        if int(response["actual_bytes"]) > budget.effective_max_bytes:
            raise AssertionError("Retrieval byte accounting disagrees with range planning")
        retrievals[best_record["id"]] = best_record
        selected.append(best_record["id"])
        offset = best_end
    return tuple(selected)


def _retrieval_response_size(
    citation: dict[str, Any],
    *,
    start_line: int,
    end_line: int,
    text_content_bytes: int,
    comparison_identity: str,
    ledger_sha256: str,
) -> int:
    zeroed = {
        "schema": "shiftory.retrieval/v1",
        "comparison_identity": comparison_identity,
        "ledger_sha256": ledger_sha256,
        "range_id": "source-range_" + "0" * 24,
        "citation_id": citation["id"],
        "path": citation["path"],
        "side": citation["side"],
        "start_line": start_line,
        "end_line": end_line,
        "content_hash": "0" * 64,
        "text": "",
        "actual_bytes": 0,
        "estimated_tokens": 0,
        "token_estimate_formula": TOKEN_ESTIMATE_FORMULA,
    }
    static_size = canonical_size(zeroed) + text_content_bytes
    actual = static_size
    while True:
        tokens = estimate_tokens(actual)
        adjusted = static_size + len(str(actual)) - 1 + len(str(tokens)) - 1
        if adjusted == actual:
            return actual
        actual = adjusted


def _retrieval_record(
    citation: dict[str, Any],
    *,
    start_line: int,
    end_line: int,
    text: str,
    comparison_identity: str,
    ledger_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    payload = {
        "citation_id": citation["id"],
        "path": citation["path"],
        "side": citation["side"],
        "start_line": start_line,
        "end_line": end_line,
        "content_hash": content_hash,
    }
    range_id = source_range_identity(payload)
    response = {
        "schema": "shiftory.retrieval/v1",
        "comparison_identity": comparison_identity,
        "ledger_sha256": ledger_sha256,
        "range_id": range_id,
        **payload,
        "text": text,
        "actual_bytes": 0,
        "estimated_tokens": 0,
        "token_estimate_formula": TOKEN_ESTIMATE_FORMULA,
    }
    response = _set_measured_fields(response)
    return (
        {
            "id": range_id,
            **payload,
            "response_bytes": response["actual_bytes"],
            "estimated_tokens": response["estimated_tokens"],
        },
        response,
    )


def _set_measured_fields(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    actual = 0
    tokens = 0
    while True:
        result["actual_bytes"] = actual
        result["estimated_tokens"] = tokens
        measured = canonical_size(result)
        estimated = estimate_tokens(measured)
        if measured == actual and estimated == tokens:
            return result
        actual, tokens = measured, estimated


def _graph_components(
    evidence: dict[str, Any],
    changed_paths: set[str],
) -> tuple[Literal["graph-guided", "deterministic-fallback"], dict[str, str]]:
    disjoint = _DisjointSet(changed_paths)
    facts = evidence.get("graph", {}).get("facts", [])
    definitions: dict[tuple[str, str], set[str]] = defaultdict(set)
    relations: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        side, symbol, path = fact.get("side"), fact.get("symbol"), fact.get("path")
        if not isinstance(side, str) or not isinstance(symbol, str) or not isinstance(path, str):
            continue
        if path not in changed_paths:
            continue
        if fact.get("kind") in {"definition", "changed_symbol", "enclosing_symbol"}:
            definitions[(side, symbol)].add(path)
        elif fact.get("kind") in {"caller", "callee", "importer", "static_test"}:
            relations[(side, symbol)].add(path)
    linked = False
    for key in sorted(definitions.keys() & relations.keys()):
        participants = definitions[key] | relations[key]
        if len(participants) < 2:
            continue
        representative = min(participants)
        for path in sorted(participants):
            linked = disjoint.union(representative, path) or linked
    component_by_path = {path: disjoint.find(path) for path in sorted(changed_paths)}
    status = evidence.get("graph", {}).get("status")
    strategy: Literal["graph-guided", "deterministic-fallback"] = (
        "graph-guided" if status == "available" and linked else "deterministic-fallback"
    )
    return strategy, component_by_path


def _pack(
    atoms: tuple[_Atom, ...],
    *,
    evidence: dict[str, Any],
    comparison_identity: str,
    ledger_sha256: str,
    strategy: str,
    budget: AgentBudget,
    citation_text: dict[str, str],
) -> tuple[dict[str, Any], ...]:
    if not atoms:
        return ()
    partitions: list[list[_Atom]] = []
    count_hint = len(atoms)
    offset = 0
    while offset < len(atoms):
        best_end = offset
        step = 1
        first_failed_end: int | None = None
        while best_end < len(atoms):
            end = min(len(atoms), offset + step)
            payload = _chunk_payload(
                list(atoms[offset:end]),
                index=len(partitions) + 1,
                count=count_hint,
                comparison_identity=comparison_identity,
                ledger_sha256=ledger_sha256,
                strategy=strategy,
                budget=budget,
            )
            if int(payload["budget"]["actual_bytes"]) <= budget.effective_max_bytes:
                best_end = end
                if end == len(atoms):
                    break
                step *= 2
            else:
                first_failed_end = end
                break
        if best_end == offset:
            atom = atoms[offset]
            raise ChunkBudgetError(
                "An irreducible ownership atom cannot fit the effective agent budget",
                details={
                    "atom_id": atom.id,
                    "ownership_target_ids": [
                        target["evidence_id"] for target in atom.value["ownership_targets"]
                    ],
                    "required_bytes": payload["budget"]["actual_bytes"],
                    "effective_max_bytes": budget.effective_max_bytes,
                },
            )
        low = best_end + 1
        high = first_failed_end - 1 if first_failed_end is not None else best_end
        while low <= high:
            end = (low + high) // 2
            payload = _chunk_payload(
                list(atoms[offset:end]),
                index=len(partitions) + 1,
                count=count_hint,
                comparison_identity=comparison_identity,
                ledger_sha256=ledger_sha256,
                strategy=strategy,
                budget=budget,
            )
            if int(payload["budget"]["actual_bytes"]) <= budget.effective_max_bytes:
                best_end = end
                low = end + 1
            else:
                high = end - 1
        partitions.append(list(atoms[offset:best_end]))
        offset = best_end

    graph_facts_by_path = _index_graph_facts(evidence.get("graph", {}).get("facts", []))
    finalized = []
    for index, partition in enumerate(partitions, 1):
        chunk = _chunk_payload(
            partition,
            index=index,
            count=len(partitions),
            comparison_identity=comparison_identity,
            ledger_sha256=ledger_sha256,
            strategy=strategy,
            budget=budget,
        )
        chunk = _inline_contexts(chunk, citation_text, budget)
        chunk = _include_graph_facts(chunk, graph_facts_by_path, budget)
        if int(chunk["budget"]["actual_bytes"]) > budget.effective_max_bytes:
            raise AssertionError("Final chunk exceeds its effective byte budget")
        finalized.append(chunk)
    return tuple(finalized)


def _chunk_payload(
    atoms: list[_Atom],
    *,
    index: int,
    count: int,
    comparison_identity: str,
    ledger_sha256: str,
    strategy: str,
    budget: AgentBudget,
) -> dict[str, Any]:
    allowed = {reference for atom in atoms for reference in _atom_references(atom.value)}
    payload = {
        "schema": "shiftory.chunk/v1",
        "id": "",
        "comparison_identity": comparison_identity,
        "ledger_sha256": ledger_sha256,
        "index": index,
        "count": count,
        "grouping_strategy": strategy,
        "budget": {
            **budget.to_dict(),
            "actual_bytes": 0,
            "estimated_tokens": 0,
        },
        "work_items": [deepcopy(atom.value) for atom in atoms],
        "graph_facts": [],
        "omitted_graph_fact_ids": [],
        "allowed_citation_ids": sorted(allowed),
    }
    return _finalize_chunk(payload)


def _atom_references(atom: dict[str, Any]) -> set[str]:
    references = {
        atom["unit"]["id"],
        *(target["evidence_id"] for target in atom["ownership_targets"]),
        *(context["citation_id"] for context in atom["contexts"]),
    }
    hunk = atom.get("hunk")
    if isinstance(hunk, dict):
        references.add(hunk["id"])
    return references


def _finalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(chunk)
    result["id"] = chunk_identity(result)
    actual = 0
    tokens = 0
    while True:
        result["budget"]["actual_bytes"] = actual
        result["budget"]["estimated_tokens"] = tokens
        measured = canonical_size(result)
        estimated = estimate_tokens(measured)
        if measured == actual and estimated == tokens:
            return result
        actual, tokens = measured, estimated


def _inline_contexts(
    chunk: dict[str, Any],
    citation_text: dict[str, str],
    budget: AgentBudget,
) -> dict[str, Any]:
    selected = deepcopy(chunk)
    estimated_size = int(selected["budget"]["actual_bytes"])
    replacements: list[tuple[int, int, dict[str, Any], int]] = []
    for item_index, item in enumerate(selected["work_items"]):
        for context_index, context in enumerate(item["contexts"]):
            if context["text"] is not None:
                continue
            inline_context = {
                **context,
                "text": citation_text[context["citation_id"]],
                "retrieval_range_ids": [],
            }
            delta = _component_size(inline_context) - _component_size(context)
            if delta <= 0 or estimated_size + delta <= budget.effective_max_bytes:
                replacements.append((item_index, context_index, context, delta))
                selected["work_items"][item_index]["contexts"][context_index] = inline_context
                estimated_size += delta
    selected = _finalize_chunk(selected)
    while int(selected["budget"]["actual_bytes"]) > budget.effective_max_bytes:
        positive = next(
            (replacement for replacement in reversed(replacements) if replacement[3] > 0),
            None,
        )
        if positive is None:
            return chunk
        replacements.remove(positive)
        item_index, context_index, original, _delta = positive
        selected["work_items"][item_index]["contexts"][context_index] = original
        selected = _finalize_chunk(selected)
    return selected


def _include_graph_facts(
    chunk: dict[str, Any],
    facts_by_path: dict[str, tuple[dict[str, Any], ...]],
    budget: AgentBudget,
) -> dict[str, Any]:
    paths = {
        path
        for item in chunk["work_items"]
        for path in (item["file"]["old_path"], item["file"]["new_path"])
        if isinstance(path, str)
    }
    relevant = sorted(
        chain.from_iterable(facts_by_path.get(path, ()) for path in paths),
        key=_graph_fact_key,
    )
    selected = deepcopy(chunk)
    estimated_size = int(selected["budget"]["actual_bytes"])
    original_allowed = set(selected["allowed_citation_ids"])
    included: list[tuple[dict[str, Any], bool]] = []
    omitted: list[str] = []
    for fact in relevant:
        added_allowed = fact["id"] not in original_allowed
        delta = _component_size(fact) + (1 if selected["graph_facts"] else 0)
        if added_allowed:
            delta += _component_size(fact["id"]) + (1 if selected["allowed_citation_ids"] else 0)
        if estimated_size + delta <= budget.effective_max_bytes:
            selected["graph_facts"].append(fact)
            if added_allowed:
                selected["allowed_citation_ids"].append(fact["id"])
                selected["allowed_citation_ids"].sort()
            included.append((fact, added_allowed))
            estimated_size += delta
        else:
            omitted.append(fact["id"])
    selected = _finalize_chunk(selected)
    while int(selected["budget"]["actual_bytes"]) > budget.effective_max_bytes and included:
        fact, added_allowed = included.pop()
        selected["graph_facts"].pop()
        if added_allowed:
            selected["allowed_citation_ids"].remove(fact["id"])
        omitted.insert(0, fact["id"])
        selected = _finalize_chunk(selected)
    omitted_size = int(selected["budget"]["actual_bytes"])
    for fact_id in omitted:
        delta = _component_size(fact_id) + (1 if selected["omitted_graph_fact_ids"] else 0)
        if omitted_size + delta > budget.effective_max_bytes:
            break
        selected["omitted_graph_fact_ids"].append(fact_id)
        omitted_size += delta
    selected = _finalize_chunk(selected)
    while int(selected["budget"]["actual_bytes"]) > budget.effective_max_bytes:
        selected["omitted_graph_fact_ids"].pop()
        selected = _finalize_chunk(selected)
    return selected


def _graph_fact_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    return (
        fact.get("side", ""),
        fact.get("path", ""),
        fact.get("line") or 0,
        fact.get("kind", ""),
        fact.get("id", ""),
    )


def _index_graph_facts(facts: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in sorted(
        (fact for fact in facts if isinstance(fact, dict)),
        key=_graph_fact_key,
    ):
        path = fact.get("path")
        if isinstance(path, str):
            indexed[path].append(fact)
    return {path: tuple(values) for path, values in indexed.items()}
