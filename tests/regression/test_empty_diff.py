from __future__ import annotations

import pytest

from shiftory.errors import ValidationError
from shiftory.evidence.builder import AnalyzeOptions, analyze
from shiftory.explain.validator import validate_explanation
from shiftory.render.evidence import render_evidence_markdown
from shiftory.render.report import build_report, render_report_markdown


def test_empty_diff_requires_and_accepts_explicit_no_changes(repo_factory) -> None:
    repository = repo_factory()
    evidence = analyze(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    explanation = {
        "schema": "shiftory.explanation/v1",
        "summary": "No changes are present.",
        "items": [],
        "coverage_owners": [],
    }
    result = validate_explanation(evidence, explanation)
    assert result.line_total == result.span_total == result.hunk_total == result.unit_total == 0
    assert all(
        value == 1.0 for key, value in result.to_dict().items() if key.endswith("_coverage_ratio")
    )

    report = build_report(evidence, explanation)
    markdown = render_report_markdown(report)
    assert "Changed lines: 0/0 (100%)" in markdown
    assert "Change spans: 0/0 (100%)" in markdown
    assert "Textual hunks: 0/0 (100%)" in markdown
    assert "Change units: 0/0 (100%)" in markdown


def test_empty_diff_rejects_items_and_implicit_summary(repo_factory) -> None:
    repository = repo_factory()
    evidence = analyze(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    explanation = {
        "schema": "shiftory.explanation/v1",
        "summary": "The comparison completed.",
        "items": [
            {
                "id": "nothing",
                "kind": "structural",
                "title": "No source delta",
                "statement": "The source is identical.",
                "confidence": "extracted",
                "citations": [],
            }
        ],
        "coverage_owners": [],
    }
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence, explanation)
    details = str(caught.value.details)
    assert "without items" in details
    assert "explicitly state no changes" in details
    assert evidence["metrics"]["files"] == evidence["metrics"]["changed_lines"] == 0
    assert evidence["diagnostics"] == [
        {
            "code": "no_changes",
            "message": "The Git comparison contains no changed files or change units.",
        }
    ]
    assert "No Git changes are present" in render_evidence_markdown(evidence)
