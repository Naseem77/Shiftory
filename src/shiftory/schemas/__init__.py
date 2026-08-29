"""Bundled JSON schemas."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Literal

SchemaName = Literal["evidence", "explanation", "report"]


def load_schema(name: SchemaName) -> dict[str, Any]:
    resource = files("shiftory.schemas").joinpath(f"{name}-v1.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
