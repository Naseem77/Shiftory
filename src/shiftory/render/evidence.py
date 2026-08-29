"""Bounded Markdown view over the canonical evidence model."""

from __future__ import annotations

from typing import Any

from shiftory.models.json import canonical_json


def render_evidence_markdown(evidence: dict[str, Any]) -> str:
    metrics = evidence["metrics"]
    lines = [
        "# Shiftory evidence",
        "",
        f"Comparison: `{evidence['comparison']['identity']}` ({evidence['comparison']['mode']})",
        "",
        "## Change ledger",
        "",
        (
            f"{metrics['files']} files · {metrics['units']} units · "
            f"{metrics['hunks']} hunks · {metrics.get('spans', 0)} spans · "
            f"{metrics['added_lines']} added lines · "
            f"{metrics['deleted_lines']} deleted lines"
        ),
        "",
    ]
    if not evidence["files"]:
        lines.extend(["**No Git changes are present in this comparison.**", ""])
    for file in evidence["files"]:
        path = file["new_path"] or file["old_path"]
        spans = {span["id"]: span for span in file.get("spans", [])}
        hunks = {hunk["id"]: hunk for hunk in file["hunks"]}
        lines.extend(
            [
                f"### `{path}`",
                "",
                (
                    f"Status: {file['status']} · Classification: "
                    f"{file['classification']} ({file['classification_confidence']})"
                ),
                "",
            ]
        )
        for unit in file.get("units", []):
            lines.extend([f"#### Unit `{unit['id']}` ({unit['kind']})", ""])
            if unit["metadata"]:
                lines.extend(
                    [
                        f"Metadata: `{canonical_json(unit['metadata']).strip()}`",
                        "",
                    ]
                )
            if not unit["hunk_ids"]:
                lines.extend(["Textual hunks: none.", ""])
            for hunk_id in unit["hunk_ids"]:
                hunk = hunks[hunk_id]
                lines.extend(
                    [
                        (
                            f"##### Hunk `{hunk['id']}` "
                            f"(-{hunk['old_start']},{hunk['old_count']} "
                            f"+{hunk['new_start']},{hunk['new_count']})"
                        ),
                        "",
                        "Spans:",
                    ]
                )
                for span_id in hunk["span_ids"]:
                    span = spans[span_id]
                    replacement = (
                        f"; replaces `{span['replacement_span_id']}`"
                        if span["replacement_span_id"]
                        else ""
                    )
                    owned_lines = ", ".join(f"`{line_id}`" for line_id in span["line_ids"])
                    lines.append(
                        f"- `{span_id}` — {span['side']} "
                        f"{span['start_line']}-{span['end_line']}; "
                        f"lines {owned_lines}{replacement}"
                    )
                lines.extend(["", "```diff"])
                for line in hunk["lines"]:
                    marker = "-" if line["side"] == "before" else "+"
                    lines.append(f"{marker}{line['content']}")
                lines.extend(["```", "", "Changed lines:"])
                for line in hunk["lines"]:
                    coordinate = line["old_line"] if line["side"] == "before" else line["new_line"]
                    lines.append(
                        f"- `{line['id']}` — {line['side']} line {coordinate} "
                        f"(ordinal {line['ordinal']})"
                    )
                lines.append("")
        if file.get("citations"):
            lines.extend(["#### Source citations", ""])
            for citation in file["citations"]:
                state = "omitted; retrieval retained" if citation["omitted"] else "included"
                lines.append(
                    f"- `{citation['id']}` — `{citation['path']}` "
                    f"{citation['side']}:{citation['start_line']}-{citation['end_line']} "
                    f"({state}, sha256 `{citation['content_hash']}`)"
                )
            lines.append("")
    lines.extend(["## Structural enrichment", ""])
    graph = evidence["graph"]
    version = f" {graph['version']}" if graph.get("version") else ""
    provider_label = f"{graph.get('provider', 'graphora')}{version}"
    lines.append(f"Graphora: **{graph['status']}** (`{provider_label}`)")
    lines.append("")
    for fact in graph.get("facts", []):
        location = (
            f"{fact['path']}:{fact['line']}"
            if fact.get("line")
            else fact["path"] or "unresolved location"
        )
        lines.append(
            f"- `{fact['id']}` {fact['kind']} {fact.get('symbol') or ''} "
            f"at `{location}` ({fact['confidence']}; {fact['provenance']})"
        )
    if evidence.get("omissions"):
        lines.extend(["", "## Omitted source context", ""])
        for omission in evidence["omissions"]:
            retrieval = omission["retrieval"]
            lines.append(
                f"- `{omission['citation_id']}` — retrieve `{retrieval['path']}` "
                f"{retrieval['side']}:{retrieval['start_line']}-{retrieval['end_line']} "
                f"(sha256 `{retrieval['content_hash']}`)"
            )
    if evidence.get("diagnostics"):
        lines.extend(["", "## Diagnostics", ""])
        for diagnostic in evidence["diagnostics"]:
            lines.append(f"- **{diagnostic['code']}**: {diagnostic['message']}")
    return "\n".join(lines).rstrip() + "\n"
