"""Canonical JSON helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def parse_json_bytes(payload: bytes, source: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{source} must contain valid UTF-8 JSON") from error
    value = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError(f"{source} must contain a JSON object")
    return value


def parse_canonical_json_bytes(payload: bytes, source: str) -> dict[str, Any]:
    value = parse_json_bytes(payload, source)
    if payload != canonical_json(value).encode("utf-8"):
        raise ValueError(f"{source} must contain byte-for-byte canonical JSON")
    return value


def load_json(path: Path) -> dict[str, Any]:
    return parse_json_bytes(path.read_bytes(), str(path))
