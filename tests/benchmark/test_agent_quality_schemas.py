"""Schema self-validation and duplicate-key/cap rejection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.agent_quality import validation as v

SCHEMA_NAMES = [
    "case-v1",
    "rubric-v1",
    "claim-record-v1",
    "candidate-evaluation-v1",
    "agent-run-v1",
    "agent-run-v2",
    "invalidated-capture-v1",
    "invalidated-generation-attempt-v1",
    "protocol-registry-v1",
    "score-v1",
    "scores-v1",
]


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = v._load_schema(name)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(v.AgentQualityError, match="Duplicate JSON key"):
        v.load_json_strict(path)


def test_oversized_document_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"a": "x" * 100}), encoding="utf-8")
    with pytest.raises(v.AgentQualityError, match="exceeding the"):
        v.load_json_strict(path, max_bytes=10)


def test_non_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe{}")
    with pytest.raises(v.AgentQualityError, match="not valid UTF-8"):
        v.load_json_strict(path)


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(v.AgentQualityError, match="not valid JSON"):
        v.load_json_strict(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(v.AgentQualityError, match="does not exist"):
        v.load_json_strict(tmp_path / "missing.json")


def test_case_schema_rejects_answer_key_shaped_fields() -> None:
    value = {
        "schema": "shiftory.benchmark-agent-quality-case/v1",
        "id": "demo-case",
        "version": 1,
        "category": "ordering-control-flow",
        "description": "A guard clause moves relative to a validation call.",
        "fixture": {"history": "history.fast-import", "metadata": "metadata.json"},
        "required_facts": [{"id": "leak"}],
    }
    with pytest.raises(v.AgentQualityError, match="does not match its schema"):
        v.validate_against_schema(value, "case-v1")


def test_case_id_pattern_rejects_traversal_like_ids() -> None:
    for bad_id in ("../escape", "UPPER", "has space", "trailing-.", ""):
        assert not v.CASE_ID_RE.fullmatch(bad_id)
    assert v.CASE_ID_RE.fullmatch("reordering-guard-clause")
