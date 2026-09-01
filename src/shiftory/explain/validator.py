"""Explanation schema, ownership, citation, confidence, and policy validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shiftory.errors import ValidationError
from shiftory.explain.grounding import (
    LITERAL_FIELDS,
    OBLIGATION_LEVELS,
    GroundingSummary,
    evaluate_grounding,
)

_CONFIDENCE = {"extracted", "inferred", "ambiguous", "unresolved", "unavailable"}
_DISALLOWED_FIELDS = {
    "bug_judgment",
    "bug_judgments",
    "finding",
    "findings",
    "recommendation",
    "review",
    "reviews",
    "review_findings",
    "recommendations",
    "recommended_fix",
    "recommended_fixes",
    "risk",
    "risks",
    "risk_rating",
    "risk_score",
    "severity",
    "severities",
    "suggested_fix",
    "suggested_fixes",
    "verdict",
}
_DISALLOWED_KINDS = {
    "bug",
    "finding",
    "recommendation",
    "review",
    "risk",
    "severity",
    "verdict",
}
_QUOTED_OR_CODE = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'")
_UNCERTAINTY_QUALIFIER = re.compile(
    r"\b(?:apparently|appears|likely|perhaps|probably|seems|uncertain)\b",
    re.I,
)
_MODAL_PREDICATE = re.compile(
    r"\bmay\s+(?:not\s+)?(?:"
    r"be|become|cause|change|contain|continue|create|differ|expose|fail|have|include|"
    r"introduce|leak|occur|omit|produce|raise|regress|remain|require|resolve|result|"
    r"return|reveal|run|skip|throw|trigger|use"
    r")\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    line_total: int
    line_owned: int
    span_total: int
    span_owned: int
    hunk_total: int
    hunk_covered: int
    unit_total: int
    unit_covered: int
    citation_count: int
    grounding: GroundingSummary | None = None

    def to_dict(self) -> dict[str, int | float]:
        return {
            "line_total": self.line_total,
            "line_owned": self.line_owned,
            "line_coverage_ratio": _ratio(self.line_owned, self.line_total),
            "span_total": self.span_total,
            "span_owned": self.span_owned,
            "span_coverage_ratio": _ratio(self.span_owned, self.span_total),
            "hunk_total": self.hunk_total,
            "hunk_covered": self.hunk_covered,
            "hunk_coverage_ratio": _ratio(self.hunk_covered, self.hunk_total),
            "unit_total": self.unit_total,
            "unit_covered": self.unit_covered,
            "unit_coverage_ratio": _ratio(self.unit_covered, self.unit_total),
            "citation_count": self.citation_count,
        }


def _ratio(covered: int, total: int) -> float:
    return 1.0 if total == 0 else covered / total


def validate_explanation(
    evidence: dict[str, Any],
    explanation: dict[str, Any],
    *,
    require_grounding: bool = False,
) -> ValidationResult:
    errors: list[dict[str, Any]] = []
    if evidence.get("schema") != "shiftory.evidence/v1":
        errors.append({"path": "$.evidence.schema", "message": "Expected shiftory.evidence/v1"})
    if explanation.get("schema") != "shiftory.explanation/v1":
        errors.append({"path": "$.schema", "message": "Expected shiftory.explanation/v1"})
    items = explanation.get("items")
    if not isinstance(items, list):
        errors.append({"path": "$.items", "message": "items must be an array"})
        items = []
    item_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"path": f"$.items[{index}]", "message": "item must be an object"})
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            errors.append({"path": f"$.items[{index}].id", "message": "item id is required"})
        elif item_id in item_ids:
            errors.append({"path": f"$.items[{index}].id", "message": "item id is duplicated"})
        else:
            item_ids.add(item_id)
        _validate_item(item, index, errors)
    _validate_policy(explanation, items, errors, _source_corpus(evidence))

    ledger = _evidence_ledger(evidence, errors)
    owners_value = explanation.get("coverage_owners")
    if not isinstance(owners_value, list):
        errors.append({"path": "$.coverage_owners", "message": "coverage_owners must be an array"})
        owners_value = []
    owners: dict[str, str] = {}
    duplicates: dict[str, set[str]] = {}
    ownable = ledger["lines"] | ledger["spans"] | ledger["non_text_units"]
    for index, entry in enumerate(owners_value):
        if not isinstance(entry, dict):
            errors.append(
                {"path": f"$.coverage_owners[{index}]", "message": "owner must be an object"}
            )
            continue
        evidence_id, owner_id = entry.get("evidence_id"), entry.get("owner_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append(
                {
                    "path": f"$.coverage_owners[{index}].evidence_id",
                    "message": "evidence_id must be a non-empty string",
                }
            )
            continue
        if evidence_id not in ownable:
            errors.append(
                {
                    "path": f"$.coverage_owners[{index}].evidence_id",
                    "message": f"{evidence_id!r} is not an ownable line, span, or non-text unit",
                }
            )
            continue
        if not isinstance(owner_id, str) or not owner_id:
            errors.append(
                {
                    "path": f"$.coverage_owners[{index}].owner_id",
                    "message": "owner_id must be a non-empty string",
                }
            )
            continue
        if owner_id not in item_ids:
            errors.append(
                {
                    "path": f"$.coverage_owners[{index}].owner_id",
                    "message": f"{owner_id!r} does not identify an explanation item",
                }
            )
        if evidence_id in owners:
            duplicates.setdefault(evidence_id, {owners[evidence_id]}).add(owner_id)
        else:
            owners[evidence_id] = owner_id
    for duplicate, duplicate_owners in sorted(duplicates.items()):
        errors.append(
            {
                "path": "$.coverage_owners",
                "message": (
                    f"{duplicate} has multiple coverage owners: "
                    f"{', '.join(sorted(duplicate_owners))}"
                ),
            }
        )
    required_direct = ledger["lines"] | ledger["non_text_units"]
    missing = required_direct - set(owners)
    if missing:
        errors.append(
            {
                "path": "$.coverage_owners",
                "message": (
                    "Missing direct coverage owners for changed lines or non-text units: "
                    f"{', '.join(sorted(missing))}"
                ),
            }
        )
    effective_span_owners: dict[str, str] = {}
    for span_id, line_ids in ledger["span_lines"].items():
        effective = {owners[line_id] for line_id in line_ids if line_id in owners}
        span_owner = owners.get(span_id)
        if len(effective) > 1:
            errors.append(
                {
                    "path": "$.coverage_owners",
                    "message": (
                        f"Lines in span {span_id} have cross-owner coverage: "
                        f"{', '.join(sorted(effective))}"
                    ),
                }
            )
        elif len(effective) == 1 and all(line_id in owners for line_id in line_ids):
            inherited_owner = next(iter(effective))
            effective_span_owners[span_id] = inherited_owner
            if span_owner is not None and span_owner != inherited_owner:
                errors.append(
                    {
                        "path": "$.coverage_owners",
                        "message": (
                            f"Direct owner {span_owner!r} of span {span_id} differs from "
                            f"its inherited line owner {inherited_owner!r}"
                        ),
                    }
                )
        elif span_owner is not None:
            errors.append(
                {
                    "path": "$.coverage_owners",
                    "message": (
                        f"Span {span_id} cannot use direct owner {span_owner!r} because "
                        "its changed lines do not establish one inherited owner"
                    ),
                }
            )
    citation_count = _validate_citations(items, ledger["references"], errors)
    if not ledger["units"] and items:
        errors.append(
            {
                "path": "$.items",
                "message": (
                    "An empty comparison must use an explicit no-changes report without items"
                ),
            }
        )
    summary = explanation.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append({"path": "$.summary", "message": "A non-empty summary is required"})
    if (
        not ledger["units"]
        and isinstance(summary, str)
        and not re.search(r"\b(?:no changes?|unchanged|identical)\b", summary, re.I)
    ):
        errors.append(
            {"path": "$.summary", "message": "An empty comparison must explicitly state no changes"}
        )
    hunk_covered = sum(
        bool(line_ids) and all(line_id in owners for line_id in line_ids)
        for line_ids in ledger["hunk_lines"].values()
    )
    unit_covered = sum(
        (
            unit_id in owners
            if unit_id in ledger["non_text_units"]
            else bool(ledger["unit_lines"][unit_id])
            and all(line_id in owners for line_id in ledger["unit_lines"][unit_id])
        )
        for unit_id in ledger["units"]
    )
    grounding = (
        evaluate_grounding(
            evidence=evidence,
            items=items,
            item_ids=item_ids,
            owners=owners,
            require_grounding=require_grounding,
            errors=errors,
        )
        if not errors
        else None
    )
    if errors:
        raise ValidationError(
            f"Explanation validation failed with {len(errors)} error(s)",
            details={"errors": errors},
        )
    return ValidationResult(
        len(ledger["lines"]),
        len(ledger["lines"] & set(owners)),
        len(ledger["spans"]),
        len(effective_span_owners),
        len(ledger["hunks"]),
        hunk_covered,
        len(ledger["units"]),
        unit_covered,
        citation_count,
        grounding,
    )


def _validate_item(item: dict[str, Any], index: int, errors: list[dict[str, Any]]) -> None:
    kind = item.get("kind")
    if kind not in {"behavioral", "structural", "observer", "ambiguity", "unresolved"}:
        errors.append({"path": f"$.items[{index}].kind", "message": "Unknown explanation kind"})
    confidence = item.get("confidence")
    if confidence not in _CONFIDENCE:
        errors.append({"path": f"$.items[{index}].confidence", "message": "Unknown confidence"})
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append(
            {"path": f"$.items[{index}].title", "message": "A non-empty title is required"}
        )
    statement = item.get("statement")
    if statement is not None and (not isinstance(statement, str) or not statement.strip()):
        errors.append(
            {
                "path": f"$.items[{index}].statement",
                "message": "statement must be a non-empty string when present",
            }
        )
    if kind == "behavioral":
        before, after, absence = item.get("before"), item.get("after"), item.get("absence")
        before_ok = isinstance(before, str) and bool(before.strip())
        after_ok = isinstance(after, str) and bool(after.strip())
        if absence == "before":
            before_ok = "before" in item and before is None
        elif absence == "after":
            after_ok = "after" in item and after is None
        elif absence is not None:
            errors.append(
                {"path": f"$.items[{index}].absence", "message": "absence must be before or after"}
            )
        if not before_ok or not after_ok:
            errors.append(
                {
                    "path": f"$.items[{index}]",
                    "message": (
                        "Behavioral items require before and after statements; "
                        "pure additions/deletions must declare the absent side"
                    ),
                }
            )
    else:
        if item.get("absence") is not None:
            errors.append(
                {
                    "path": f"$.items[{index}].absence",
                    "message": "Only behavioral additions or deletions may declare absence",
                }
            )
        before_present = "before" in item
        after_present = "after" in item
        if before_present != after_present:
            errors.append(
                {
                    "path": f"$.items[{index}]",
                    "message": "before and after must be provided together",
                }
            )
        elif before_present and (
            not isinstance(item.get("before"), str)
            or not str(item["before"]).strip()
            or not isinstance(item.get("after"), str)
            or not str(item["after"]).strip()
        ):
            errors.append(
                {
                    "path": f"$.items[{index}]",
                    "message": "before and after must be non-empty strings",
                }
            )
    if kind == "ambiguity" and confidence != "ambiguous":
        errors.append(
            {
                "path": f"$.items[{index}].confidence",
                "message": "Ambiguity items must use ambiguous confidence",
            }
        )
    if kind == "unresolved" and confidence not in {"unresolved", "unavailable"}:
        errors.append(
            {
                "path": f"$.items[{index}].confidence",
                "message": "Unresolved items must use unresolved or unavailable confidence",
            }
        )
    if confidence == "ambiguous" and kind != "ambiguity":
        errors.append(
            {
                "path": f"$.items[{index}].kind",
                "message": "Ambiguous confidence must be represented by an ambiguity item",
            }
        )
    if confidence in {"unresolved", "unavailable"} and kind != "unresolved":
        errors.append(
            {
                "path": f"$.items[{index}].kind",
                "message": (
                    "Unresolved or unavailable confidence must be represented by an unresolved item"
                ),
            }
        )
    if confidence == "extracted":
        prose = _QUOTED_OR_CODE.sub(
            "",
            " ".join(
                str(item.get(field, "")) for field in ("statement", "before", "after", "title")
            ),
        )
        if _contains_uncertainty(prose):
            errors.append(
                {
                    "path": f"$.items[{index}].confidence",
                    "message": "Inferred or ambiguous semantics cannot be labeled extracted",
                }
            )


def _contains_uncertainty(value: str) -> bool:
    if _UNCERTAINTY_QUALIFIER.search(value) or re.search(r"\bmight\b", value, re.I):
        return True
    # Lowercase "may" is ordinarily the modal. Capitalized "May" is commonly a
    # month or proper noun, so require a following predicate before treating it
    # as uncertainty.
    return bool(re.search(r"\bmay\b", value) or _MODAL_PREDICATE.search(value))


def _source_corpus(evidence: dict[str, Any]) -> str:
    """Every piece of source text the evidence packet carries.

    A claim value that occurs verbatim here is source-derived, exactly like the
    quoted source the explanation-not-review check already permits elsewhere.
    """
    parts: list[str] = []
    files = evidence.get("files")
    for file in files if isinstance(files, list) else []:
        if not isinstance(file, dict):
            continue
        hunks = file.get("hunks")
        for hunk in hunks if isinstance(hunks, list) else []:
            if not isinstance(hunk, dict):
                continue
            lines = hunk.get("lines")
            for line in lines if isinstance(lines, list) else []:
                if isinstance(line, dict) and isinstance(line.get("content"), str):
                    parts.append(line["content"])
        citations = file.get("citations")
        for citation in citations if isinstance(citations, list) else []:
            if isinstance(citation, dict) and isinstance(citation.get("text"), str):
                parts.append(citation["text"])
    return "\n".join(parts)


def _validate_policy(
    explanation: dict[str, Any],
    items: list[Any],
    errors: list[dict[str, Any]],
    source_corpus: str = "",
) -> None:
    for field in sorted(explanation):
        normalized = field.lower().replace("-", "_")
        if normalized not in _DISALLOWED_FIELDS:
            continue
        errors.append(
            {
                "path": f"$.{field}",
                "message": f"{field!r} is a review/judgment structure, not an explanation field",
            }
        )
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        kind = str(raw_item.get("kind", "")).lower()
        if kind in _DISALLOWED_KINDS:
            errors.append(
                {
                    "path": f"$.items[{index}].kind",
                    "message": f"{kind!r} communicates review or recommendation intent",
                }
            )
        for field in sorted(raw_item):
            normalized = field.lower().replace("-", "_")
            if normalized in _DISALLOWED_FIELDS:
                errors.append(
                    {
                        "path": f"$.items[{index}].{field}",
                        "message": (
                            f"{field!r} is a review/judgment structure, not an explanation field"
                        ),
                    }
                )
        for field in ("title", "statement", "before", "after"):
            value = raw_item.get(field)
            if not isinstance(value, str):
                continue
            unquoted = _QUOTED_OR_CODE.sub("", value)
            _validate_policy_text(unquoted, f"$.items[{index}].{field}", errors)
        _validate_grounding_policy(raw_item, index, errors, source_corpus)
    summary = explanation.get("summary")
    if isinstance(summary, str):
        unquoted = _QUOTED_OR_CODE.sub("", summary)
        _validate_policy_text(unquoted, "$.summary", errors)


def _validate_grounding_policy(
    item: dict[str, Any],
    index: int,
    errors: list[dict[str, Any]],
    source_corpus: str = "",
) -> None:
    grounding = item.get("grounding")
    if not isinstance(grounding, dict):
        return
    claims = grounding.get("claims")
    if not isinstance(claims, list):
        return
    for position, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_path = f"$.items[{index}].grounding.claims[{position}]"
        for field in sorted(claim):
            normalized = field.lower().replace("-", "_")
            if normalized in _DISALLOWED_FIELDS:
                errors.append(
                    {
                        "path": f"{claim_path}.{field}",
                        "message": (
                            f"{field!r} is a review/judgment structure, not an explanation field"
                        ),
                    }
                )
        limits = claim.get("limits")
        if isinstance(limits, str):
            _validate_policy_text(_QUOTED_OR_CODE.sub("", limits), f"{claim_path}.limits", errors)
        _validate_claim_values(claim, claim_path, errors, source_corpus)
        shared = claim.get("shared_support")
        if not isinstance(shared, list):
            continue
        for shared_position, entry in enumerate(shared):
            reason = entry.get("reason") if isinstance(entry, dict) else None
            if isinstance(reason, str):
                _validate_policy_text(
                    _QUOTED_OR_CODE.sub("", reason),
                    f"{claim_path}.shared_support[{shared_position}].reason",
                    errors,
                )


_EVIDENCE_FORCED_FIELDS: dict[str, tuple[str, ...]] = {
    "addition": ("literal",),
    "deletion": ("literal",),
    "graph_relation": ("symbol", "target", "path"),
    "non_text_change": (),
    "source_order": ("first", "second"),
    "text_absence": (),
    "text_presence": ("literal",),
    "value_change": ("before_literal", "after_literal"),
}


def _validate_claim_values(
    claim: dict[str, Any],
    claim_path: str,
    errors: list[dict[str, Any]],
    source_corpus: str,
) -> None:
    """Scan every claim value that is not forced to come from the evidence.

    An operand that an obligation makes match the evidence byte-for-byte is real
    source or graph text, so diff content mentioning words such as "vulnerability"
    must pass. `text_absence` inverts that: a verified absence proves the literal
    is *not* in the cited source, and inferred or ambiguous absence constrains it
    not at all, so its literal is agent prose at every level. Non-text metadata is
    only compared with the unit at `verified`. Anything that occurs verbatim in
    the packet's source text is treated as quoted source and left alone.
    """
    claim_type = claim.get("type")
    level = claim.get("support_level")
    forced: tuple[str, ...] = ()
    if isinstance(claim_type, str) and isinstance(level, str) and level in OBLIGATION_LEVELS:
        forced = _EVIDENCE_FORCED_FIELDS.get(claim_type, ())
    for name in (*LITERAL_FIELDS, "target", "path", "id"):
        if name in forced:
            continue
        value = claim.get(name)
        if isinstance(value, str):
            _scan_claim_value(value, f"{claim_path}.{name}", errors, source_corpus)
    if level == "verified":
        return
    metadata = claim.get("metadata")
    if not isinstance(metadata, dict):
        return
    for key in sorted(metadata):
        value = metadata[key]
        if isinstance(key, str):
            _scan_claim_value(key, f"{claim_path}.metadata", errors, source_corpus)
        if isinstance(value, str):
            _scan_claim_value(value, f"{claim_path}.metadata.{key}", errors, source_corpus)


def _scan_claim_value(
    value: str,
    path: str,
    errors: list[dict[str, Any]],
    source_corpus: str,
) -> None:
    if value in source_corpus:
        return
    _validate_policy_text(_QUOTED_OR_CODE.sub("", value), path, errors)


def _validate_policy_text(
    value: str,
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    if path.endswith(".title") and re.fullmatch(
        r"\s*(?:bugs?|defects?|vulnerabilit(?:y|ies)|security flaws?)\s*[.!:]?\s*",
        value,
        re.I,
    ):
        errors.append(
            {
                "path": path,
                "message": (
                    "Disallowed defect or security finding; "
                    "describe before-to-after behavior instead"
                ),
            }
        )
    patterns = (
        (
            r"^\s*(?:(?:code\s+)?review(?:\s+findings?)?|findings?|recommendations?|"
            r"severity|risk rating|bug judgment)\s*(?::|$)",
            "review-style heading",
        ),
        (
            r"^\s*(?:critical|high|medium|low)\s+(?:severity|risk)\s*(?::|$)|"
            r"\b(?:this(?: change)?|the change|the patch|the implementation|"
            r"the finding|the issue|it)\s+(?:is|has|carries|presents|poses)\s+"
            r"(?:a\s+)?(?:critical|high|medium|low)\s+(?:severity|risk)\b",
            "severity or risk judgment",
        ),
        (
            r"\b(?:severity|risk)\s+(?:is|was|rated|rating)\s+"
            r"(?:critical|high|medium|low)\b",
            "severity or risk judgment",
        ),
        (
            r"\b(?:this(?: change)?|the change|the patch|the implementation)\s+"
            r"(?:is|introduces|causes|contains|creates)\s+(?:a\s+)?"
            r"(?:bug|defect|vulnerability|security flaw|credential exposure)\b",
            "bug-finding claim",
        ),
        (
            r"\b(?:bugs?|defects?|vulnerabilit(?:y|ies)|security flaws?)\s+"
            r"(?:(?:is|are|was|were)|(?:will|would)\s+be|(?:has|have|had)\s+been)\s+"
            r"(?:added|caused|created|introduced|presented|triggered)\b",
            "passive defect or security claim",
        ),
        (
            r"\b(?:(?:a|an|the)\s+(?:bug|defect|vulnerability|security flaw)|"
            r"(?:the\s+)?(?:bugs|defects|vulnerabilities|security flaws))\s+"
            r"(?:(?:was|were)|(?:has|have)\s+been)\s+"
            r"(?:detected|discovered|found)\b",
            "passive defect or security claim",
        ),
        (
            r"(?:^\s*|\b(?:a|an|the)\s+)"
            r"(?:[\w-]+\s+){0,4}"
            r"(?:bug|defect|vulnerability|security flaw)\s+"
            r"(?:allows?|bypasses?|causes?|corrupts?|discloses?|enables?|exists?|"
            r"exposes?|leaks?|loses?|permits?|remains?|reveals?)\b",
            "defect or security finding",
        ),
        (
            r"^\s*(?:[\w/-]+\s+){1,5}"
            r"(?:bug|defect|vulnerability|security flaw)\s*[.!]?\s*$",
            "defect or security finding",
        ),
        (
            r"\b(?:this(?: change)?|the change|the patch|the implementation)\s+"
            r"(?:discloses?|exposes?|leaks?|reveals?)\s+"
            r"(?:credentials?|passwords?|secrets?|tokens?|private data|sensitive data)\b",
            "security finding",
        ),
        (
            r"\b(?:i|we)\s+(?:recommend|suggest)\b|"
            r"\b(?:recommend|suggest)\s+(?:that|changing|fixing|using|avoiding|replacing)\b",
            "recommendation",
        ),
        (
            r"\b(?:should|must|needs?\s+to)\s+(?:be\s+)?"
            r"(?:changed|fixed|avoided|replaced|rewritten|removed)\b",
            "recommended fix",
        ),
        (
            r"\b(?:code\s+)?review\s+(?:finds?|found|identifies|identified|flags?|flagged)\b",
            "review finding",
        ),
    )
    for pattern, intent in patterns:
        if re.search(pattern, value, re.I):
            errors.append(
                {
                    "path": path,
                    "message": f"Disallowed {intent}; describe before-to-after behavior instead",
                }
            )


def _evidence_ledger(
    evidence: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    lines: set[str] = set()
    spans: set[str] = set()
    hunks: set[str] = set()
    units: set[str] = set()
    non_text_units: set[str] = set()
    span_lines: dict[str, tuple[str, ...]] = {}
    hunk_lines: dict[str, tuple[str, ...]] = {}
    unit_lines: dict[str, tuple[str, ...]] = {}
    references: set[str] = set()
    hunk_by_id: dict[str, tuple[str, ...]] = {}
    hunk_spans: dict[str, tuple[str, ...]] = {}
    hunk_paths: dict[str, str] = {}
    identities: dict[str, tuple[str, str]] = {}
    span_paths: dict[str, str] = {}
    hunk_unit_count: dict[str, int] = {}

    def add_identity(value: Any, path: str, kind: str) -> str | None:
        if not isinstance(value, str) or not value:
            errors.append({"path": path, "message": f"{kind} id must be a non-empty string"})
            return None
        if value in identities:
            first_kind, first_path = identities[value]
            errors.append(
                {
                    "path": path,
                    "message": (
                        f"Duplicate evidence id {value!r} for {kind}; "
                        f"already used by {first_kind} at {first_path}"
                    ),
                }
            )
            return None
        identities[value] = (kind, path)
        return value

    files = evidence.get("files", [])
    if not isinstance(files, list):
        errors.append({"path": "$.evidence.files", "message": "files must be an array"})
        files = []
    for file_index, file in enumerate(files):
        if not isinstance(file, dict):
            errors.append(
                {"path": f"$.evidence.files[{file_index}]", "message": "file must be an object"}
            )
            continue
        file_path = f"$.evidence.files[{file_index}]"
        file_line_ids: set[str] = set()
        file_hunk_ids: set[str] = set()
        file_span_ids: set[str] = set()
        file_hunks = file.get("hunks", [])
        if not isinstance(file_hunks, list):
            errors.append({"path": f"{file_path}.hunks", "message": "hunks must be an array"})
            file_hunks = []
        for hunk_index, hunk in enumerate(file_hunks):
            path = f"{file_path}.hunks[{hunk_index}]"
            if not isinstance(hunk, dict):
                errors.append({"path": path, "message": "hunk must be an object"})
                continue
            hunk_id = add_identity(hunk.get("id"), f"{path}.id", "hunk")
            if hunk_id is None:
                continue
            raw_lines = hunk.get("lines", [])
            if not isinstance(raw_lines, list) or not raw_lines:
                errors.append({"path": f"{path}.lines", "message": "hunk lines must be non-empty"})
                raw_lines = []
            line_ids_list: list[str] = []
            for line_index, line in enumerate(raw_lines):
                line_path = f"{path}.lines[{line_index}]"
                if not isinstance(line, dict):
                    errors.append({"path": line_path, "message": "changed line must be an object"})
                    continue
                line_id = add_identity(line.get("id"), f"{line_path}.id", "changed line")
                if line_id is not None:
                    line_ids_list.append(line_id)
            line_ids = tuple(line_ids_list)
            raw_span_ids = hunk.get("span_ids", [])
            if not isinstance(raw_span_ids, list) or any(
                not isinstance(value, str) for value in raw_span_ids
            ):
                errors.append(
                    {"path": f"{path}.span_ids", "message": "span_ids must be an array of strings"}
                )
                raw_span_ids = []
            span_ids = tuple(raw_span_ids)
            if len(set(span_ids)) != len(span_ids):
                errors.append(
                    {"path": f"{path}.span_ids", "message": "hunk contains duplicate span ids"}
                )
            hunks.add(hunk_id)
            lines.update(line_ids)
            file_hunk_ids.add(hunk_id)
            file_line_ids.update(line_ids)
            hunk_lines[hunk_id] = line_ids
            hunk_by_id[hunk_id] = line_ids
            hunk_spans[hunk_id] = span_ids
            hunk_paths[hunk_id] = path
        file_spans = file.get("spans", [])
        if not isinstance(file_spans, list):
            errors.append({"path": f"{file_path}.spans", "message": "spans must be an array"})
            file_spans = []
        for span_index, span in enumerate(file_spans):
            path = f"{file_path}.spans[{span_index}]"
            if not isinstance(span, dict):
                errors.append({"path": path, "message": "span must be an object"})
                continue
            span_id = add_identity(span.get("id"), f"{path}.id", "span")
            if span_id is None:
                continue
            raw_line_ids = span.get("line_ids", [])
            if not isinstance(raw_line_ids, list) or not raw_line_ids:
                errors.append(
                    {"path": f"{path}.line_ids", "message": "span line_ids must be non-empty"}
                )
                raw_line_ids = []
            line_ids = tuple(value for value in raw_line_ids if isinstance(value, str))
            if len(line_ids) != len(raw_line_ids):
                errors.append(
                    {
                        "path": f"{path}.line_ids",
                        "message": "span line_ids must contain only strings",
                    }
                )
            if len(set(line_ids)) != len(line_ids):
                errors.append(
                    {"path": f"{path}.line_ids", "message": "span contains duplicate line ids"}
                )
            unknown_lines = sorted(set(line_ids) - file_line_ids)
            if unknown_lines:
                errors.append(
                    {
                        "path": f"{path}.line_ids",
                        "message": (
                            f"Unknown changed lines in this file: {', '.join(unknown_lines)}"
                        ),
                    }
                )
            spans.add(span_id)
            file_span_ids.add(span_id)
            span_lines[span_id] = line_ids
            span_paths[span_id] = path
        span_hunk_count = dict.fromkeys(file_span_ids, 0)
        for hunk_id in file_hunk_ids:
            unknown_spans = sorted(set(hunk_spans[hunk_id]) - file_span_ids)
            if unknown_spans:
                errors.append(
                    {
                        "path": f"{hunk_paths[hunk_id]}.span_ids",
                        "message": f"Unknown spans in this file: {', '.join(unknown_spans)}",
                    }
                )
            referenced_lines: list[str] = []
            for span_id in hunk_spans[hunk_id]:
                if span_id in span_hunk_count:
                    span_hunk_count[span_id] += 1
                    referenced_lines.extend(span_lines[span_id])
            if set(referenced_lines) != set(hunk_lines[hunk_id]) or len(referenced_lines) != len(
                hunk_lines[hunk_id]
            ):
                errors.append(
                    {
                        "path": f"{hunk_paths[hunk_id]}.span_ids",
                        "message": f"Hunk {hunk_id} does not exactly own its changed lines",
                    }
                )
        invalid_span_hunks = sorted(
            f"{span_id} ({count} hunks)" for span_id, count in span_hunk_count.items() if count != 1
        )
        if invalid_span_hunks:
            errors.append(
                {
                    "path": f"{file_path}.hunks[*].span_ids",
                    "message": (
                        "Every span must belong to exactly one hunk: "
                        f"{', '.join(invalid_span_hunks)}"
                    ),
                }
            )
        file_units = file.get("units", [])
        if not isinstance(file_units, list):
            errors.append({"path": f"{file_path}.units", "message": "units must be an array"})
            file_units = []
        for unit_index, unit in enumerate(file_units):
            path = f"{file_path}.units[{unit_index}]"
            if not isinstance(unit, dict):
                errors.append({"path": path, "message": "unit must be an object"})
                continue
            unit_id = add_identity(unit.get("id"), f"{path}.id", "change unit")
            if unit_id is None:
                continue
            units.add(unit_id)
            kind = unit.get("kind")
            raw_hunk_ids = unit.get("hunk_ids", [])
            if not isinstance(raw_hunk_ids, list) or any(
                not isinstance(value, str) for value in raw_hunk_ids
            ):
                errors.append(
                    {"path": f"{path}.hunk_ids", "message": "hunk_ids must be an array of strings"}
                )
                raw_hunk_ids = []
            hunk_ids = tuple(raw_hunk_ids)
            unknown_hunks = sorted(set(hunk_ids) - file_hunk_ids)
            if unknown_hunks:
                errors.append(
                    {
                        "path": f"{path}.hunk_ids",
                        "message": f"Unknown text hunks: {', '.join(unknown_hunks)}",
                    }
                )
            if kind == "text":
                if not hunk_ids:
                    errors.append(
                        {
                            "path": f"{path}.hunk_ids",
                            "message": "A text unit must contain at least one hunk",
                        }
                    )
                for hunk_id in hunk_ids:
                    if hunk_id in hunk_by_id:
                        hunk_unit_count[hunk_id] = hunk_unit_count.get(hunk_id, 0) + 1
                unit_lines[unit_id] = tuple(
                    line_id for hunk_id in hunk_ids for line_id in hunk_by_id.get(hunk_id, ())
                )
            else:
                if kind not in {"binary", "mode", "rename", "copy", "submodule", "unsupported"}:
                    errors.append({"path": f"{path}.kind", "message": "Unknown change unit kind"})
                if hunk_ids:
                    errors.append(
                        {
                            "path": f"{path}.hunk_ids",
                            "message": "A non-text unit cannot own text hunks",
                        }
                    )
                non_text_units.add(unit_id)
                unit_lines[unit_id] = ()
        file_citations = file.get("citations", [])
        if not isinstance(file_citations, list):
            errors.append(
                {"path": f"{file_path}.citations", "message": "citations must be an array"}
            )
            file_citations = []
        for citation_index, citation in enumerate(file_citations):
            path = f"{file_path}.citations[{citation_index}]"
            if not isinstance(citation, dict):
                errors.append({"path": path, "message": "source citation must be an object"})
                continue
            citation_id = add_identity(citation.get("id"), f"{path}.id", "source citation")
            if citation_id is not None:
                references.add(citation_id)
    graph = evidence.get("graph", {})
    if not isinstance(graph, dict):
        errors.append({"path": "$.evidence.graph", "message": "graph must be an object"})
        graph = {}
    facts = graph.get("facts", [])
    if not isinstance(facts, list):
        errors.append({"path": "$.evidence.graph.facts", "message": "facts must be an array"})
        facts = []
    for fact_index, fact in enumerate(facts):
        path = f"$.evidence.graph.facts[{fact_index}]"
        if not isinstance(fact, dict):
            errors.append({"path": path, "message": "graph fact must be an object"})
            continue
        fact_id = add_identity(fact.get("id"), f"{path}.id", "graph fact")
        if fact_id is not None:
            references.add(fact_id)
    references.update(lines | spans | hunks | units)
    line_span_count = dict.fromkeys(lines, 0)
    for span_id, line_ids in span_lines.items():
        unknown_lines = sorted(set(line_ids) - lines)
        if unknown_lines:
            errors.append(
                {
                    "path": f"{span_paths[span_id]}.line_ids",
                    "message": f"Unknown changed lines: {', '.join(unknown_lines)}",
                }
            )
        for line_id in line_ids:
            if line_id in line_span_count:
                line_span_count[line_id] += 1
    invalid_line_spans = sorted(
        f"{line_id} ({count} spans)" for line_id, count in line_span_count.items() if count != 1
    )
    if invalid_line_spans:
        errors.append(
            {
                "path": "$.evidence.files[*].spans",
                "message": (
                    "Every changed line must belong to exactly one span: "
                    f"{', '.join(invalid_line_spans)}"
                ),
            }
        )
    invalid_hunk_units = sorted(
        f"{hunk_id} ({hunk_unit_count.get(hunk_id, 0)} units)"
        for hunk_id in hunks
        if hunk_unit_count.get(hunk_id, 0) != 1
    )
    if invalid_hunk_units:
        errors.append(
            {
                "path": "$.evidence.files[*].units",
                "message": (
                    "Every text hunk must belong to exactly one text unit: "
                    f"{', '.join(invalid_hunk_units)}"
                ),
            }
        )
    return {
        "lines": lines,
        "spans": spans,
        "hunks": hunks,
        "units": units,
        "non_text_units": non_text_units,
        "span_lines": span_lines,
        "hunk_lines": hunk_lines,
        "unit_lines": unit_lines,
        "references": references,
    }


def _validate_citations(
    items: list[Any],
    references: set[str],
    errors: list[dict[str, Any]],
) -> int:
    count = 0
    for item_index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        citations = raw_item.get("citations", [])
        if not isinstance(citations, list):
            errors.append(
                {
                    "path": f"$.items[{item_index}].citations",
                    "message": "citations must be an array",
                }
            )
            continue
        for citation_index, citation in enumerate(citations):
            count += 1
            path = f"$.items[{item_index}].citations[{citation_index}]"
            reference: Any
            if isinstance(citation, str):
                reference = citation
            elif isinstance(citation, dict) and set(citation) == {"id"}:
                reference = citation.get("id")
            else:
                errors.append(
                    {
                        "path": path,
                        "message": (
                            "A citation must be an evidence id or an object containing only id"
                        ),
                    }
                )
                continue
            if not isinstance(reference, str) or not reference:
                errors.append({"path": path, "message": "Citation id must be a non-empty string"})
                continue
            if reference not in references:
                errors.append(
                    {
                        "path": path,
                        "message": f"Unknown evidence reference {reference!r}",
                    }
                )
    return count
