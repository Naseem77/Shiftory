"""Bundled JSON schemas."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Literal

SchemaName = Literal[
    "evidence",
    "explanation",
    "report",
    "run",
    "chunk-plan",
    "chunk",
    "chunk-explanation",
    "retrieval",
]

_SCHEMA_FILES: dict[SchemaName, str] = {
    "evidence": "evidence-v1.json",
    "explanation": "explanation-v1.json",
    "report": "report-v1.json",
    "run": "run-v2.json",
    "chunk-plan": "chunk-plan-v1.json",
    "chunk": "chunk-v1.json",
    "chunk-explanation": "chunk-explanation-v1.json",
    "retrieval": "retrieval-v1.json",
}


def load_schema(name: SchemaName) -> dict[str, Any]:
    resource = files("shiftory.schemas").joinpath(_SCHEMA_FILES[name])
    value = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
