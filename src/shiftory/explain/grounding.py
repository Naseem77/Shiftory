"""Deterministic semantic grounding for explanation claims.

Grounding binds a prose claim to the exact evidence that is allowed to support
it and then evaluates a named predicate over the evidence bytes. It never
interprets natural language, never scores prose, and never proves runtime
behavior. Every predicate is a byte-exact operation over content that
``shiftory.evidence/v1`` already carries.

Each claim declares a ``support_level``:

``verified``
    The engine proved the claim's predicate over bound evidence.
``inferred`` / ``ambiguous``
    The engine proved the claim's *obligations* -- the operands exist, on the
    required side, inside evidence the item owns -- but the predicate itself is
    agent-declared and must state its limits.
``unresolved`` / ``unavailable``
    Nothing is asserted. Support stays bound when present, and limits are
    required.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CLAIM_TYPES = (
    "addition",
    "deletion",
    "graph_relation",
    "non_text_change",
    "source_order",
    "text_absence",
    "text_presence",
    "value_change",
)
SUPPORT_LEVELS = ("verified", "inferred", "ambiguous", "unresolved", "unavailable")
GRAPH_FACT_KINDS = (
    "callee",
    "caller",
    "changed_symbol",
    "definition",
    "enclosing_symbol",
    "importer",
    "static_test",
)
NON_TEXT_UNIT_KINDS = ("binary", "copy", "mode", "rename", "submodule", "unsupported")
SIDES = ("before", "after")

MAX_CLAIMS_PER_ITEM = 32
MAX_SUPPORT_PER_CLAIM = 32
MAX_LITERAL_LENGTH = 512

_LEVEL_STRENGTH = {
    "verified": 4,
    "inferred": 3,
    "ambiguous": 2,
    "unresolved": 1,
    "unavailable": 1,
}
_CONFIDENCE_STRENGTH = {
    "extracted": 4,
    "inferred": 3,
    "ambiguous": 2,
    "unresolved": 1,
    "unavailable": 1,
}
OBLIGATION_LEVELS = frozenset({"verified", "inferred", "ambiguous"})
LITERAL_FIELDS = ("literal", "first", "second", "before_literal", "after_literal", "symbol")

_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "addition": ("literal",),
    "deletion": ("literal",),
    "graph_relation": ("fact_kind", "side", "symbol", "target", "path"),
    "non_text_change": ("unit_kind", "metadata"),
    "source_order": ("side", "first", "second"),
    "text_absence": ("side", "literal"),
    "text_presence": ("side", "literal"),
    "value_change": ("before_literal", "after_literal"),
}
_COMMON_FIELDS = ("id", "type", "support_level", "support", "shared_support", "limits")

_REGION_RANK = {"changed line": 1, "changed lines": 2, "source citation": 3}


@dataclass(frozen=True, slots=True)
class LineRecord:
    id: str
    side: str
    path: str | None
    content: str
    hunk_id: str
    span_id: str | None
    file_index: int


@dataclass(frozen=True, slots=True)
class SpanRecord:
    id: str
    side: str
    path: str | None
    start_line: int
    end_line: int
    line_ids: tuple[str, ...]
    replacement_span_id: str | None
    file_index: int


@dataclass(frozen=True, slots=True)
class HunkRecord:
    id: str
    line_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    file_index: int


@dataclass(frozen=True, slots=True)
class UnitRecord:
    id: str
    kind: str
    line_ids: tuple[str, ...]
    metadata: dict[str, Any]
    paths: tuple[str, ...]
    file_index: int


@dataclass(frozen=True, slots=True)
class CitationRecord:
    id: str
    path: str
    side: str
    start_line: int
    end_line: int
    text: str | None
    omitted: bool
    span_id: str | None
    file_index: int


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    lines: dict[str, LineRecord] = field(default_factory=dict)
    spans: dict[str, SpanRecord] = field(default_factory=dict)
    hunks: dict[str, HunkRecord] = field(default_factory=dict)
    units: dict[str, UnitRecord] = field(default_factory=dict)
    citations: dict[str, CitationRecord] = field(default_factory=dict)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    graph_status: str = "disabled"
    changed_paths: frozenset[str] = frozenset()
    supports: dict[str, Support] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ItemScope:
    """The exact change one explanation item owns."""

    item_id: str
    owned_lines: frozenset[str]
    owned_units: frozenset[str]
    owned_paths: frozenset[str]


@dataclass(frozen=True, slots=True)
class Region:
    """A contiguous, single-side text region resolved from evidence."""

    span_id: str
    side: str
    line_ids: tuple[str, ...]
    text_lines: tuple[str, ...]
    origin: str

    def joined(self) -> str:
        return "\n".join(self.text_lines)


@dataclass(frozen=True, slots=True)
class Support:
    """One resolved support reference bound to one claim."""

    id: str
    kind: str
    line_ids: tuple[str, ...]
    regions: tuple[Region, ...]
    hunk_ids: tuple[str, ...]
    paths: tuple[str, ...]
    unit_id: str | None = None
    fact: dict[str, Any] | None = None
    shared: bool = False


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    item_id: str
    claim_id: str
    type: str
    support_level: str
    proof: str
    limits: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "type": self.type,
            "support_level": self.support_level,
            "proof": self.proof,
            "limits": self.limits,
        }


@dataclass(frozen=True, slots=True)
class GroundingSummary:
    mode: str
    grounded_items: int
    claim_total: int
    level_counts: dict[str, int]
    outcomes: tuple[ClaimOutcome, ...]

    def to_dict(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for outcome in self.outcomes:
            if not items or items[-1]["item_id"] != outcome.item_id:
                items.append({"item_id": outcome.item_id, "claims": []})
            items[-1]["claims"].append(outcome.to_dict())
        return {
            "mode": self.mode,
            "grounded_items": self.grounded_items,
            "claim_total": self.claim_total,
            "verified": self.level_counts["verified"],
            "inferred": self.level_counts["inferred"],
            "ambiguous": self.level_counts["ambiguous"],
            "unresolved": self.level_counts["unresolved"],
            "unavailable": self.level_counts["unavailable"],
            "items": items,
        }


class _Diagnostics:
    """Typed grounding diagnostics appended to the shared validation errors."""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink
        self.count = 0

    def add(self, code: str, path: str, message: str) -> None:
        self.count += 1
        self._sink.append({"code": code, "path": path, "message": message})


def index_evidence(evidence: dict[str, Any]) -> EvidenceIndex:
    """Index the evidence detail needed to evaluate claims.

    Structural integrity is validated by the accounting validator, so malformed
    records are skipped here instead of producing duplicate errors.
    """
    lines: dict[str, LineRecord] = {}
    spans: dict[str, SpanRecord] = {}
    hunks: dict[str, HunkRecord] = {}
    units: dict[str, UnitRecord] = {}
    citations: dict[str, CitationRecord] = {}
    facts: dict[str, dict[str, Any]] = {}
    changed_paths: set[str] = set()
    line_span: dict[str, str] = {}

    for file_index, file in enumerate(_objects(evidence.get("files"))):
        paths: dict[str, str | None] = {
            "before": _text(file.get("old_path")),
            "after": _text(file.get("new_path")),
        }
        changed_paths.update(value for value in paths.values() if value is not None)
        _index_hunks(file, file_index, paths, lines, hunks)
        file_spans = _index_spans(file, file_index, paths, spans, line_span)
        _index_units(file, file_index, paths, hunks, units)
        _index_citations(file, file_index, file_spans, citations)

    graph = evidence.get("graph")
    graph_dict = graph if isinstance(graph, dict) else {}
    for fact in _objects(graph_dict.get("facts")):
        fact_id = _text(fact.get("id"))
        if fact_id is not None:
            facts[fact_id] = fact
    status = graph_dict.get("status")
    resolved_lines = {
        line_id: LineRecord(
            record.id,
            record.side,
            record.path,
            record.content,
            record.hunk_id,
            line_span.get(line_id),
            record.file_index,
        )
        for line_id, record in lines.items()
    }
    return EvidenceIndex(
        resolved_lines,
        spans,
        hunks,
        units,
        citations,
        facts,
        status if isinstance(status, str) else "disabled",
        frozenset(changed_paths),
        _index_supports(resolved_lines, spans, hunks, units, citations, facts),
    )


def _index_hunks(
    file: dict[str, Any],
    file_index: int,
    paths: dict[str, str | None],
    lines: dict[str, LineRecord],
    hunks: dict[str, HunkRecord],
) -> None:
    for hunk in _objects(file.get("hunks")):
        hunk_id = _text(hunk.get("id"))
        if hunk_id is None:
            continue
        hunk_line_ids: list[str] = []
        for line in _objects(hunk.get("lines")):
            line_id = _text(line.get("id"))
            side = line.get("side")
            if line_id is None or not isinstance(side, str) or side not in SIDES:
                continue
            content = line.get("content")
            lines[line_id] = LineRecord(
                line_id,
                side,
                paths[side],
                content if isinstance(content, str) else "",
                hunk_id,
                None,
                file_index,
            )
            hunk_line_ids.append(line_id)
        hunks[hunk_id] = HunkRecord(
            hunk_id, tuple(hunk_line_ids), tuple(_strings(hunk.get("span_ids"))), file_index
        )


def _index_spans(
    file: dict[str, Any],
    file_index: int,
    paths: dict[str, str | None],
    spans: dict[str, SpanRecord],
    line_span: dict[str, str],
) -> dict[tuple[str, int, int], str]:
    geometry: dict[tuple[str, int, int], str] = {}
    for span in _objects(file.get("spans")):
        span_id = _text(span.get("id"))
        side = span.get("side")
        if span_id is None or not isinstance(side, str) or side not in SIDES:
            continue
        line_ids = tuple(_strings(span.get("line_ids")))
        start_line = _integer(span.get("start_line"))
        end_line = _integer(span.get("end_line"))
        spans[span_id] = SpanRecord(
            span_id,
            side,
            paths[side],
            start_line,
            end_line,
            line_ids,
            _text(span.get("replacement_span_id")),
            file_index,
        )
        geometry[(side, start_line, end_line)] = span_id
        for line_id in line_ids:
            line_span[line_id] = span_id
    return geometry


def _index_units(
    file: dict[str, Any],
    file_index: int,
    paths: dict[str, str | None],
    hunks: dict[str, HunkRecord],
    units: dict[str, UnitRecord],
) -> None:
    unit_paths = tuple(sorted({value for value in paths.values() if value is not None}))
    for unit in _objects(file.get("units")):
        unit_id = _text(unit.get("id"))
        kind = _text(unit.get("kind"))
        if unit_id is None or kind is None:
            continue
        metadata = unit.get("metadata")
        units[unit_id] = UnitRecord(
            unit_id,
            kind,
            tuple(
                line_id
                for hunk_id in _strings(unit.get("hunk_ids"))
                for line_id in (hunks[hunk_id].line_ids if hunk_id in hunks else ())
            ),
            metadata if isinstance(metadata, dict) else {},
            unit_paths,
            file_index,
        )


def _index_citations(
    file: dict[str, Any],
    file_index: int,
    file_spans: dict[tuple[str, int, int], str],
    citations: dict[str, CitationRecord],
) -> None:
    for citation in _objects(file.get("citations")):
        citation_id = _text(citation.get("id"))
        citation_path = _text(citation.get("path"))
        side = citation.get("side")
        if citation_id is None or citation_path is None:
            continue
        if not isinstance(side, str) or side not in SIDES:
            continue
        start_line = _integer(citation.get("start_line"))
        end_line = _integer(citation.get("end_line"))
        text = citation.get("text")
        citations[citation_id] = CitationRecord(
            citation_id,
            citation_path,
            side,
            start_line,
            end_line,
            text if isinstance(text, str) else None,
            bool(citation.get("omitted")),
            file_spans.get((side, start_line, end_line)),
            file_index,
        )


def _index_supports(
    lines: dict[str, LineRecord],
    spans: dict[str, SpanRecord],
    hunks: dict[str, HunkRecord],
    units: dict[str, UnitRecord],
    citations: dict[str, CitationRecord],
    facts: dict[str, dict[str, Any]],
) -> dict[str, Support]:
    """Resolve every evidence id to an immutable support exactly once.

    Support resolution is a pure function of an evidence id, so building the
    whole map with the index keeps claim evaluation independent of how many
    claims cite the same span, hunk, or unit. Insertion follows a fixed
    precedence so an id shared by two record kinds resolves deterministically.
    """
    supports: dict[str, Support] = {}
    span_regions: dict[str, Region] = {}
    span_hunks: dict[str, tuple[str, ...]] = {}
    for span_id, span in spans.items():
        present = [line_id for line_id in span.line_ids if line_id in lines]
        span_regions[span_id] = Region(
            span_id,
            span.side,
            span.line_ids,
            tuple(lines[line_id].content for line_id in present),
            "changed lines",
        )
        span_hunks[span_id] = tuple(sorted({lines[line_id].hunk_id for line_id in present}))
    for line_id, line in lines.items():
        line_span = spans.get(line.span_id) if line.span_id else None
        if line_span is None:
            region = Region(line_id, line.side, (line_id,), (line.content,), "changed line")
            supports.setdefault(
                line_id,
                Support(line_id, "line", (line_id,), (region,), (line.hunk_id,), _paths(line.path)),
            )
            continue
        # A changed line is only meaningful inside its contiguous span. Resolving
        # to the whole span keeps every span-scoped predicate, and the proof text
        # that names the span, honest when a claim cites a single line.
        supports.setdefault(
            line_id,
            Support(
                line_id,
                "line",
                line_span.line_ids,
                (span_regions[line_span.id],),
                span_hunks[line_span.id],
                _paths(line.path),
            ),
        )
    for span_id, span in spans.items():
        supports.setdefault(
            span_id,
            Support(
                span_id,
                "span",
                span.line_ids,
                (span_regions[span_id],),
                span_hunks[span_id],
                _paths(span.path),
            ),
        )
    for citation_id, citation in citations.items():
        cited = spans.get(citation.span_id) if citation.span_id else None
        if cited is None:
            supports.setdefault(
                citation_id, Support(citation_id, "citation", (), (), (), (citation.path,))
            )
            continue
        region = (
            span_regions[cited.id]
            if citation.omitted or citation.text is None
            else Region(
                cited.id,
                cited.side,
                cited.line_ids,
                tuple(citation.text.split("\n")),
                "source citation",
            )
        )
        supports.setdefault(
            citation_id,
            Support(
                citation_id,
                "citation",
                cited.line_ids,
                (region,),
                span_hunks[cited.id],
                (citation.path,),
            ),
        )
    for hunk_id, hunk in hunks.items():
        supports.setdefault(
            hunk_id,
            Support(
                hunk_id,
                "hunk",
                hunk.line_ids,
                _span_regions_for(hunk.line_ids, lines, span_regions),
                (hunk_id,),
                _paths_for_lines(hunk.line_ids, lines),
            ),
        )
    for unit_id, unit in units.items():
        supports.setdefault(
            unit_id,
            Support(
                unit_id,
                "unit" if unit.kind == "text" else "non_text_unit",
                unit.line_ids,
                _span_regions_for(unit.line_ids, lines, span_regions),
                _hunks_for(unit.line_ids, lines),
                unit.paths,
                unit_id,
            ),
        )
    for fact_id, fact in facts.items():
        supports.setdefault(
            fact_id,
            Support(fact_id, "fact", (), (), (), _paths(_text(fact.get("path"))), None, fact),
        )
    return supports


def _span_regions_for(
    line_ids: tuple[str, ...],
    lines: dict[str, LineRecord],
    span_regions: dict[str, Region],
) -> tuple[Region, ...]:
    """Collect a coarse reference's span regions in first-appearance order.

    Membership uses a set so a hunk or unit covering many spans stays linear in
    its own changed lines.
    """
    seen: set[str] = set()
    ordered: list[Region] = []
    for line_id in line_ids:
        record = lines.get(line_id)
        if record is None or record.span_id is None or record.span_id in seen:
            continue
        seen.add(record.span_id)
        region = span_regions.get(record.span_id)
        if region is not None:
            ordered.append(region)
    return tuple(ordered)


def item_scopes(
    index: EvidenceIndex,
    owners: dict[str, str],
    item_ids: set[str],
) -> dict[str, ItemScope]:
    """Derive each item's exact owned change from validated coverage owners."""
    owned_lines: dict[str, set[str]] = {item_id: set() for item_id in item_ids}
    owned_units: dict[str, set[str]] = {item_id: set() for item_id in item_ids}
    for evidence_id, owner_id in owners.items():
        if owner_id not in owned_lines:
            continue
        if evidence_id in index.lines:
            owned_lines[owner_id].add(evidence_id)
        elif evidence_id in index.spans:
            owned_lines[owner_id].update(
                line_id for line_id in index.spans[evidence_id].line_ids if line_id in index.lines
            )
        elif evidence_id in index.units:
            owned_units[owner_id].add(evidence_id)
    scopes: dict[str, ItemScope] = {}
    for item_id in sorted(item_ids):
        line_ids = owned_lines[item_id]
        unit_ids = owned_units[item_id]
        item_paths = {
            path
            for line_id in line_ids
            for path in (index.lines[line_id].path,)
            if path is not None
        }
        item_paths.update(path for unit_id in unit_ids for path in index.units[unit_id].paths)
        scopes[item_id] = ItemScope(
            item_id, frozenset(line_ids), frozenset(unit_ids), frozenset(item_paths)
        )
    return scopes


def evaluate_grounding(
    *,
    evidence: dict[str, Any],
    items: list[Any],
    item_ids: set[str],
    owners: dict[str, str],
    require_grounding: bool,
    errors: list[dict[str, Any]],
) -> GroundingSummary:
    """Validate and evaluate every grounding block in an explanation."""
    diagnostics = _Diagnostics(errors)
    index = index_evidence(evidence)
    scopes = item_scopes(index, owners, item_ids)
    declared_owners = _declared_owners(index, owners)
    outcomes: list[ClaimOutcome] = []
    level_counts = dict.fromkeys(SUPPORT_LEVELS, 0)
    grounded_items = 0

    for position, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        path = f"$.items[{position}]"
        claims = _item_claims(raw_item, path, scopes, require_grounding, diagnostics)
        if claims is None:
            continue
        item_id = str(raw_item["id"])
        grounded_items += 1
        item_outcomes = _evaluate_item(
            raw_item, claims, path, index, scopes[item_id], scopes, declared_owners, diagnostics
        )
        for outcome in item_outcomes:
            level_counts[outcome.support_level] += 1
        outcomes.extend(item_outcomes)
    return GroundingSummary(
        "required" if require_grounding else "optional",
        grounded_items,
        len(outcomes),
        level_counts,
        tuple(outcomes),
    )


def _item_claims(
    item: dict[str, Any],
    path: str,
    scopes: dict[str, ItemScope],
    require_grounding: bool,
    diagnostics: _Diagnostics,
) -> list[Any] | None:
    if "grounding" not in item:
        if require_grounding:
            diagnostics.add(
                "grounding.missing",
                f"{path}.grounding",
                "Required grounding is missing; declare at least one claim",
            )
        return None
    grounding = item.get("grounding")
    if not isinstance(grounding, dict):
        diagnostics.add("grounding.claim_shape", f"{path}.grounding", "grounding must be an object")
        return None
    item_id = item.get("id")
    if not isinstance(item_id, str) or item_id not in scopes:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.grounding",
            "Grounding requires a valid, unique item id",
        )
        return None
    claims = grounding.get("claims")
    if set(grounding) != {"claims"} or not isinstance(claims, list) or not claims:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.grounding.claims",
            "grounding must contain only a non-empty claims array",
        )
        return None
    if len(claims) > MAX_CLAIMS_PER_ITEM:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.grounding.claims",
            f"An item may declare at most {MAX_CLAIMS_PER_ITEM} claims",
        )
        return None
    return claims


def _evaluate_item(
    item: dict[str, Any],
    raw_claims: list[Any],
    item_path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    scopes: dict[str, ItemScope],
    declared_owners: dict[str, str],
    diagnostics: _Diagnostics,
) -> list[ClaimOutcome]:
    outcomes: list[ClaimOutcome] = []
    claim_ids: set[str] = set()
    observed_types: set[str] = set()
    observed_sides: set[str] = set()
    weakest = _LEVEL_STRENGTH["verified"]
    failures = 0
    for position, raw_claim in enumerate(raw_claims):
        path = f"{item_path}.grounding.claims[{position}]"
        claim = _claim_shape(raw_claim, path, claim_ids, diagnostics)
        if claim is None:
            failures += 1
            continue
        claim_ids.add(str(claim["id"]))
        supports = _resolve_support(claim, path, index, scope, scopes, declared_owners, diagnostics)
        if supports is None:
            failures += 1
            continue
        outcome = _evaluate_claim(claim, path, index, scope, supports, diagnostics)
        if outcome is None:
            failures += 1
            continue
        weakest = min(weakest, _LEVEL_STRENGTH[outcome.support_level])
        observed_types.add(outcome.type)
        observed_sides.update(_claim_sides(claim, supports))
        outcomes.append(outcome)
    if outcomes and not failures:
        _check_confidence(item, item_path, weakest, diagnostics)
        _check_item_shape(item, item_path, observed_types, observed_sides, diagnostics)
    return outcomes


def _claim_shape(
    raw_claim: Any,
    path: str,
    claim_ids: set[str],
    diagnostics: _Diagnostics,
) -> dict[str, Any] | None:
    if not isinstance(raw_claim, dict):
        diagnostics.add("grounding.claim_shape", path, "A claim must be an object")
        return None
    claim_id = raw_claim.get("id")
    if not isinstance(claim_id, str) or not claim_id.strip():
        diagnostics.add("grounding.claim_shape", f"{path}.id", "A claim id is required")
        return None
    if claim_id in claim_ids:
        diagnostics.add(
            "grounding.duplicate_claim_id",
            f"{path}.id",
            f"Claim id {claim_id!r} is duplicated within this item",
        )
        return None
    claim_type = raw_claim.get("type")
    if not isinstance(claim_type, str) or claim_type not in CLAIM_TYPES:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.type",
            f"Unknown claim type; expected one of {', '.join(CLAIM_TYPES)}",
        )
        return None
    level = raw_claim.get("support_level")
    if not isinstance(level, str) or level not in SUPPORT_LEVELS:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.support_level",
            f"Unknown support level; expected one of {', '.join(SUPPORT_LEVELS)}",
        )
        return None
    if not _limits_valid(raw_claim, level, path, diagnostics):
        return None
    unexpected = sorted(set(raw_claim) - {*_COMMON_FIELDS, *_TYPE_FIELDS[claim_type]})
    if unexpected:
        diagnostics.add(
            "grounding.claim_shape",
            path,
            f"{claim_type!r} claims do not accept: {', '.join(unexpected)}",
        )
        return None
    if not _type_fields_valid(raw_claim, claim_type, path, diagnostics):
        return None
    return raw_claim


def _limits_valid(
    claim: dict[str, Any],
    level: str,
    path: str,
    diagnostics: _Diagnostics,
) -> bool:
    limits = claim.get("limits")
    if limits is not None and (not isinstance(limits, str) or not limits.strip()):
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.limits",
            "limits must be a non-empty string when present",
        )
        return False
    if level != "verified" and limits is None:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.limits",
            f"{level!r} support requires limits describing what is not proven",
        )
        return False
    return True


def _type_fields_valid(
    claim: dict[str, Any],
    claim_type: str,
    path: str,
    diagnostics: _Diagnostics,
) -> bool:
    valid = True
    fields = _TYPE_FIELDS[claim_type]
    for name in LITERAL_FIELDS:
        if name not in fields:
            continue
        value = claim.get(name)
        if not isinstance(value, str) or not value.strip():
            diagnostics.add(
                "grounding.claim_shape", f"{path}.{name}", f"{name} must be a non-empty literal"
            )
            valid = False
        elif len(value) > MAX_LITERAL_LENGTH:
            diagnostics.add(
                "grounding.claim_shape",
                f"{path}.{name}",
                f"{name} must be at most {MAX_LITERAL_LENGTH} characters",
            )
            valid = False
    if "side" in fields and claim.get("side") not in SIDES:
        diagnostics.add("grounding.claim_shape", f"{path}.side", "side must be 'before' or 'after'")
        valid = False
    if claim_type == "graph_relation" and not _graph_fields_valid(claim, path, diagnostics):
        valid = False
    if claim_type == "non_text_change" and not _non_text_fields_valid(claim, path, diagnostics):
        valid = False
    return valid


def _graph_fields_valid(claim: dict[str, Any], path: str, diagnostics: _Diagnostics) -> bool:
    valid = True
    if claim.get("fact_kind") not in GRAPH_FACT_KINDS:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.fact_kind",
            f"fact_kind must be one of {', '.join(GRAPH_FACT_KINDS)}",
        )
        valid = False
    for name in ("target", "path"):
        value = claim.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            diagnostics.add(
                "grounding.claim_shape",
                f"{path}.{name}",
                f"{name} must be a non-empty string when present",
            )
            valid = False
    return valid


def _non_text_fields_valid(claim: dict[str, Any], path: str, diagnostics: _Diagnostics) -> bool:
    valid = True
    if claim.get("unit_kind") not in NON_TEXT_UNIT_KINDS:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.unit_kind",
            f"unit_kind must be one of {', '.join(NON_TEXT_UNIT_KINDS)}",
        )
        valid = False
    metadata = claim.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.metadata",
            "metadata must be an object when present",
        )
        valid = False
    return valid


def _resolve_support(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    scopes: dict[str, ItemScope],
    declared_owners: dict[str, str],
    diagnostics: _Diagnostics,
) -> tuple[Support, ...] | None:
    level = str(claim["support_level"])
    raw_support = claim.get("support", [])
    if not isinstance(raw_support, list):
        diagnostics.add("grounding.claim_shape", f"{path}.support", "support must be an array")
        return None
    if len(raw_support) > MAX_SUPPORT_PER_CLAIM:
        diagnostics.add(
            "grounding.claim_shape",
            f"{path}.support",
            f"A claim may bind at most {MAX_SUPPORT_PER_CLAIM} support references",
        )
        return None
    failed = False
    resolved: list[Support] = []
    seen: set[str] = set()
    for position, value in enumerate(raw_support):
        entry_path = f"{path}.support[{position}]"
        if not isinstance(value, str) or not value:
            diagnostics.add(
                "grounding.claim_shape", entry_path, "A support reference must be a string id"
            )
            failed = True
            continue
        if value in seen:
            continue
        seen.add(value)
        support = _resolve_one(value, index)
        if support is None:
            diagnostics.add(
                "grounding.unknown_support", entry_path, f"Unknown evidence reference {value!r}"
            )
            failed = True
            continue
        if not _is_bound(support, scope, index):
            diagnostics.add(
                "grounding.support_unbound",
                entry_path,
                (
                    f"{value!r} does not resolve into the change owned by item "
                    f"{scope.item_id!r}; declare cross-item evidence in shared_support"
                ),
            )
            failed = True
            continue
        resolved.append(_narrowed(support, scope, index))
    shared = _resolve_shared(
        claim, path, index, scope, scopes, declared_owners, resolved, diagnostics
    )
    if shared is None or failed:
        return None
    if level in OBLIGATION_LEVELS and not resolved:
        diagnostics.add(
            "grounding.support_unbound",
            f"{path}.support",
            f"{level!r} support requires at least one reference owned by this item",
        )
        return None
    return (*resolved, *shared)


def _resolve_shared(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    scopes: dict[str, ItemScope],
    declared_owners: dict[str, str],
    bound: list[Support],
    diagnostics: _Diagnostics,
) -> tuple[Support, ...] | None:
    if "shared_support" not in claim:
        return ()
    raw_shared = claim.get("shared_support")
    if not isinstance(raw_shared, list) or not raw_shared:
        diagnostics.add(
            "grounding.shared_support_invalid",
            f"{path}.shared_support",
            "shared_support must be a non-empty array when present",
        )
        return None
    if len(raw_shared) > MAX_SUPPORT_PER_CLAIM:
        diagnostics.add(
            "grounding.shared_support_invalid",
            f"{path}.shared_support",
            f"A claim may declare at most {MAX_SUPPORT_PER_CLAIM} shared references",
        )
        return None
    local_hunks = {hunk_id for support in bound for hunk_id in support.hunk_ids}
    failed = False
    resolved: list[Support] = []
    for position, entry in enumerate(raw_shared):
        support = _shared_entry(
            entry,
            f"{path}.shared_support[{position}]",
            index,
            scope,
            scopes,
            declared_owners,
            local_hunks,
            diagnostics,
        )
        if support is None:
            failed = True
            continue
        resolved.append(support)
    return None if failed else tuple(resolved)


def _shared_entry(
    entry: Any,
    entry_path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    scopes: dict[str, ItemScope],
    declared_owners: dict[str, str],
    local_hunks: set[str],
    diagnostics: _Diagnostics,
) -> Support | None:
    if not isinstance(entry, dict) or set(entry) != {"evidence_id", "owner_id", "reason"}:
        diagnostics.add(
            "grounding.shared_support_invalid",
            entry_path,
            "shared_support entries require exactly evidence_id, owner_id, and reason",
        )
        return None
    evidence_id, owner_id, reason = entry["evidence_id"], entry["owner_id"], entry["reason"]
    if not all(isinstance(item, str) and item.strip() for item in (evidence_id, owner_id, reason)):
        diagnostics.add(
            "grounding.shared_support_invalid",
            entry_path,
            "shared_support entries require non-empty strings",
        )
        return None
    support = _resolve_one(str(evidence_id), index)
    if support is None:
        diagnostics.add(
            "grounding.unknown_support", entry_path, f"Unknown evidence reference {evidence_id!r}"
        )
        return None
    if owner_id == scope.item_id or owner_id not in scopes:
        diagnostics.add(
            "grounding.shared_support_invalid",
            entry_path,
            f"owner_id must identify a different explanation item, not {owner_id!r}",
        )
        return None
    if _is_bound(support, scope, index):
        diagnostics.add(
            "grounding.shared_support_invalid",
            entry_path,
            f"{evidence_id!r} is owned by this item; declare it in support",
        )
        return None
    actual = _effective_owners(support, declared_owners)
    if actual != {owner_id}:
        diagnostics.add(
            "grounding.shared_support_invalid",
            entry_path,
            (
                f"{evidence_id!r} is owned by {', '.join(sorted(actual)) or 'no single item'}, "
                f"not {owner_id!r}"
            ),
        )
        return None
    if not local_hunks.intersection(support.hunk_ids):
        diagnostics.add(
            "grounding.shared_support_nonlocal",
            entry_path,
            (
                f"{evidence_id!r} shares no textual hunk with this claim's own support, so it "
                "cannot legitimately support the same statement"
            ),
        )
        return None
    return Support(
        support.id,
        support.kind,
        support.line_ids,
        support.regions,
        support.hunk_ids,
        support.paths,
        support.unit_id,
        support.fact,
        True,
    )


def _declared_owners(index: EvidenceIndex, owners: dict[str, str]) -> dict[str, str]:
    resolved = dict(owners)
    for span_id, span in index.spans.items():
        if span_id in resolved:
            continue
        line_owners = {owners[line_id] for line_id in span.line_ids if line_id in owners}
        if len(line_owners) == 1:
            resolved[span_id] = next(iter(line_owners))
    return resolved


def _effective_owners(support: Support, declared_owners: dict[str, str]) -> set[str]:
    if support.unit_id is not None and not support.line_ids:
        owner = declared_owners.get(support.unit_id)
        return {owner} if owner is not None else set()
    return {declared_owners[line_id] for line_id in support.line_ids if line_id in declared_owners}


def _resolve_one(value: str, index: EvidenceIndex) -> Support | None:
    return index.supports.get(value)


def _hunks_for(line_ids: tuple[str, ...], lines: dict[str, LineRecord]) -> tuple[str, ...]:
    return tuple(sorted({lines[line_id].hunk_id for line_id in line_ids if line_id in lines}))


def _paths(value: str | None) -> tuple[str, ...]:
    return (value,) if value else ()


def _paths_for_lines(line_ids: tuple[str, ...], lines: dict[str, LineRecord]) -> tuple[str, ...]:
    found: set[str] = set()
    for line_id in line_ids:
        record = lines.get(line_id)
        if record is not None and record.path is not None:
            found.add(record.path)
    return tuple(sorted(found))


def _narrowed(support: Support, scope: ItemScope, index: EvidenceIndex) -> Support:
    """Drop the parts of a coarse reference the item does not own.

    A textual hunk or text unit only has to *intersect* the item's owned change
    to be usable, so it can also cover spans that belong to another item. Those
    spans must not become claim text, otherwise coarse support would silently
    reach evidence that `shared_support` exists to declare.

    The advertised hunks shrink with the retained lines. A file's text unit spans
    every hunk in that file, so forwarding the original set would turn
    shared-support hunk locality into mere same-file locality.
    """
    if support.kind not in {"hunk", "unit"}:
        return support
    owned = scope.owned_lines
    regions = tuple(
        region for region in support.regions if all(line_id in owned for line_id in region.line_ids)
    )
    line_ids = tuple(line_id for line_id in support.line_ids if line_id in owned)
    return Support(
        support.id,
        support.kind,
        line_ids,
        regions,
        _hunks_for(line_ids, index.lines),
        support.paths,
        support.unit_id,
        support.fact,
        support.shared,
    )


def _is_bound(support: Support, scope: ItemScope, index: EvidenceIndex) -> bool:
    if support.kind in {"line", "span", "citation"}:
        return bool(support.line_ids) and set(support.line_ids) <= scope.owned_lines
    if support.kind in {"hunk", "unit"}:
        return bool(scope.owned_lines.intersection(support.line_ids))
    if support.kind == "non_text_unit":
        return support.id in scope.owned_units
    if support.kind == "fact":
        allowed = scope.owned_paths
        if not scope.owned_lines and not scope.owned_units:
            allowed = index.changed_paths
        return bool(support.paths) and set(support.paths) <= allowed
    return False


def _claim_sides(claim: dict[str, Any], supports: tuple[Support, ...]) -> set[str]:
    claim_type = str(claim["type"])
    if claim_type == "value_change":
        return {"before", "after"}
    if claim_type == "addition":
        return {"after"}
    if claim_type == "deletion":
        return {"before"}
    side = claim.get("side")
    if isinstance(side, str) and side in SIDES:
        return {side}
    return {region.side for support in supports for region in support.regions}


def _regions(supports: tuple[Support, ...], side: str) -> tuple[Region, ...]:
    chosen: dict[str, Region] = {}
    order: list[str] = []
    for support in supports:
        for region in support.regions:
            if region.side != side:
                continue
            existing = chosen.get(region.span_id)
            if existing is None:
                chosen[region.span_id] = region
                order.append(region.span_id)
            elif _REGION_RANK[region.origin] > _REGION_RANK[existing.origin]:
                chosen[region.span_id] = region
    return tuple(chosen[span_id] for span_id in order)


def _cited(regions: tuple[Region, ...]) -> str:
    """Name the exact regions a predicate examined.

    A claim may cite a single changed line, which resolves to its whole
    contiguous span, so the proof always names the span it actually read.
    """
    return ", ".join(sorted(region.span_id for region in regions)) or "no cited region"


def _contains(regions: tuple[Region, ...], literal: str) -> bool:
    return any(literal in region.joined() for region in regions)


def _positions(region: Region, literal: str) -> tuple[tuple[int, int], ...]:
    found: list[tuple[int, int]] = []
    for line_index, line in enumerate(region.text_lines):
        start = line.find(literal)
        while start != -1:
            found.append((line_index, start))
            start = line.find(literal, start + 1)
    return tuple(found)


def _evaluate_claim(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    diagnostics: _Diagnostics,
) -> ClaimOutcome | None:
    claim_type = str(claim["type"])
    level = str(claim["support_level"])
    limits = claim.get("limits")
    before = diagnostics.count
    proof = _CLAIM_EVALUATORS[claim_type](claim, path, index, scope, supports, level, diagnostics)
    if diagnostics.count != before:
        return None
    return ClaimOutcome(
        scope.item_id,
        str(claim["id"]),
        claim_type,
        level,
        proof,
        limits if isinstance(limits, str) else None,
    )


def _text_presence(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del index, scope
    side, literal = str(claim["side"]), str(claim["literal"])
    if level not in OBLIGATION_LEVELS:
        return f"the presence of {literal!r} in the {side} source is not established"
    regions = _regions(supports, side)
    if _require_regions(regions, side, path, diagnostics) and not _contains(regions, literal):
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.literal",
            f"{literal!r} does not appear in the bound {side} evidence",
        )
    return f"the cited {side} source ({_cited(regions)}) contains {literal!r}"


def _text_absence(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del index, scope
    side, literal = str(claim["side"]), str(claim["literal"])
    regions = _regions(supports, side)
    if level in OBLIGATION_LEVELS:
        if not regions:
            diagnostics.add(
                "grounding.absence_unscoped",
                f"{path}.support",
                (
                    f"An absence claim requires cited {side} source regions; absence cannot be "
                    "asserted without a scope"
                ),
            )
        elif level == "verified" and _contains(regions, literal):
            diagnostics.add(
                "grounding.absence_violated",
                f"{path}.literal",
                f"{literal!r} is present in the cited {side} source",
            )
    cited = _cited(regions)
    if level == "verified":
        return f"{literal!r} is absent from the cited {side} source ({cited})"
    if level in OBLIGATION_LEVELS:
        return (
            f"the cited {side} source ({cited}) is bound to this claim; the absence of "
            f"{literal!r} is asserted, not proven"
        )
    return f"the absence of {literal!r} in the {side} source is not established"


def _value_change(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del scope
    before_literal = str(claim["before_literal"])
    after_literal = str(claim["after_literal"])
    generic = (
        f"{before_literal!r} appears in the bound before source and {after_literal!r} in the "
        "bound after source; the replacement itself is asserted, not proven"
    )
    if level not in OBLIGATION_LEVELS:
        return f"the replacement of {before_literal!r} by {after_literal!r} is not established"
    if before_literal == after_literal:
        diagnostics.add(
            "grounding.operand_ambiguous",
            f"{path}.after_literal",
            "A value change requires different before and after literals",
        )
        return generic
    before_regions = _regions(supports, "before")
    after_regions = _regions(supports, "after")
    sided = _require_regions(before_regions, "before", path, diagnostics)
    sided = _require_regions(after_regions, "after", path, diagnostics) and sided
    if not sided:
        return generic
    if not _contains(before_regions, before_literal):
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.before_literal",
            f"{before_literal!r} does not appear in the bound before evidence",
        )
    if not _contains(after_regions, after_literal):
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.after_literal",
            f"{after_literal!r} does not appear in the bound after evidence",
        )
    if level != "verified":
        return generic
    linked = _linked_replacement(
        before_regions, after_regions, before_literal, after_literal, index
    )
    if linked is None:
        diagnostics.add(
            "grounding.replacement_link_missing",
            f"{path}.support",
            (
                "A verified value change requires replacement-linked before and after spans "
                f"where {before_literal!r} appears only in the before span and {after_literal!r} "
                "appears only in the after span"
            ),
        )
        return generic
    return (
        f"replacement span {linked[0].span_id} -> {linked[1].span_id} changes "
        f"{before_literal!r} to {after_literal!r}"
    )


def _linked_replacement(
    before_regions: tuple[Region, ...],
    after_regions: tuple[Region, ...],
    before_literal: str,
    after_literal: str,
    index: EvidenceIndex,
) -> tuple[Region, Region] | None:
    """Find the first replacement-linked pair that proves the change.

    `replacement_span_id` is a function, not a relation, so the candidate after
    span is a lookup rather than a scan over every before/after combination.
    """
    after_by_span = {region.span_id: region for region in after_regions}
    for before_region in before_regions:
        span = index.spans.get(before_region.span_id)
        if span is None or span.replacement_span_id is None:
            continue
        after_region = after_by_span.get(span.replacement_span_id)
        if after_region is None:
            continue
        before_text = before_region.joined()
        after_text = after_region.joined()
        if (
            before_literal in before_text
            and after_literal in after_text
            and before_literal not in after_text
            and after_literal not in before_text
        ):
            return before_region, after_region
    return None


def _addition(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    return _sided_presence(claim, path, index, scope, supports, level, diagnostics, "after")


def _deletion(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    return _sided_presence(claim, path, index, scope, supports, level, diagnostics, "before")


def _sided_presence(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
    side: str,
) -> str:
    del scope
    literal = str(claim["literal"])
    other = "before" if side == "after" else "after"
    verb = "added" if side == "after" else "deleted"
    proof = (
        f"{literal!r} is {verb} and appears in no changed {other} line of the same changed file(s)"
    )
    if level not in OBLIGATION_LEVELS:
        return f"the {verb} text {literal!r} is not established"
    if level != "verified":
        proof = (
            f"{literal!r} appears in the bound {side} source; that it is {verb} rather than "
            "moved or retained is asserted, not proven"
        )
    regions = _regions(supports, side)
    if not _require_regions(regions, side, path, diagnostics):
        return proof
    if not _contains(regions, literal):
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.literal",
            f"{literal!r} does not appear in the bound {side} evidence",
        )
        return proof
    if level != "verified":
        return proof
    residual = _residual_lines(regions, index, other, literal)
    if residual:
        diagnostics.add(
            "grounding.absence_violated",
            f"{path}.literal",
            (
                f"{literal!r} also appears in changed {other} line(s) of the same file "
                f"({', '.join(residual)}), so it is moved or retained rather than {verb}"
            ),
        )
    return proof


def _residual_lines(
    regions: tuple[Region, ...],
    index: EvidenceIndex,
    other: str,
    literal: str,
) -> list[str]:
    """Changed lines on the other side that still carry the literal.

    The scope is every changed file the claim's support touches, not only the
    lines this item owns, so narrow ownership cannot manufacture a verified
    addition or deletion for text that merely moved.
    """
    files = {
        index.spans[region.span_id].file_index
        for region in regions
        if region.span_id in index.spans
    }
    return sorted(
        line_id
        for line_id, record in index.lines.items()
        if record.side == other and record.file_index in files and literal in record.content
    )


def _source_order(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del index, scope
    side, first, second = str(claim["side"]), str(claim["first"]), str(claim["second"])
    generic = f"{first!r} precedes {second!r} in {side} source order"
    if level not in OBLIGATION_LEVELS:
        return f"the {side} source order of {first!r} and {second!r} is not established"
    if first in second or second in first:
        diagnostics.add(
            "grounding.operand_ambiguous",
            f"{path}.second",
            "Ordered operands must not contain one another",
        )
        return generic
    regions = _regions(supports, side)
    if not _require_regions(regions, side, path, diagnostics):
        return generic
    missing = [
        (name, literal)
        for name, literal in (("first", first), ("second", second))
        if not _contains(regions, literal)
    ]
    for name, literal in missing:
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.{name}",
            (
                f"{literal!r} does not appear in the bound {side} evidence; an order claim must "
                "evidence both operations"
            ),
        )
    if missing:
        return generic
    if level != "verified":
        return f"{first!r} and {second!r} both appear in the bound {side} source"
    return _verified_order(regions, side, first, second, path, diagnostics, generic)


def _verified_order(
    regions: tuple[Region, ...],
    side: str,
    first: str,
    second: str,
    path: str,
    diagnostics: _Diagnostics,
    generic: str,
) -> str:
    if len(regions) != 1:
        diagnostics.add(
            "grounding.region_required",
            f"{path}.support",
            (
                "A verified order claim requires exactly one contiguous cited region; found "
                f"{len(regions)}"
            ),
        )
        return generic
    region = regions[0]
    first_positions = _positions(region, first)
    second_positions = _positions(region, second)
    if not first_positions or not second_positions:
        diagnostics.add(
            "grounding.operand_missing",
            f"{path}.support",
            f"Both operands must appear in the single cited region {region.span_id}",
        )
        return generic
    if max(first_positions) >= min(second_positions):
        diagnostics.add(
            "grounding.order_unproven",
            f"{path}.first",
            f"{first!r} does not precede {second!r} everywhere in cited region {region.span_id}",
        )
        return generic
    return (
        f"in {side} source order within {region.span_id}, {first!r} precedes {second!r} "
        "(source order, not execution order)"
    )


def _graph_relation(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del scope
    fact_kind, side, symbol = str(claim["fact_kind"]), str(claim["side"]), str(claim["symbol"])
    target = claim.get("target")
    declared_path = claim.get("path")
    detail = f" -> {target!r}" if isinstance(target, str) else ""
    proof = (
        f"the static graph records a {fact_kind} relation for {symbol!r}{detail} on the "
        f"{side} side (static, not runtime)"
    )
    if level not in OBLIGATION_LEVELS:
        return f"a {fact_kind} relation for {symbol!r} on the {side} side is not established"
    matches = [
        support.fact
        for support in supports
        if support.fact is not None
        and support.fact.get("kind") == fact_kind
        and support.fact.get("side") == side
        and support.fact.get("symbol") == symbol
        and (target is None or support.fact.get("target") == target)
        and (declared_path is None or support.fact.get("path") == declared_path)
    ]
    if not matches:
        _graph_mismatch(index, fact_kind, side, symbol, path, diagnostics)
        return proof
    if level != "verified":
        return (
            f"a {fact_kind} fact for {symbol!r}{detail} on the {side} side is bound to this "
            "claim, but is not strong enough to verify it (static, not runtime)"
        )
    if index.graph_status != "available":
        diagnostics.add(
            "grounding.graph_unavailable",
            f"{path}.support_level",
            f"Graph enrichment is {index.graph_status}, so a graph claim cannot be verified",
        )
    elif all(match.get("confidence") != "extracted" for match in matches):
        diagnostics.add(
            "grounding.graph_fact_mismatch",
            f"{path}.support_level",
            "Only extracted graph facts can verify a claim; the matched facts are not extracted",
        )
    return proof


def _graph_mismatch(
    index: EvidenceIndex,
    fact_kind: str,
    side: str,
    symbol: str,
    path: str,
    diagnostics: _Diagnostics,
) -> None:
    if index.graph_status != "available":
        diagnostics.add(
            "grounding.graph_unavailable",
            f"{path}.support",
            (
                f"Graph enrichment is {index.graph_status}; a graph claim must then use "
                "unresolved or unavailable support"
            ),
        )
        return
    diagnostics.add(
        "grounding.graph_fact_mismatch",
        f"{path}.support",
        f"No supported graph fact matches kind={fact_kind!r}, side={side!r}, symbol={symbol!r}",
    )


def _non_text_change(
    claim: dict[str, Any],
    path: str,
    index: EvidenceIndex,
    scope: ItemScope,
    supports: tuple[Support, ...],
    level: str,
    diagnostics: _Diagnostics,
) -> str:
    del scope
    unit_kind = str(claim["unit_kind"])
    metadata = claim.get("metadata")
    declared: dict[str, Any] = metadata if isinstance(metadata, dict) else {}
    detail = (
        f" with {', '.join(f'{key}={declared[key]!r}' for key in sorted(declared))}"
        if declared
        else ""
    )
    if level not in OBLIGATION_LEVELS:
        return f"the {unit_kind} unit change is not interpreted"
    units = sorted(
        (
            index.units[support.id]
            for support in supports
            if support.kind == "non_text_unit" and support.id in index.units
        ),
        key=lambda unit: unit.id,
    )
    if not units:
        diagnostics.add(
            "grounding.non_text_mismatch",
            f"{path}.support",
            "A non-text claim requires a supported non-text change unit owned by this item",
        )
        return f"the change unit is a {unit_kind} change{detail}"
    matching = [unit for unit in units if unit.kind == unit_kind]
    if not matching:
        diagnostics.add(
            "grounding.non_text_mismatch",
            f"{path}.unit_kind",
            (
                f"No supported unit has kind {unit_kind!r}; supported kinds are "
                f"{', '.join(sorted({unit.kind for unit in units}))}"
            ),
        )
        return f"the change unit is a {unit_kind} change{detail}"
    if level != "verified":
        return (
            f"change unit {matching[0].id} is a {unit_kind} change; any declared metadata is "
            "asserted, not proven"
        )
    # One unit must satisfy the whole claim. Letting different units satisfy
    # different keys would state a combined fact that no unit actually has.
    exact = [unit for unit in matching if _metadata_matches(unit, declared)]
    if not exact:
        diagnostics.add(
            "grounding.non_text_mismatch",
            f"{path}.metadata",
            (
                "No single supported unit has kind "
                f"{unit_kind!r} together with every declared metadata entry: "
                f"{_declared_detail(declared)}"
            ),
        )
        return f"the change unit is a {unit_kind} change{detail}"
    return f"change unit {exact[0].id} is a {unit_kind} change{detail}"


def _metadata_matches(unit: UnitRecord, declared: dict[str, Any]) -> bool:
    """Require every declared entry on one unit, with key presence significant.

    A declared `null` must match a key that is present and null, never a key the
    unit does not carry at all.
    """
    return all(
        key in unit.metadata and unit.metadata[key] == value for key, value in declared.items()
    )


def _declared_detail(declared: dict[str, Any]) -> str:
    return ", ".join(f"{key}={declared[key]!r}" for key in sorted(declared)) or "none"


def _require_regions(
    regions: tuple[Region, ...],
    side: str,
    path: str,
    diagnostics: _Diagnostics,
) -> bool:
    if regions:
        return True
    diagnostics.add(
        "grounding.side_mismatch",
        f"{path}.support",
        f"No bound support resolves to {side} source text",
    )
    return False


def _check_confidence(
    item: dict[str, Any],
    path: str,
    weakest: int,
    diagnostics: _Diagnostics,
) -> None:
    confidence = item.get("confidence")
    if not isinstance(confidence, str) or confidence not in _CONFIDENCE_STRENGTH:
        return
    if _CONFIDENCE_STRENGTH[confidence] > weakest:
        diagnostics.add(
            "grounding.confidence_overstated",
            f"{path}.confidence",
            (
                f"{confidence!r} confidence is stronger than the weakest declared support level "
                "for this item"
            ),
        )


def _check_item_shape(
    item: dict[str, Any],
    path: str,
    observed_types: set[str],
    observed_sides: set[str],
    diagnostics: _Diagnostics,
) -> None:
    if item.get("kind") != "behavioral":
        return
    absence = item.get("absence")
    claims_path = f"{path}.grounding.claims"
    if absence in {"before", "after"}:
        required = "addition" if absence == "before" else "deletion"
        if required not in observed_types:
            diagnostics.add(
                "grounding.item_shape",
                claims_path,
                f"A pure {required} requires at least one {required} claim",
            )
        return
    if "value_change" in observed_types or {"before", "after"} <= observed_sides:
        return
    diagnostics.add(
        "grounding.item_shape",
        claims_path,
        (
            "A before-to-after item requires a value_change claim or grounded claims on both the "
            "before and after sides"
        ),
    )


_ClaimEvaluator = Callable[
    [dict[str, Any], str, EvidenceIndex, ItemScope, tuple[Support, ...], str, _Diagnostics],
    str,
]
_CLAIM_EVALUATORS: dict[str, _ClaimEvaluator] = {
    "addition": _addition,
    "deletion": _deletion,
    "graph_relation": _graph_relation,
    "non_text_change": _non_text_change,
    "source_order": _source_order,
    "text_absence": _text_absence,
    "text_presence": _text_presence,
    "value_change": _value_change,
}


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
