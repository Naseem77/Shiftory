from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator

from shiftory.errors import ValidationError
from shiftory.explain.validator import validate_explanation
from shiftory.schemas import load_schema


def evidence() -> dict:
    return {
        "schema": "shiftory.evidence/v1",
        "files": [
            {
                "units": [{"id": "u1", "kind": "text", "hunk_ids": ["h1"]}],
                "hunks": [
                    {
                        "id": "h1",
                        "span_ids": ["s1", "s2"],
                        "lines": [
                            {"id": "l1", "side": "before"},
                            {"id": "l2", "side": "after"},
                        ],
                    }
                ],
                "spans": [
                    {"id": "s1", "line_ids": ["l1"]},
                    {"id": "s2", "line_ids": ["l2"]},
                ],
                "citations": [{"id": "c1"}],
            }
        ],
        "graph": {"facts": [{"id": "f1"}]},
    }


def explanation() -> dict:
    return {
        "schema": "shiftory.explanation/v1",
        "summary": "Return behavior changes.",
        "items": [
            {
                "id": "item",
                "kind": "behavioral",
                "title": "Value selection",
                "before": "The function returned one.",
                "after": "The function returns two.",
                "confidence": "extracted",
                "citations": ["c1", "c1", {"id": "f1"}],
            }
        ],
        "coverage_owners": [{"evidence_id": value, "owner_id": "item"} for value in ("l1", "l2")],
    }


def test_explanation_schema_accepts_manifest_and_rejects_judgment_shape() -> None:
    validator = Draft202012Validator(load_schema("explanation"))
    assert not list(validator.iter_errors(explanation()))

    manifest = explanation()
    manifest["items"][0]["severity"] = "high"
    errors = list(validator.iter_errors(manifest))
    assert errors
    assert "Additional properties are not allowed" in errors[0].message


def test_reused_citations_do_not_change_exact_ownership() -> None:
    result = validate_explanation(evidence(), explanation())
    assert result.line_owned == result.line_total == 2
    assert result.span_owned == result.span_total == 2
    assert result.hunk_covered == result.hunk_total == 1
    assert result.unit_covered == result.unit_total == 1
    assert result.citation_count == 3


def test_spans_may_also_repeat_their_inherited_owner_directly() -> None:
    manifest = explanation()
    manifest["coverage_owners"].extend(
        {"evidence_id": value, "owner_id": "item"} for value in ("s1", "s2")
    )
    result = validate_explanation(evidence(), manifest)
    assert result.span_owned == result.span_total == 2


def test_missing_changed_line_owner_fails_clearly() -> None:
    manifest = explanation()
    manifest["coverage_owners"] = [
        owner for owner in manifest["coverage_owners"] if owner["evidence_id"] != "l1"
    ]
    with pytest.raises(ValidationError, match="validation failed") as caught:
        validate_explanation(evidence(), manifest)
    assert "Missing direct coverage owners" in str(caught.value.details)
    assert "l1" in str(caught.value.details)


def test_duplicate_ownership_fails_while_duplicate_citations_pass() -> None:
    manifest = explanation()
    manifest["coverage_owners"].append({"evidence_id": "l1", "owner_id": "item"})
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "multiple coverage owners" in str(caught.value.details)


def test_cross_owner_span_and_conflicting_direct_span_owner_fail() -> None:
    packet = evidence()
    packet["files"][0]["spans"] = [{"id": "s1", "line_ids": ["l1", "l2"]}]
    packet["files"][0]["hunks"][0]["span_ids"] = ["s1"]
    manifest = explanation()
    manifest["items"].append(
        {
            "id": "other",
            "kind": "structural",
            "title": "Other source change",
            "statement": "A second source region changes.",
            "confidence": "inferred",
            "citations": ["c1"],
        }
    )
    manifest["coverage_owners"][1]["owner_id"] = "other"
    manifest["coverage_owners"].append({"evidence_id": "s1", "owner_id": "item"})
    with pytest.raises(ValidationError) as caught:
        validate_explanation(packet, manifest)
    assert "cross-owner coverage" in str(caught.value.details)


def test_hunks_must_exactly_own_each_span_and_changed_line() -> None:
    packet = evidence()
    packet["files"][0]["hunks"][0]["span_ids"] = ["s1"]

    with pytest.raises(ValidationError) as caught:
        validate_explanation(packet, explanation())

    details = str(caught.value.details)
    assert "does not exactly own its changed lines" in details
    assert "Every span must belong to exactly one hunk" in details


def test_direct_span_owner_must_equal_inherited_line_owner() -> None:
    manifest = explanation()
    manifest["items"].append(
        {
            "id": "other",
            "kind": "structural",
            "title": "Other source change",
            "statement": "A second source region changes.",
            "confidence": "inferred",
            "citations": ["c1"],
        }
    )
    manifest["coverage_owners"].append({"evidence_id": "s1", "owner_id": "other"})
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "differs from its inherited line owner" in str(caught.value.details)


def test_non_text_unit_requires_exactly_one_owner() -> None:
    packet = {
        "schema": "shiftory.evidence/v1",
        "files": [
            {
                "units": [{"id": "binary", "kind": "binary", "hunk_ids": []}],
                "hunks": [],
                "spans": [],
                "citations": [],
            }
        ],
        "graph": {"facts": []},
    }
    manifest = {
        "schema": "shiftory.explanation/v1",
        "summary": "A binary artifact changes.",
        "items": [
            {
                "id": "binary-change",
                "kind": "structural",
                "title": "Replace the binary artifact",
                "statement": "The binary artifact has different bytes.",
                "confidence": "extracted",
                "citations": ["binary"],
            }
        ],
        "coverage_owners": [{"evidence_id": "binary", "owner_id": "binary-change"}],
    }
    result = validate_explanation(packet, manifest)
    assert result.line_total == result.hunk_total == 0
    assert result.unit_covered == result.unit_total == 1

    manifest["coverage_owners"] = []
    with pytest.raises(ValidationError) as caught:
        validate_explanation(packet, manifest)
    assert "Missing direct coverage owners" in str(caught.value.details)


def test_unknown_owner_target_and_unknown_evidence_id_fail_clearly() -> None:
    manifest = explanation()
    manifest["coverage_owners"][0]["owner_id"] = "missing-item"
    manifest["coverage_owners"].append({"evidence_id": "unknown", "owner_id": "item"})
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    details = str(caught.value.details)
    assert "does not identify an explanation item" in details
    assert "is not an ownable line, span, or non-text unit" in details


def test_source_fact_and_ledger_citations_validate_but_unknown_references_fail() -> None:
    manifest = explanation()
    manifest["items"][0]["citations"].extend(["l1", "s1", "h1", "u1"])
    result = validate_explanation(evidence(), manifest)
    assert result.citation_count == 7

    manifest["items"][0]["citations"].append({"id": "missing"})
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "Unknown evidence reference 'missing'" in str(caught.value.details)


def test_context_aware_policy_allows_identifiers_and_faithful_descriptions() -> None:
    manifest = explanation()
    manifest["items"][0]["title"] = "Review configuration and severity parsing"
    manifest["items"][0]["after"] = (
        "The `risk_score`, `severity_level`, and `fix_bug` fields are now returned, "
        'the src/review/fix_bug.py path is retained, "review finding: high severity" '
        "is emitted verbatim, and the bug fix review handler now accepts high severity "
        "as a domain value."
    )
    validate_explanation(evidence(), manifest)


@pytest.mark.parametrize(
    "value",
    [
        "Bug fix review handler",
        "Defect classification",
        "Security vulnerability field",
        "The credential exposure defect code is returned.",
        "The src/security/vulnerability.py path is retained.",
        "The risk and severity domain values are returned.",
        '"A vulnerability exposes credentials" is retained as quoted source.',
    ],
)
def test_context_aware_policy_allows_domain_terms_without_judgments(value: str) -> None:
    manifest = explanation()
    manifest["items"][0]["after"] = value
    validate_explanation(evidence(), manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Bug tracker integration"),
        ("after", "The bug tracker receives the issue key."),
        ("after", "The bug tracker stores vulnerability records."),
        ("after", "Vulnerability records were added to the response."),
        ("after", "The fix_bug identifier was renamed to repair_issue."),
        ("after", "The risk_score and severity_level identifiers were removed."),
        ("after", "The src/security/vulnerability.py filename was renamed."),
        ("after", "The src/security/vulnerabilities.py path is retained."),
        ("after", "The vulnerability identifier was renamed and the defect flag was removed."),
        ("after", "Vulnerability and defect names were added to the accepted values."),
        ("after", "Vulnerability names were renamed in the generated schema."),
        ("after", "Security flaw names were removed from the accepted values."),
        ("after", "The vulnerabilities identifier was removed from the schema."),
        ("after", '"A vulnerability was detected in the implementation" is removed from output.'),
        ("after", '"Vulnerabilities were discovered in the implementation" is retained verbatim.'),
    ],
)
def test_policy_does_not_treat_explanatory_term_uses_as_findings(field: str, value: str) -> None:
    manifest = explanation()
    manifest["items"][0][field] = value
    validate_explanation(evidence(), manifest)


@pytest.mark.parametrize(
    "title",
    [
        "Defect",
        "Defects",
        "Bug",
        "Bugs",
        "Vulnerability",
        "Vulnerabilities",
        "Security flaw",
        "Security flaws",
    ],
)
def test_policy_rejects_standalone_finding_titles(title: str) -> None:
    manifest = explanation()
    manifest["items"][0]["title"] = title
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "Disallowed defect or security finding" in str(caught.value.details)


@pytest.mark.parametrize(
    "value",
    [
        "A vulnerability was found in the implementation.",
        "A vulnerability was detected in the implementation.",
        "A vulnerability was discovered in the implementation.",
        "Vulnerabilities were found in the implementation.",
        "Vulnerabilities were detected in the implementation.",
        "Vulnerabilities were discovered in the implementation.",
        "The bugs have been found in the implementation.",
        "Security flaws were discovered in the implementation.",
    ],
)
def test_policy_rejects_passive_finding_declarations(value: str) -> None:
    manifest = explanation()
    manifest["items"][0]["after"] = value
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "Disallowed passive defect or security claim" in str(caught.value.details)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Recommendations"),
        ("after", "This change introduces a bug."),
        ("after", "The change is high severity."),
        ("after", "This should be fixed."),
        ("after", "We recommend that callers avoid this implementation."),
        ("after", "Code review found a defect."),
        ("after", "A vulnerability is introduced by this change."),
        ("after", "A security flaw has been created by this patch."),
        ("title", "Credential exposure defect"),
        ("after", "A vulnerability exposes credentials."),
        ("after", "The change leaks credentials."),
    ],
)
def test_policy_rejects_review_judgment_intent(field: str, value: str) -> None:
    manifest = copy.deepcopy(explanation())
    manifest["items"][0][field] = value
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "Disallowed" in str(caught.value.details)


@pytest.mark.parametrize("field", ["findings", "recommendation", "severity", "bug_judgment"])
def test_policy_rejects_review_judgment_structures(field: str) -> None:
    manifest = explanation()
    manifest["items"][0][field] = "Not allowed"
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "review/judgment structure" in str(caught.value.details)


@pytest.mark.parametrize(
    ("kind", "confidence", "message"),
    [
        ("ambiguity", "inferred", "Ambiguity items must use ambiguous confidence"),
        ("unresolved", "extracted", "Unresolved items must use unresolved or unavailable"),
        ("behavioral", "ambiguous", "Ambiguous confidence must be represented"),
        ("structural", "unavailable", "Unresolved or unavailable confidence"),
    ],
)
def test_confidence_must_match_uncertainty_kind(kind: str, confidence: str, message: str) -> None:
    manifest = explanation()
    manifest["items"][0]["kind"] = kind
    manifest["items"][0]["confidence"] = confidence
    if kind != "behavioral":
        manifest["items"][0].pop("before")
        manifest["items"][0].pop("after")
        manifest["items"][0]["statement"] = "The meaning is not established."
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert message in str(caught.value.details)


def test_extracted_confidence_rejects_uncertainty_language() -> None:
    manifest = explanation()
    manifest["items"][0]["after"] = "The function probably returns two."
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "cannot be labeled extracted" in str(caught.value.details)


@pytest.mark.parametrize(
    "value",
    [
        "Handle May dates",
        "The May parser returns normalized dates.",
        "The `may_return` identifier is retained.",
        '"may return" is emitted as quoted source.',
    ],
)
def test_extracted_confidence_allows_non_modal_may_uses(value: str) -> None:
    manifest = explanation()
    manifest["items"][0]["after"] = value
    validate_explanation(evidence(), manifest)


@pytest.mark.parametrize("value", ["The function may return two.", "May return two values."])
def test_extracted_confidence_rejects_modal_may(value: str) -> None:
    manifest = explanation()
    manifest["items"][0]["after"] = value
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "cannot be labeled extracted" in str(caught.value.details)


@pytest.mark.parametrize(
    ("absence", "before", "after"),
    [
        ("before", None, "The function now exists."),
        ("after", "The function previously existed.", None),
    ],
)
def test_behavioral_absence_requires_a_null_absent_side(
    absence: str, before: str | None, after: str | None
) -> None:
    manifest = explanation()
    manifest["items"][0].update({"absence": absence, "before": before, "after": after})
    validate_explanation(evidence(), manifest)

    manifest["items"][0][absence] = "Not actually absent."
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "must declare the absent side" in str(caught.value.details)


def test_absence_is_rejected_for_non_behavioral_items() -> None:
    manifest = explanation()
    manifest["items"][0].update(
        {
            "kind": "structural",
            "statement": "A source declaration is added.",
            "absence": "before",
            "before": None,
        }
    )
    with pytest.raises(ValidationError) as caught:
        validate_explanation(evidence(), manifest)
    assert "Only behavioral" in str(caught.value.details)
