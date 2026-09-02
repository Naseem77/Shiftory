"""Runtime validation, structural invariants, and bounded caps.

Every JSON document this benchmark layer reads or writes goes through this
module: a duplicate-key-rejecting loader, JSON Schema validation against the
schemas in ``schemas/``, and code-checkable structural invariants (excerpt
hashes, audit-coverage consistency, invalid-candidate exclusivity, size/count
caps).

Read the module docstring in ``benchmarks/agent_quality/__init__.py`` first:
these checks prove that claims point at real candidate text and that an
auditor asserted their decomposition was complete. They cannot prove that no
semantic proposition was missed -- that remains a human/agent judgment call,
recorded but not verified by this code.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from functools import cache
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Bounded caps (Delta 6 / point 6): every JSON document and raw byte stream this
# layer touches is capped so that no case, rubric, capture, or evaluation can grow
# without limit.
MAX_ITEMS_PER_EXPLANATION = 40
MAX_CLAIMS_PER_EVALUATION = 200
MAX_REQUIRED_FACTS = 50
MAX_TEXT_FIELD_CHARS = 2000
MAX_JSON_DOC_BYTES = 200_000
MAX_HISTORY_BYTES = 100 * 1024
MAX_RAW_RESPONSE_BYTES = 256 * 1024
MAX_HEURISTIC_ALIASES = 20
MAX_ALIAS_CHARS = 80

MATERIAL_ITEM_FIELDS = ("title", "statement", "before", "after")


class AgentQualityError(Exception):
    """Raised for any schema, structural-invariant, or cap violation."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise AgentQualityError(f"Duplicate JSON key {key!r}")
        seen[key] = value
    return seen


def parse_json_text(text: str) -> Any:
    """Parse a JSON string with duplicate-key rejection (no byte-size cap;
    callers that read from bounded sources, such as a capped subprocess
    response, have already enforced their own size limit)."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise AgentQualityError(f"not valid JSON: {error}") from error


def load_json_strict(path: Path, *, max_bytes: int = MAX_JSON_DOC_BYTES) -> Any:
    """Load JSON with duplicate-key rejection and a byte-size cap."""
    if not path.is_file():
        raise AgentQualityError(f"{path} does not exist or is not a regular file")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise AgentQualityError(f"{path} is {len(raw)} bytes, exceeding the {max_bytes}-byte cap")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AgentQualityError(f"{path} is not valid UTF-8") from error
    try:
        return parse_json_text(text)
    except AgentQualityError as error:
        raise AgentQualityError(f"{path} is {error}") from error


def check_file_size(path: Path, max_bytes: int, label: str) -> None:
    size = path.stat().st_size
    if size > max_bytes:
        raise AgentQualityError(
            f"{label} {path} is {size} bytes, exceeding the {max_bytes}-byte cap"
        )


@cache
def _load_schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], load_json_strict(SCHEMA_DIR / f"{name}.schema.json"))


def validate_against_schema(value: dict[str, Any], name: str) -> None:
    """Validate ``value`` against ``schemas/<name>.schema.json``, fail closed."""
    schema = _load_schema(name)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        rendered = "; ".join(
            "$"
            + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path
            )
            + f": {error.message}"
            for error in errors
        )
        raise AgentQualityError(f"{name} does not match its schema: {rendered}")


def safe_case_dir(base: Path, case_id: str) -> Path:
    """Resolve ``case_id`` under ``base``, rejecting invalid ids and path/symlink escapes."""
    if not CASE_ID_RE.fullmatch(case_id):
        raise AgentQualityError(f"Invalid case id {case_id!r}")
    base_resolved = base.resolve(strict=True)
    candidate = (base_resolved / case_id).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as error:
        raise AgentQualityError(f"Case id {case_id!r} escapes {base_resolved}") from error
    return candidate


def _pointer_text(explanation: dict[str, Any], field: str) -> str:
    parts = field.strip("/").split("/")
    node: Any = explanation
    for part in parts:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as error:
                raise AgentQualityError(f"Cannot resolve field pointer {field!r}") from error
        elif isinstance(node, dict):
            if part not in node:
                raise AgentQualityError(f"Cannot resolve field pointer {field!r}")
            node = node[part]
        else:
            raise AgentQualityError(f"Cannot resolve field pointer {field!r}")
    if not isinstance(node, str):
        raise AgentQualityError(f"Field pointer {field!r} does not resolve to a string")
    return node


def material_fields(explanation: dict[str, Any]) -> list[str]:
    """Every non-empty material text field a candidate explanation contains."""
    fields: list[str] = []
    summary = explanation.get("summary")
    if isinstance(summary, str) and summary.strip():
        fields.append("/summary")
    items = explanation.get("items", [])
    if not isinstance(items, list):
        return fields
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for name in MATERIAL_ITEM_FIELDS:
            value = item.get(name)
            if isinstance(value, str) and value.strip():
                fields.append(f"/items/{index}/{name}")
    return fields


def check_explanation_caps(explanation: dict[str, Any]) -> None:
    items = explanation.get("items", [])
    if isinstance(items, list) and len(items) > MAX_ITEMS_PER_EXPLANATION:
        raise AgentQualityError(
            f"explanation has {len(items)} items, exceeding cap {MAX_ITEMS_PER_EXPLANATION}"
        )
    for field in material_fields(explanation):
        text = _pointer_text(explanation, field)
        if len(text) > MAX_TEXT_FIELD_CHARS:
            raise AgentQualityError(f"field {field!r} exceeds {MAX_TEXT_FIELD_CHARS} characters")


def check_claim_anchor(explanation: dict[str, Any], claim: dict[str, Any]) -> None:
    """Recompute a claim's excerpt hash against the real candidate text; fail if it drifted."""
    text = _pointer_text(explanation, claim["field"])
    start, end = claim["start"], claim["end"]
    if not (0 <= start < end <= len(text)):
        raise AgentQualityError(
            f"Claim {claim['claim_id']!r} has an out-of-range span for field {claim['field']!r}"
        )
    digest = sha256_text(text[start:end])
    if digest != claim["excerpt_sha256"]:
        raise AgentQualityError(
            f"Claim {claim['claim_id']!r} excerpt_sha256 does not match the candidate text "
            f"at {claim['field']!r}[{start}:{end}]"
        )


def check_claim_overlaps(claims: list[dict[str, Any]]) -> None:
    by_field: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        by_field.setdefault(claim["field"], []).append(claim)
    for field_claims in by_field.values():
        for first, second in itertools.combinations(field_claims, 2):
            overlaps = first["start"] < second["end"] and second["start"] < first["end"]
            if not overlaps:
                continue
            first_ack = second["claim_id"] in first.get("overlaps_with", [])
            second_ack = first["claim_id"] in second.get("overlaps_with", [])
            if not (first_ack and second_ack):
                raise AgentQualityError(
                    f"Claims {first['claim_id']!r} and {second['claim_id']!r} overlap in "
                    f"{first['field']!r} without mutually declaring overlaps_with"
                )


def check_audit_coverage(
    explanation: dict[str, Any],
    claims: list[dict[str, Any]],
    audit_coverage: list[dict[str, Any]],
) -> None:
    """Verify every material field has exactly one coverage attestation whose claim_ids
    match reality. This proves the attestations are internally consistent; it cannot
    prove the auditor actually noticed every proposition in the text (see module docs).
    """
    required_fields = set(material_fields(explanation))
    claims_by_field: dict[str, set[str]] = {}
    for claim in claims:
        claims_by_field.setdefault(claim["field"], set()).add(claim["claim_id"])

    covered_fields: set[str] = set()
    for entry in audit_coverage:
        field = entry["field"]
        if field in covered_fields:
            raise AgentQualityError(f"Duplicate audit_coverage attestation for {field!r}")
        covered_fields.add(field)
        recorded_ids = set(entry.get("claim_ids", []))
        actual_ids = claims_by_field.get(field, set())
        if recorded_ids != actual_ids:
            raise AgentQualityError(
                f"audit_coverage for {field!r} lists claim ids {sorted(recorded_ids)} but the "
                f"actual claims for that field are {sorted(actual_ids)}"
            )
        if not recorded_ids and not entry.get("non_claim_rationale"):
            raise AgentQualityError(
                f"audit_coverage for {field!r} has no claims and no non_claim_rationale"
            )

    missing = required_fields - covered_fields
    if missing:
        raise AgentQualityError(f"Missing audit_coverage attestation for fields: {sorted(missing)}")
    extra_claim_fields = set(claims_by_field) - required_fields
    if extra_claim_fields:
        raise AgentQualityError(
            f"Claims reference fields with no material text: {sorted(extra_claim_fields)}"
        )


def check_claim_fact_references(claims: list[dict[str, Any]], required_fact_ids: set[str]) -> None:
    for claim in claims:
        fact_id = claim.get("maps_to_required_fact_id")
        if fact_id is not None and fact_id not in required_fact_ids:
            raise AgentQualityError(
                f"Claim {claim['claim_id']!r} maps to unknown required fact {fact_id!r}"
            )


def check_invalid_candidate_exclusivity(evaluation: dict[str, Any]) -> None:
    invalid = evaluation.get("invalid_candidate")
    claims = evaluation.get("claims") or []
    coverage = evaluation.get("audit_coverage") or []
    explanation_sha = evaluation.get("explanation_sha256")
    if invalid is not None:
        if claims or coverage or explanation_sha is not None:
            raise AgentQualityError(
                "invalid_candidate must not co-occur with claims/audit_coverage/explanation_sha256"
            )
    elif explanation_sha is None:
        raise AgentQualityError("A usable candidate evaluation requires explanation_sha256")


def validate_candidate_evaluation(
    explanation: dict[str, Any] | None,
    evaluation: dict[str, Any],
    rubric: dict[str, Any],
) -> None:
    """Full structural validation of one candidate-evaluation-v1 record.

    Raises AgentQualityError on any schema or invariant violation. Does not, and
    cannot, validate that the underlying claim verdicts are semantically correct --
    only that the record is internally consistent and anchored to real text.
    """
    validate_against_schema(evaluation, "candidate-evaluation-v1")
    check_invalid_candidate_exclusivity(evaluation)
    if evaluation.get("invalid_candidate") is not None:
        return
    if explanation is None:
        raise AgentQualityError("A usable candidate evaluation requires its explanation document")
    check_explanation_caps(explanation)
    claims = evaluation.get("claims", [])
    if len(claims) > MAX_CLAIMS_PER_EVALUATION:
        raise AgentQualityError(
            f"evaluation has {len(claims)} claims, exceeding cap {MAX_CLAIMS_PER_EVALUATION}"
        )
    for claim in claims:
        validate_against_schema(claim, "claim-record-v1")
        if claim["end"] <= claim["start"]:
            raise AgentQualityError(f"Claim {claim['claim_id']!r} has end <= start")
        check_claim_anchor(explanation, claim)
    check_claim_overlaps(claims)
    required_fact_ids = {fact["id"] for fact in rubric["required_facts"]}
    check_claim_fact_references(claims, required_fact_ids)
    check_audit_coverage(explanation, claims, evaluation.get("audit_coverage", []))


def validate_invalidated_capture(case_root: Path, config_id: str) -> dict[str, Any]:
    """Full structural validation of one withdrawn (answer-key-leak) capture
    archived under ``benchmarks/agent_quality/invalidated/<case_id>/``.

    Loads ``<config_id>/invalidation.json``, validates it against
    ``invalidated-capture-v1``, and recomputes sha256 over every archived
    artifact it points at (raw response, explanation when one existed,
    agent-run provenance, the withdrawn candidate-evaluation-v1 record, and
    the withdrawn score-v1 record), raising if any recorded digest does not
    match the actual archived bytes. Also cross-checks that
    ``original_prompt_package_digest`` matches the archived ``agent-run.json``'s
    own ``prompt_package_digest`` -- otherwise this field would be free-text,
    uncheckable by any other assertion here. This proves the archive is an
    honest, unmodified copy of what was withdrawn -- it does not, and cannot,
    prove that the replacement capture referenced by ``replacement_capture``
    is itself free of the same or a different leak; that remains a manual
    review judgment.
    """
    config_dir = case_root / config_id
    record = load_json_strict(config_dir / "invalidation.json")
    validate_against_schema(record, "invalidated-capture-v1")
    if record["config_id"] != config_id:
        raise AgentQualityError(
            f"{config_dir}: invalidation.json config_id {record['config_id']!r} "
            f"does not match its directory name {config_id!r}"
        )

    raw_path = config_dir / "raw-response.txt"
    if not raw_path.is_file():
        raw_path = config_dir / "raw-response.bin"
    check_file_size(raw_path, MAX_RAW_RESPONSE_BYTES, f"{config_dir}/raw-response")
    raw_bytes = raw_path.read_bytes()
    if sha256_bytes(raw_bytes) != record["archived_raw_response_sha256"]:
        raise AgentQualityError(f"{config_dir}: archived raw response bytes do not hash-match")
    if len(raw_bytes) != record["archived_raw_response_bytes"]:
        raise AgentQualityError(f"{config_dir}: archived raw response byte count does not match")

    explanation_path = config_dir / "explanation.json"
    if record["archived_explanation_sha256"] is None:
        if explanation_path.is_file():
            raise AgentQualityError(
                f"{config_dir}: archived_explanation_sha256 is null but explanation.json exists"
            )
    else:
        if not explanation_path.is_file():
            raise AgentQualityError(
                f"{config_dir}: archived_explanation_sha256 is set but explanation.json is missing"
            )
        if sha256_file(explanation_path) != record["archived_explanation_sha256"]:
            raise AgentQualityError(f"{config_dir}: archived explanation.json does not hash-match")

    agent_run_path = config_dir / "agent-run.json"
    agent_run_bytes = agent_run_path.read_bytes()
    if sha256_bytes(agent_run_bytes) != record["archived_agent_run_sha256"]:
        raise AgentQualityError(f"{config_dir}: archived agent-run.json does not hash-match")
    agent_run = parse_json_text(agent_run_bytes.decode("utf-8"))
    if agent_run.get("prompt_package_digest") != record["original_prompt_package_digest"]:
        raise AgentQualityError(
            f"{config_dir}: invalidation.json's original_prompt_package_digest does not match "
            "the archived agent-run.json's own prompt_package_digest"
        )

    evaluation_path = case_root / "evaluations" / f"{config_id}.json"
    if sha256_file(evaluation_path) != record["archived_evaluation_sha256"]:
        raise AgentQualityError(f"{evaluation_path}: does not hash-match its archived digest")

    score_path = case_root / "scores" / f"{config_id}.json"
    if sha256_file(score_path) != record["archived_score_sha256"]:
        raise AgentQualityError(f"{score_path}: does not hash-match its archived digest")

    return cast(dict[str, Any], record)


def validate_protocol_registry(registry: dict[str, Any], cases_dir: Path) -> None:
    """Validate a protocol-registry-v1 document against its schema and check
    that every ``case_revisions`` entry matches the actual committed
    ``case.json`` ``version`` for that case -- so the registry can never
    silently drift from the case content it claims to freeze."""
    validate_against_schema(registry, "protocol-registry-v1")
    for case_id, expected_version in registry["case_revisions"].items():
        case_path = cases_dir / case_id / "case.json"
        if not case_path.is_file():
            raise AgentQualityError(f"protocol_registry.json references unknown case {case_id!r}")
        case = load_json_strict(case_path)
        if case["version"] != expected_version:
            raise AgentQualityError(
                f"protocol_registry.json says {case_id!r} is at revision {expected_version}, "
                f"but its committed case.json is at version {case['version']}"
            )
