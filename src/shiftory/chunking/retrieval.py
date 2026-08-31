"""Resolve only hash-bound source ranges recorded by a private chunk plan."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, cast

from shiftory.chunking.planner import (
    TOKEN_ESTIMATE_FORMULA,
    _set_measured_fields,
    estimate_tokens,
    plan_identity,
    sha256_json,
    source_range_identity,
)
from shiftory.errors import RetrievalError
from shiftory.git.repository import assert_comparison_consistent, repository_identity
from shiftory.git.source import source_bytes
from shiftory.models.core import Comparison


def retrieve_source_range(
    descriptor: dict[str, Any],
    evidence: dict[str, Any],
    plan: dict[str, Any],
    range_id: str,
) -> dict[str, Any]:
    comparison_identity = _comparison_identity(evidence)
    ledger_sha256 = sha256_json(evidence)
    if plan.get("id") != plan_identity(plan):
        raise RetrievalError("The chunk plan identity is invalid")
    if plan.get("comparison_identity") != comparison_identity:
        raise RetrievalError("The chunk plan comparison identity is stale")
    if plan.get("ledger_sha256") != ledger_sha256:
        raise RetrievalError("The chunk plan ledger digest is stale")
    records = [
        value
        for value in plan.get("retrieval_ranges", [])
        if isinstance(value, dict) and value.get("id") == range_id
    ]
    if len(records) != 1:
        raise RetrievalError(
            "The requested source range is not uniquely recorded by this run",
            details={"range_id": range_id},
        )
    record = records[0]
    citation = _citation(evidence, record.get("citation_id"))
    _validate_record(record, citation)
    comparison = _comparison(descriptor, evidence)
    assert_comparison_consistent(comparison, operation="recorded source retrieval")
    content = source_bytes(comparison, record["path"], record["side"])
    if content is None:
        raise RetrievalError(
            "The recorded source is unavailable",
            details={"range_id": range_id, "path": record["path"]},
        )
    lines = _exact_source_lines(content)
    citation_text = b"\n".join(lines[int(citation["start_line"]) - 1 : int(citation["end_line"])])
    if hashlib.sha256(citation_text).hexdigest() != citation["content_hash"]:
        raise RetrievalError(
            "The recorded citation content hash no longer matches its source",
            details={"citation_id": citation["id"], "range_id": range_id},
        )
    raw = b"\n".join(lines[int(record["start_line"]) - 1 : int(record["end_line"])])
    text = raw.decode("utf-8", "backslashreplace")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != record["content_hash"]:
        raise RetrievalError(
            "The retrieved source range failed content-hash verification",
            details={"range_id": range_id},
        )
    response = _set_measured_fields(
        {
            "schema": "shiftory.retrieval/v1",
            "comparison_identity": comparison_identity,
            "ledger_sha256": ledger_sha256,
            "range_id": range_id,
            "citation_id": record["citation_id"],
            "path": record["path"],
            "side": record["side"],
            "start_line": record["start_line"],
            "end_line": record["end_line"],
            "content_hash": record["content_hash"],
            "text": text,
            "actual_bytes": 0,
            "estimated_tokens": 0,
            "token_estimate_formula": TOKEN_ESTIMATE_FORMULA,
        }
    )
    if (
        response["actual_bytes"] != record["response_bytes"]
        or response["estimated_tokens"] != record["estimated_tokens"]
        or response["estimated_tokens"] != estimate_tokens(response["actual_bytes"])
    ):
        raise RetrievalError("The recorded retrieval response size is invalid")
    effective = plan.get("budget", {}).get("effective_max_bytes")
    if not isinstance(effective, int) or response["actual_bytes"] > effective:
        raise RetrievalError("The retrieval response exceeds its recorded agent budget")
    assert_comparison_consistent(comparison, operation="recorded source retrieval")
    return response


def _comparison_identity(evidence: dict[str, Any]) -> str:
    comparison = evidence.get("comparison")
    identity = comparison.get("identity") if isinstance(comparison, dict) else None
    if not isinstance(identity, str) or not identity:
        raise RetrievalError("The global evidence ledger has no comparison identity")
    return identity


def _citation(evidence: dict[str, Any], citation_id: Any) -> dict[str, Any]:
    matches = [
        citation
        for file in evidence.get("files", [])
        for citation in file.get("citations", [])
        if citation.get("id") == citation_id
    ]
    if len(matches) != 1:
        raise RetrievalError("The recorded source range has no unique global citation")
    return cast(dict[str, Any], matches[0])


def _validate_record(record: dict[str, Any], citation: dict[str, Any]) -> None:
    path = record.get("path")
    if not isinstance(path, str) or not _safe_recorded_path(path):
        raise RetrievalError("The recorded retrieval path is unsafe")
    side = record.get("side")
    start, end = record.get("start_line"), record.get("end_line")
    if (
        side not in {"before", "after"}
        or path != citation.get("path")
        or side != citation.get("side")
        or not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < int(citation.get("start_line", 0))
        or end > int(citation.get("end_line", 0))
        or start > end
    ):
        raise RetrievalError("The recorded source range is outside its global citation")
    if record.get("id") != source_range_identity(record):
        raise RetrievalError("The recorded source range identity is invalid")


def _safe_recorded_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return bool(
        path
        and "\x00" not in path
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and "." not in candidate.parts
    )


def _comparison(descriptor: dict[str, Any], evidence: dict[str, Any]) -> Comparison:
    root_value = descriptor.get("repository_root")
    repository_id = descriptor.get("repository_id")
    comparison = evidence.get("comparison")
    if (
        not isinstance(root_value, str)
        or not isinstance(repository_id, str)
        or not isinstance(comparison, dict)
    ):
        raise RetrievalError("The run does not record a valid repository comparison")
    root = Path(root_value).resolve()
    if not root.is_dir() or repository_identity(root) != repository_id:
        raise RetrievalError("The recorded repository identity no longer matches")
    if comparison.get("repository_id") != repository_id:
        raise RetrievalError("The evidence repository identity does not match the run")
    return Comparison(
        repository_root=root,
        repository_id=repository_id,
        mode=cast(str, comparison["mode"]),
        base_sha=cast(str | None, comparison.get("base_sha")),
        head_sha=cast(str | None, comparison.get("head_sha")),
        base_label=cast(str, comparison["base_label"]),
        head_label=cast(str, comparison["head_label"]),
        identity=cast(str, comparison["identity"]),
        after_fingerprint=cast(str | None, comparison.get("after_fingerprint")),
        parent=cast(int | None, comparison.get("parent")),
    )


def _exact_source_lines(content: bytes) -> list[bytes]:
    lines = content.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines
