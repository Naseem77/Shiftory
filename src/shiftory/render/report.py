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
_ACCOUNTING_GUARANTEE = (
    "Shiftory verified accounting and citation references; it does not verify semantic correctness."
)
_GROUNDED_GUARANTEE = (
    "Shiftory verified accounting, citation references, and every declared grounding claim "
    "against the exact evidence bound to it; verified claims are source-level facts and do not "
    "establish runtime behavior or semantic correctness."
)


def build_report(
    evidence: dict[str, Any],
    explanation: dict[str, Any],
    *,
    require_grounding: bool = False,
) -> dict[str, Any]:
    result = validate_explanation(evidence, explanation, require_grounding=require_grounding)
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
    grounding = result.grounding
    grounded = grounding is not None and grounding.claim_total > 0
    report: dict[str, Any] = {
        "schema": "shiftory.report/v1",
        "evidence_schema": evidence["schema"],
        "explanation_schema": explanation["schema"],
        "comparison_identity": evidence["comparison"]["identity"],
        "summary": explanation["summary"],
        "sections": sections,
        "coverage": result.to_dict(),
        "coverage_owners": owners,
        "guarantee": _GROUNDED_GUARANTEE if grounded else _ACCOUNTING_GUARANTEE,
    }
    if grounding is not None and grounded:
        report["grounding"] = grounding.to_dict()
    return report


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
    lines.extend(_render_grounding(report.get("grounding")))
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


def _render_grounding(grounding: dict[str, Any] | None) -> list[str]:
    if not grounding:
        return []
    unresolved = grounding["unresolved"] + grounding["unavailable"]
    lines = [
        "## Grounded claims",
        "",
        f"Grounding mode: **{grounding['mode']}**.",
        "",
        (
            f"- Claims: {grounding['claim_total']} across "
            f"{grounding['grounded_items']} explanation item(s)"
        ),
        f"- Verified against bound evidence: {grounding['verified']}",
        f"- Declared inferred: {grounding['inferred']}",
        f"- Declared ambiguous: {grounding['ambiguous']}",
        f"- Declared unresolved or unavailable: {unresolved}",
        "",
        (
            "Verified claims are proven against the exact evidence bound to them. Source order "
            "is lexical order inside the cited region, never execution order."
        ),
        "",
    ]
    for item in grounding["items"]:
        lines.extend([f"### Grounding for `{item['item_id']}`", ""])
        for claim in item["claims"]:
            lines.append(
                f"- `{claim['claim_id']}` (`{claim['type']}`, **{claim['support_level']}**): "
                f"{claim['proof']}"
            )
            if claim["limits"]:
                lines.append(f"  - Limits: {claim['limits']}")
        lines.append("")
    return lines
