"""Fail-closed composition of per-chunk explanations into explanation/v1."""

from __future__ import annotations

from typing import Any

from shiftory.chunking.planner import chunk_identity, plan_identity, sha256_json
from shiftory.errors import CompositionError


def compose_chunks(
    evidence: dict[str, Any],
    plan: dict[str, Any],
    chunks: tuple[dict[str, Any], ...],
    explanations: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    comparison_identity = _comparison_identity(evidence)
    ledger_sha256 = sha256_json(evidence)
    errors: list[str] = []
    if plan.get("id") != plan_identity(plan):
        errors.append("chunk plan identity is invalid")
    if plan.get("comparison_identity") != comparison_identity:
        errors.append("chunk plan comparison identity is stale")
    if plan.get("ledger_sha256") != ledger_sha256:
        errors.append("chunk plan ledger digest is stale")

    entries = plan.get("chunks")
    if not isinstance(entries, list):
        errors.append("chunk plan entries must be an array")
        entries = []
    if len(chunks) != len(entries) or len(explanations) != len(entries):
        errors.append("all planned chunks and chunk explanations are required")

    span_lines, non_text_units, references = _ledger(evidence)
    global_targets = set(span_lines) | non_text_units
    assigned_targets: set[str] = set()
    item_ids: set[str] = set()
    final_items: list[dict[str, Any]] = []
    final_owners: list[dict[str, str]] = []
    summaries: list[str] = []

    for offset, entry in enumerate(entries):
        if offset >= len(chunks) or offset >= len(explanations):
            continue
        chunk = chunks[offset]
        explanation = explanations[offset]
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if chunk.get("id") != entry_id:
            errors.append(f"chunk {offset + 1} does not match the plan entry")
        if chunk.get("id") != chunk_identity(chunk):
            errors.append(f"chunk {offset + 1} identity is invalid")
        if not isinstance(entry, dict) or entry.get("payload_sha256") != sha256_json(chunk):
            errors.append(f"chunk {offset + 1} payload digest is invalid")
        if chunk.get("index") != offset + 1 or chunk.get("count") != len(entries):
            errors.append(f"chunk {offset + 1} index or count is invalid")
        if chunk.get("comparison_identity") != comparison_identity:
            errors.append(f"chunk {offset + 1} comparison identity is stale")
        if chunk.get("ledger_sha256") != ledger_sha256:
            errors.append(f"chunk {offset + 1} ledger digest is stale")

        expected_targets: set[str] = set()
        for item in chunk.get("work_items", []):
            if not isinstance(item, dict):
                continue
            for target in item.get("ownership_targets", []):
                if isinstance(target, dict) and isinstance(target.get("evidence_id"), str):
                    expected_targets.add(target["evidence_id"])
        recorded_targets: set[str] = (
            {value for value in entry.get("ownership_target_ids", []) if isinstance(value, str)}
            if isinstance(entry, dict)
            else set()
        )
        if expected_targets != recorded_targets:
            errors.append(f"chunk {offset + 1} ownership assignment was tampered")
        overlap = assigned_targets & expected_targets
        if overlap:
            errors.append(
                f"cross-chunk duplicate ownership assignments: {', '.join(sorted(overlap))}"
            )
        assigned_targets.update(expected_targets)

        if explanation.get("chunk_id") != chunk.get("id"):
            errors.append(f"chunk explanation {offset + 1} binds to a different chunk")
        if explanation.get("comparison_identity") != comparison_identity:
            errors.append(f"chunk explanation {offset + 1} comparison identity is stale")
        if explanation.get("ledger_sha256") != ledger_sha256:
            errors.append(f"chunk explanation {offset + 1} ledger digest is stale")

        raw_items = explanation.get("items")
        if not isinstance(raw_items, list):
            errors.append(f"chunk explanation {offset + 1} items must be an array")
            raw_items = []
        local_item_ids: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                errors.append(f"chunk explanation {offset + 1} contains an invalid item")
                continue
            item_id = item["id"]
            if item_id in local_item_ids or item_id in item_ids:
                errors.append(f"duplicate explanation item id: {item_id}")
            local_item_ids.add(item_id)
            item_ids.add(item_id)
            _validate_item_citations(
                item,
                allowed=set(chunk.get("allowed_citation_ids", [])),
                references=references,
                errors=errors,
                chunk_index=offset + 1,
            )
            final_items.append(item)

        raw_owners = explanation.get("coverage_owners")
        if not isinstance(raw_owners, list):
            errors.append(f"chunk explanation {offset + 1} owners must be an array")
            raw_owners = []
        owners: dict[str, str] = {}
        duplicate_targets: set[str] = set()
        for owner in raw_owners:
            if not isinstance(owner, dict):
                errors.append(f"chunk explanation {offset + 1} contains an invalid owner")
                continue
            evidence_id, owner_id = owner.get("evidence_id"), owner.get("owner_id")
            if not isinstance(evidence_id, str) or not isinstance(owner_id, str):
                errors.append(f"chunk explanation {offset + 1} contains an invalid owner")
                continue
            if evidence_id in owners:
                duplicate_targets.add(evidence_id)
            else:
                owners[evidence_id] = owner_id
            if owner_id not in local_item_ids:
                errors.append(
                    f"chunk {offset + 1} owner {owner_id!r} does not identify a local item"
                )
        if duplicate_targets:
            errors.append(
                f"chunk {offset + 1} duplicates ownership for "
                f"{', '.join(sorted(duplicate_targets))}"
            )
        if set(owners) != expected_targets:
            missing = expected_targets - set(owners)
            extra = set(owners) - expected_targets
            if missing:
                errors.append(
                    f"chunk {offset + 1} is missing owners for {', '.join(sorted(missing))}"
                )
            if extra:
                errors.append(
                    f"chunk {offset + 1} owns unassigned targets {', '.join(sorted(extra))}"
                )
        for evidence_id, owner_id in sorted(owners.items()):
            if evidence_id in span_lines:
                final_owners.extend(
                    {"evidence_id": line_id, "owner_id": owner_id}
                    for line_id in span_lines[evidence_id]
                )
                final_owners.append({"evidence_id": evidence_id, "owner_id": owner_id})
            elif evidence_id in non_text_units:
                final_owners.append({"evidence_id": evidence_id, "owner_id": owner_id})
        summary = explanation.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries.append(summary.strip())
        else:
            errors.append(f"chunk explanation {offset + 1} requires a non-empty summary")

    if assigned_targets != global_targets:
        missing = global_targets - assigned_targets
        extra = assigned_targets - global_targets
        if missing:
            errors.append(f"chunk plan misses ownership targets: {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"chunk plan has unknown ownership targets: {', '.join(sorted(extra))}")
    if errors:
        raise CompositionError(
            f"Chunk composition failed with {len(errors)} error(s)",
            details={"errors": [{"message": error} for error in errors]},
        )
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "\n\n".join(summaries),
        "items": final_items,
        "coverage_owners": sorted(
            final_owners, key=lambda owner: (owner["evidence_id"], owner["owner_id"])
        ),
    }


def _comparison_identity(evidence: dict[str, Any]) -> str:
    comparison = evidence.get("comparison")
    identity = comparison.get("identity") if isinstance(comparison, dict) else None
    if not isinstance(identity, str) or not identity:
        raise CompositionError("The global evidence ledger has no comparison identity")
    return identity


def _ledger(
    evidence: dict[str, Any],
) -> tuple[dict[str, tuple[str, ...]], set[str], set[str]]:
    span_lines: dict[str, tuple[str, ...]] = {}
    non_text_units: set[str] = set()
    references: set[str] = set()
    for file in evidence.get("files", []):
        for unit in file.get("units", []):
            unit_id = unit["id"]
            references.add(unit_id)
            if unit["kind"] != "text":
                non_text_units.add(unit_id)
        for hunk in file.get("hunks", []):
            references.add(hunk["id"])
            references.update(line["id"] for line in hunk.get("lines", []))
        for span in file.get("spans", []):
            span_lines[span["id"]] = tuple(span["line_ids"])
            references.add(span["id"])
        references.update(citation["id"] for citation in file.get("citations", []))
    references.update(fact["id"] for fact in evidence.get("graph", {}).get("facts", []))
    return span_lines, non_text_units, references


def _validate_item_citations(
    item: dict[str, Any],
    *,
    allowed: set[Any],
    references: set[str],
    errors: list[str],
    chunk_index: int,
) -> None:
    citations = item.get("citations")
    if not isinstance(citations, list):
        errors.append(f"chunk {chunk_index} item {item['id']!r} citations must be an array")
        return
    for citation in citations:
        reference = (
            citation
            if isinstance(citation, str)
            else citation.get("id")
            if isinstance(citation, dict)
            else None
        )
        if not isinstance(reference, str) or reference not in references:
            errors.append(f"chunk {chunk_index} item {item['id']!r} has an unknown citation")
        elif reference not in allowed:
            errors.append(
                f"chunk {chunk_index} item {item['id']!r} cites evidence outside its chunk"
            )
