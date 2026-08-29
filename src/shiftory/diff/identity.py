"""Versioned stable identity construction."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_id(kind: str, payload: dict[str, Any] | bytes) -> str:
    if isinstance(payload, dict):
        value = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    else:
        value = payload
    digest = hashlib.sha256(f"shiftory-{kind}/v1\0".encode() + value).hexdigest()
    return f"{kind}_{digest[:24]}"
