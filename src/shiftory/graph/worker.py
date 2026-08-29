"""Private isolated worker for the pinned Graphora public API."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, NoReturn, cast

from shiftory.errors import GraphoraError
from shiftory.graph.provider import (
    GRAPH_WORKER_REQUEST_SCHEMA,
    GRAPH_WORKER_RESULT_SCHEMA,
    GRAPHORA_PACKAGE_CODE_SHA256,
    GRAPHORA_WHEEL_SHA256,
    _GraphoraWorkerEngine,
    _normalized_path,
)
from shiftory.models.core import Side
from shiftory.models.json import canonical_json


def _fail(
    message: str,
    exception: str = "GraphoraError",
    provenance: dict[str, Any] | None = None,
) -> NoReturn:
    print(
        canonical_json(
            {
                "schema": GRAPH_WORKER_RESULT_SCHEMA,
                "ok": False,
                "result": None,
                "provenance": provenance,
                "error": {
                    "code": "graphora_worker_error",
                    "message": message,
                    "exception": exception,
                },
            }
        ),
        end="",
    )
    raise SystemExit(0)


def _request() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (OSError, ValueError) as exc:
        _fail("invalid worker request JSON", type(exc).__name__)
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "operation",
        "snapshot",
        "project",
        "data_dir",
        "changed_paths",
        "side",
        "changed_lines",
        "expected_provenance_sha256",
    }:
        _fail("invalid worker request envelope")
    if (
        value["schema"] != GRAPH_WORKER_REQUEST_SCHEMA
        or value["operation"] not in {"probe", "enrich"}
        or not isinstance(value["snapshot"], str)
        or not Path(value["snapshot"]).is_absolute()
        or not isinstance(value["data_dir"], str)
        or not Path(value["data_dir"]).is_absolute()
        or not isinstance(value["project"], str)
        or not value["project"]
        or value["side"] not in {"before", "after"}
        or not isinstance(value["changed_paths"], list)
        or not isinstance(value["changed_lines"], dict)
        or (
            value["expected_provenance_sha256"] is not None
            and (
                not isinstance(value["expected_provenance_sha256"], str)
                or len(value["expected_provenance_sha256"]) != 64
            )
        )
    ):
        _fail("invalid worker request fields")
    paths = value["changed_paths"]
    if any(
        not isinstance(path, str) or _normalized_path(path) != path for path in paths
    ) or paths != sorted(set(paths)):
        _fail("invalid or unordered changed paths")
    lines = value["changed_lines"]
    if list(lines) != sorted(lines) or not set(lines).issubset(paths):
        _fail("unordered changed-line paths")
    for path, numbers in lines.items():
        if (
            not isinstance(path, str)
            or _normalized_path(path) != path
            or not isinstance(numbers, list)
            or any(
                not isinstance(number, int) or isinstance(number, bool) or number < 1
                for number in numbers
            )
            or numbers != sorted(set(numbers))
        ):
            _fail("invalid changed lines")
    return cast(dict[str, Any], value)


def _direct_url(distribution: importlib.metadata.Distribution) -> dict[str, Any] | None:
    text = distribution.read_text("direct_url.json")
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return {"invalid": True}
    return value if isinstance(value, dict) else {"invalid": True}


def _provider_provenance() -> dict[str, Any]:
    distribution = importlib.metadata.distribution("graphora-kg")
    direct_url = _direct_url(distribution)
    raw_archive_info = direct_url.get("archive_info") if isinstance(direct_url, dict) else None
    archive_info = raw_archive_info if isinstance(raw_archive_info, dict) else {}
    raw_archive_hashes = archive_info.get("hashes")
    archive_hashes = raw_archive_hashes if isinstance(raw_archive_hashes, dict) else {}
    artifact_sha256 = archive_hashes.get("sha256")
    metadata_license = distribution.metadata["License"]
    editable = bool(
        isinstance(direct_url, dict)
        and isinstance(direct_url.get("dir_info"), dict)
        and direct_url["dir_info"].get("editable") is True
    )

    import graphora

    raw_module_file = getattr(graphora, "__file__", None)
    if not isinstance(raw_module_file, str):
        raise GraphoraError("Graphora package has no module origin")
    module_file = Path(raw_module_file).resolve(strict=True)
    package_root = module_file.parent
    actual_files = sorted(
        path.resolve(strict=True)
        for path in package_root.rglob("*.py")
        if path.is_file() and not path.is_symlink()
    )
    manifest = []
    for path in actual_files:
        try:
            relative = path.relative_to(package_root).as_posix()
        except ValueError as exc:
            raise GraphoraError("Graphora package code escapes its module root") from exc
        payload = path.read_bytes()
        manifest.append(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    package_code_sha256 = hashlib.sha256(
        canonical_json({"files": manifest}).encode("utf-8")
    ).hexdigest()

    declared: dict[Path, importlib.metadata.PackagePath] = {}
    for entry in distribution.files or ():
        parts = Path(str(entry)).parts
        if len(parts) >= 2 and parts[0] == "graphora" and str(entry).endswith(".py"):
            declared[Path(str(distribution.locate_file(entry))).resolve(strict=True)] = entry
    expected_module = next(
        (
            path
            for path in declared
            if path.name == "__init__.py" and path.parent.name == "graphora"
        ),
        None,
    )
    errors: list[str] = []
    if editable:
        errors.append("editable_install")
    if artifact_sha256 is not None and artifact_sha256 != GRAPHORA_WHEEL_SHA256:
        errors.append("unexpected_archive_hash")
    if metadata_license != "MIT":
        errors.append("metadata_license_mismatch")
    if package_code_sha256 != GRAPHORA_PACKAGE_CODE_SHA256:
        errors.append("unexpected_package_code")
    if expected_module is None or module_file != expected_module:
        errors.append("module_origin_mismatch")
    if set(actual_files) != set(declared):
        errors.append("package_inventory_mismatch")
    for path, entry in declared.items():
        record_hash = entry.hash
        if record_hash is None or record_hash.mode != "sha256":
            errors.append("missing_record_hash")
            break
        digest = base64.urlsafe_b64encode(hashlib.sha256(path.read_bytes()).digest())
        if digest.rstrip(b"=").decode("ascii") != record_hash.value:
            errors.append("record_hash_mismatch")
            break
    return {
        "schema": "shiftory.graphora-provider-provenance/v1",
        "distribution": "graphora-kg",
        "distribution_version": distribution.version,
        "module_file": str(module_file),
        "direct_url": direct_url,
        "artifact_sha256": artifact_sha256,
        "metadata_license": metadata_license,
        "editable": editable,
        "package_code_sha256": package_code_sha256,
        "artifact_verified": not errors,
        "artifact_errors": sorted(set(errors)),
    }


def main() -> int:
    request = _request()
    try:
        provenance = _provider_provenance()
        if request["operation"] == "probe":
            print(
                canonical_json(
                    {
                        "schema": GRAPH_WORKER_RESULT_SCHEMA,
                        "ok": True,
                        "result": None,
                        "provenance": provenance,
                        "error": None,
                    }
                ),
                end="",
            )
            return 0
        expected = request["expected_provenance_sha256"]
        actual = hashlib.sha256(canonical_json(provenance).encode("utf-8")).hexdigest()
        if expected != actual:
            _fail("Graphora provider provenance changed after verification", provenance=provenance)
        if not provenance["artifact_verified"]:
            _fail("Graphora provider is not a verified installed artifact", provenance=provenance)
        result = _GraphoraWorkerEngine().enrich(
            Path(request["snapshot"]),
            project=request["project"],
            data_dir=Path(request["data_dir"]),
            patch="",
            changed_paths=tuple(request["changed_paths"]),
            side=cast(Side, request["side"]),
            changed_lines={
                path: tuple(numbers) for path, numbers in request["changed_lines"].items()
            },
        )
    except Exception as exc:
        message = str(exc) if isinstance(exc, GraphoraError) else f"{type(exc).__name__}: {exc}"
        _fail(message, type(exc).__name__, locals().get("provenance"))
    print(
        canonical_json(
            {
                "schema": GRAPH_WORKER_RESULT_SCHEMA,
                "ok": True,
                "result": asdict(result),
                "provenance": provenance,
                "error": None,
            }
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
