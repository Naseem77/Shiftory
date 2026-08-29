"""Deterministic v1 JSON and Markdown report rendering."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from shiftory.explain.validator import validate_explanation

_SECTION_TITLES = {
    "behavioral": "Behavioral before to after",
    "structural": "Structural and non-behavioral changes",
    "observer": "Who observes the changes",
    "ambiguity": "Ambiguity and unresolved notes",
    "unresolved": "Ambiguity and unresolved notes",
}


def build_report(evidence: dict[str, Any], explanation: dict[str, Any]) -> dict[str, Any]:
    result = validate_explanation(evidence, explanation)
    sections: dict[str, list[dict[str, Any]]] = {
        "behavioral": [],
        "structural": [],
        "observer": [],
        "ambiguity": [],
    }
    for item in explanation["items"]:
        key = "ambiguity" if item["kind"] == "unresolved" else item["kind"]
        sections[key].append(deepcopy(item))
    owners = sorted(
        explanation["coverage_owners"],
        key=lambda value: (value["evidence_id"], value["owner_id"]),
    )
    return {
        "schema": "shiftory.report/v1",
        "evidence_schema": evidence["schema"],
        "explanation_schema": explanation["schema"],
        "comparison_identity": evidence["comparison"]["identity"],
        "summary": explanation["summary"],
        "sections": sections,
        "coverage": result.to_dict(),
        "coverage_owners": owners,
        "guarantee": (
            "Shiftory verified accounting and citation references; "
            "it does not verify semantic correctness."
        ),
    }


def render_report(evidence: dict[str, Any], explanation: dict[str, Any]) -> str:
    return render_report_markdown(build_report(evidence, explanation))


def render_report_markdown(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    lines = ["# Shiftory explanation", "", report["summary"], ""]
    order = ("behavioral", "structural", "observer", "ambiguity")
    for key in order:
        lines.extend([f"## {_SECTION_TITLES[key]}", ""])
        items = report["sections"][key]
        if not items:
            lines.extend(["_None._", ""])
            continue
        for item in items:
            lines.append(f"### {item['title']}")
            lines.append("")
            if "before" in item and "after" in item:
                before = "Absent" if item.get("absence") == "before" else item.get("before", "")
                after = "Absent" if item.get("absence") == "after" else item.get("after", "")
                lines.extend([f"**Before:** {before}", "", f"**After:** {after}", ""])
            if item.get("statement"):
                lines.extend([item["statement"], ""])
            references = [
                citation.get("id") if isinstance(citation, dict) else citation
                for citation in item.get("citations", [])
            ]
            if references:
                lines.extend(
                    [
                        f"Evidence: {', '.join(f'`{reference}`' for reference in references)}",
                        "",
                    ]
                )
            lines.extend([f"Confidence: **{item['confidence']}**", ""])
    lines.extend(
        [
            "## Complete source-cited coverage appendix",
            "",
            (
                f"- Changed lines: {coverage['line_owned']}/{coverage['line_total']} "
                f"({coverage['line_coverage_ratio']:.0%})"
            ),
            (
                f"- Change spans: {coverage['span_owned']}/{coverage['span_total']} "
                f"({coverage['span_coverage_ratio']:.0%})"
            ),
            (
                f"- Textual hunks: {coverage['hunk_covered']}/{coverage['hunk_total']} "
                f"({coverage['hunk_coverage_ratio']:.0%})"
            ),
            (
                f"- Change units: {coverage['unit_covered']}/{coverage['unit_total']} "
                f"({coverage['unit_coverage_ratio']:.0%})"
            ),
            f"- Valid citation references: {coverage['citation_count']}",
            "",
            ("Spans without a direct entry inherit the unanimous owner of their changed lines."),
            "",
            "| Directly owned evidence | Coverage owner |",
            "|---|---|",
        ]
    )
    for owner in report["coverage_owners"]:
        lines.append(f"| `{owner['evidence_id']}` | `{owner['owner_id']}` |")
    lines.extend(["", f"> {report['guarantee']}", ""])
    return "\n".join(lines)
