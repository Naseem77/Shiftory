from __future__ import annotations

from jsonschema import Draft202012Validator

from shiftory.render.evidence import render_evidence_markdown
from shiftory.render.report import build_report, render_report_markdown
from shiftory.schemas import load_schema


def test_fixed_report_section_order() -> None:
    report = {
        "summary": "Summary.",
        "sections": {"behavioral": [], "structural": [], "observer": [], "ambiguity": []},
        "coverage": {
            "line_owned": 0,
            "line_total": 0,
            "line_coverage_ratio": 1.0,
            "span_owned": 0,
            "span_total": 0,
            "span_coverage_ratio": 1.0,
            "hunk_covered": 0,
            "hunk_total": 0,
            "hunk_coverage_ratio": 1.0,
            "unit_covered": 0,
            "unit_total": 0,
            "unit_coverage_ratio": 1.0,
            "citation_count": 0,
        },
        "coverage_owners": [],
        "guarantee": "Accounting only.",
    }
    output = render_report_markdown(report)
    headings = [
        "## Behavioral before to after",
        "## Structural and non-behavioral changes",
        "## Who observes the changes",
        "## Ambiguity and unresolved notes",
        "## Complete source-cited coverage appendix",
    ]
    assert [output.index(heading) for heading in headings] == sorted(
        output.index(heading) for heading in headings
    )
    assert "Change spans: 0/0 (100%)" in output
    assert "Valid citation references: 0" in output
    assert "Spans without a direct entry inherit" in output


def test_report_json_schema_and_markdown_layering_are_deterministic() -> None:
    evidence = {
        "schema": "shiftory.evidence/v1",
        "comparison": {"identity": "comparison"},
        "files": [
            {
                "units": [{"id": "u1", "kind": "text", "hunk_ids": ["h1"]}],
                "hunks": [
                    {
                        "id": "h1",
                        "span_ids": ["s1", "s2"],
                        "lines": [{"id": "l1"}, {"id": "l2"}],
                    }
                ],
                "spans": [
                    {"id": "s1", "line_ids": ["l1"]},
                    {"id": "s2", "line_ids": ["l2"]},
                ],
                "citations": [{"id": "c1"}],
            }
        ],
        "graph": {"facts": []},
    }
    explanation = {
        "schema": "shiftory.explanation/v1",
        "summary": "A declaration changes shape.",
        "items": [
            {
                "id": "shape",
                "kind": "structural",
                "title": "Reshape the declaration",
                "before": "The declaration used one field.",
                "after": "The declaration uses two fields.",
                "statement": "The declaration is represented by a wider mapping.",
                "confidence": "extracted",
                "citations": ["c1", "c1"],
            }
        ],
        "coverage_owners": [
            {"evidence_id": "l2", "owner_id": "shape"},
            {"evidence_id": "l1", "owner_id": "shape"},
        ],
    }
    first = build_report(evidence, explanation)
    second = build_report(evidence, explanation)
    assert first == second
    assert first["coverage_owners"] == [
        {"evidence_id": "l1", "owner_id": "shape"},
        {"evidence_id": "l2", "owner_id": "shape"},
    ]
    assert not list(Draft202012Validator(load_schema("report")).iter_errors(first))

    markdown = render_report_markdown(first)
    assert markdown == render_report_markdown(second)
    assert markdown.index("**Before:**") < markdown.index("**After:**")
    assert markdown.index("**After:**") < markdown.index("The declaration is represented")
    assert "Change spans: 2/2 (100%)" in markdown
    assert "Valid citation references: 2" in markdown


def test_evidence_markdown_uses_the_canonical_ledger() -> None:
    evidence = {
        "comparison": {"identity": "comparison", "mode": "working"},
        "metrics": {
            "files": 1,
            "units": 1,
            "hunks": 1,
            "spans": 2,
            "added_lines": 1,
            "deleted_lines": 1,
        },
        "files": [
            {
                "new_path": "app.py",
                "old_path": "app.py",
                "status": "modified",
                "classification": "behavioral",
                "classification_confidence": "inferred",
                "units": [
                    {
                        "id": "unit",
                        "kind": "text",
                        "hunk_ids": ["hunk"],
                        "metadata": {},
                    }
                ],
                "spans": [
                    {
                        "id": "before-span",
                        "side": "before",
                        "start_line": 1,
                        "end_line": 1,
                        "line_ids": ["before-line"],
                        "replacement_span_id": "after-span",
                    },
                    {
                        "id": "after-span",
                        "side": "after",
                        "start_line": 1,
                        "end_line": 1,
                        "line_ids": ["after-line"],
                        "replacement_span_id": "before-span",
                    },
                ],
                "citations": [],
                "hunks": [
                    {
                        "id": "hunk",
                        "old_start": 1,
                        "old_count": 1,
                        "new_start": 1,
                        "new_count": 1,
                        "span_ids": ["before-span", "after-span"],
                        "lines": [
                            {
                                "id": "before-line",
                                "side": "before",
                                "old_line": 1,
                                "new_line": None,
                                "ordinal": 0,
                                "content": "old",
                            },
                            {
                                "id": "after-line",
                                "side": "after",
                                "old_line": None,
                                "new_line": 1,
                                "ordinal": 1,
                                "content": "new",
                            },
                        ],
                    }
                ],
            }
        ],
        "graph": {
            "status": "available",
            "provider": "graphora",
            "version": "0.2.1",
            "facts": [
                {
                    "id": "fact",
                    "kind": "caller",
                    "path": "caller.py",
                    "line": 2,
                    "symbol": "value",
                    "confidence": "inferred",
                    "provenance": "graphora:tree-sitter",
                }
            ],
        },
        "omissions": [],
        "diagnostics": [{"code": "bounded", "message": "Context omitted."}],
    }
    output = render_evidence_markdown(evidence)
    assert "```diff\n-old\n+new\n```" in output
    assert "`fact` caller value at `caller.py:2`" in output
    assert "**bounded**: Context omitted." in output
