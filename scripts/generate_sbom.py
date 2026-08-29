#!/usr/bin/env python3
"""Generate a CycloneDX inventory from an exact wheelhouse and pip install report."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.requirements import InvalidRequirement, Requirement

GRAPHORA_VERSION = "0.2.1"
GRAPHORA_FILENAME = "graphora_kg-0.2.1-py3-none-any.whl"
GRAPHORA_SHA256 = "6b39eab0dc8aa7fc2aec9912d1506306556ca5cacd76447aa00e8afb6ef358d9"
GRAPHORA_TREE_SITTER_REQUIREMENT = "tree-sitter!=0.26.0,>=0.23"
TREE_SITTER_VERSION = "0.25.2"
LICENSE_BASENAME = re.compile(r"^(license|copying|notice)(?:$|[._-].*)", re.IGNORECASE)


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True)
class Artifact:
    name: str
    version: str
    filename: str
    path: Path
    sha256: str
    url: str
    requires: tuple[str, ...]
    license: str | None

    @property
    def ref(self) -> str:
        return f"pkg:pypi/{self.name}@{self.version}"


def wheel_metadata(path: Path) -> tuple[str, str, tuple[str, ...], str | None]:
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise SystemExit(f"{path}: wheel must contain exactly one METADATA file")
            metadata = email.message_from_bytes(archive.read(metadata_names[0]))
    except zipfile.BadZipFile as error:
        raise SystemExit(f"{path}: invalid wheel archive") from error
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise SystemExit(f"{path}: wheel metadata lacks Name or Version")
    declared = metadata.get("License-Expression") or metadata.get("License")
    if declared:
        declared = declared.strip()
        if not declared or declared.upper() == "UNKNOWN":
            declared = None
    return (
        canonical_name(name),
        version,
        tuple(metadata.get_all("Requires-Dist", [])),
        declared,
    )


def report_hash(entry: dict[str, Any], name: str) -> str:
    archive_info = entry.get("download_info", {}).get("archive_info", {})
    digest = archive_info.get("hashes", {}).get("sha256")
    legacy = archive_info.get("hash")
    if not digest and isinstance(legacy, str) and legacy.startswith("sha256="):
        digest = legacy.removeprefix("sha256=")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise SystemExit(f"Install report entry for {name} lacks a SHA-256 artifact hash")
    return digest.lower()


def load_artifacts(
    wheelhouse: Path, report_path: Path
) -> tuple[dict[str, Artifact], dict[str, str]]:
    wheels = sorted(wheelhouse.glob("*.whl"))
    unexpected = sorted(
        path.name for path in wheelhouse.iterdir() if path.is_file() and path.suffix != ".whl"
    )
    if unexpected:
        raise SystemExit(f"Wheelhouse contains non-wheel artifacts: {', '.join(unexpected)}")
    if not wheels:
        raise SystemExit("Wheelhouse contains no wheels")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("version") != "1" or not isinstance(report.get("environment"), dict):
        raise SystemExit("Input is not a pip installation report version 1")
    entries = report.get("install")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("Pip installation report contains no artifacts")

    report_by_filename: dict[str, dict[str, Any]] = {}
    for entry in entries:
        url = entry.get("download_info", {}).get("url")
        metadata = entry.get("metadata", {})
        if not isinstance(url, str) or not metadata.get("name") or not metadata.get("version"):
            raise SystemExit("Pip installation report has an incomplete artifact entry")
        filename = Path(unquote(urlsplit(url).path)).name
        if not filename.endswith(".whl"):
            raise SystemExit(f"Install report accepted a non-wheel artifact: {filename}")
        if filename in report_by_filename:
            raise SystemExit(f"Install report repeats artifact filename: {filename}")
        report_by_filename[filename] = entry

    wheel_names = {path.name for path in wheels}
    report_names = set(report_by_filename)
    if wheel_names != report_names:
        raise SystemExit(
            "Wheelhouse and install report differ; "
            f"only-in-wheelhouse={sorted(wheel_names - report_names)}, "
            f"only-in-report={sorted(report_names - wheel_names)}"
        )

    artifacts: dict[str, Artifact] = {}
    for path in wheels:
        name, version, requires, declared = wheel_metadata(path)
        entry = report_by_filename[path.name]
        report_name = canonical_name(str(entry["metadata"]["name"]))
        report_version = str(entry["metadata"]["version"])
        if (name, version) != (report_name, report_version):
            raise SystemExit(f"{path.name}: wheel metadata and install report disagree")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != report_hash(entry, name):
            raise SystemExit(f"{path.name}: install report SHA-256 does not match wheel bytes")
        if name in artifacts:
            raise SystemExit(f"Wheelhouse has multiple artifacts for distribution: {name}")
        artifacts[name] = Artifact(
            name=name,
            version=version,
            filename=path.name,
            path=path,
            sha256=digest,
            url=str(entry["download_info"]["url"]),
            requires=requires,
            license=declared,
        )
    return artifacts, {str(key): str(value) for key, value in report["environment"].items()}


def dependency_graph(
    artifacts: dict[str, Artifact], environment: dict[str, str]
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    marker_environment = {**environment, "extra": ""}
    for name, artifact in artifacts.items():
        graph[name] = set()
        for raw_requirement in artifact.requires:
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as error:
                raise SystemExit(
                    f"{artifact.filename}: invalid requirement {raw_requirement}"
                ) from error
            if requirement.marker is not None and not requirement.marker.evaluate(
                marker_environment
            ):
                continue
            child_name = canonical_name(requirement.name)
            child = artifacts.get(child_name)
            if child is None:
                raise SystemExit(
                    f"Runtime closure is incomplete: {name} requires missing {child_name}"
                )
            if requirement.specifier and child.version not in requirement.specifier:
                raise SystemExit(
                    f"Resolved {child_name}=={child.version} does not satisfy {raw_requirement}"
                )
            graph[name].add(child_name)
    return graph


def artifact_url(artifact: Artifact, output: Path) -> str:
    parsed = urlsplit(artifact.url)
    if parsed.scheme != "file":
        return artifact.url
    try:
        return artifact.path.resolve().relative_to(output.parent.resolve()).as_posix()
    except ValueError:
        return artifact.path.resolve().as_uri()


def component(artifact: Artifact, output: Path) -> dict[str, Any]:
    digest = {"alg": "SHA-256", "content": artifact.sha256}
    value: dict[str, Any] = {
        "type": "library",
        "bom-ref": artifact.ref,
        "name": artifact.name,
        "version": artifact.version,
        "purl": artifact.ref,
        "hashes": [digest],
        "externalReferences": [
            {
                "type": "distribution",
                "url": artifact_url(artifact, output),
                "hashes": [digest],
            }
        ],
        "properties": [
            {"name": "shiftory:artifact:filename", "value": artifact.filename},
            {"name": "shiftory:artifact:source", "value": "pip-install-report"},
        ],
    }
    if artifact.license:
        value["licenses"] = [{"license": {"name": artifact.license}}]
    return value


def license_material_kind(path: PurePosixPath) -> str:
    basename = path.name.lower()
    if basename.startswith("notice"):
        return "notice"
    if basename.startswith("copying"):
        return "copying"
    return "license"


def license_materials(artifact: Artifact) -> list[tuple[str, bytes, str]]:
    result: list[tuple[str, bytes, str]] = []
    with zipfile.ZipFile(artifact.path) as archive:
        selected: set[str] = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            member = PurePosixPath(name)
            in_license_directory = any(
                part.lower().endswith(".dist-info")
                and index + 1 < len(member.parts)
                and member.parts[index + 1].lower() == "licenses"
                for index, part in enumerate(member.parts)
            )
            if not in_license_directory and LICENSE_BASENAME.fullmatch(member.name) is None:
                continue
            if (
                "\\" in name
                or member.is_absolute()
                or any(part in {"", ".", ".."} for part in member.parts)
            ):
                raise SystemExit(f"{artifact.filename}: unsafe license material path {name!r}")
            if name in selected:
                raise SystemExit(f"{artifact.filename}: duplicate license material path {name!r}")
            selected.add(name)
            result.append((name, archive.read(info), license_material_kind(member)))
    return sorted(result, key=lambda item: item[0])


def verify_license_materials(
    materials_directory: Path,
    artifacts: dict[str, Artifact],
    records: dict[str, list[dict[str, Any]]],
) -> None:
    for name, artifact in artifacts.items():
        with zipfile.ZipFile(artifact.path) as archive:
            for item in records[name]:
                archive_bytes = archive.read(item["path"])
                material_path = materials_directory / item["material_path"]
                material_bytes = material_path.read_bytes()
                archive_hash = hashlib.sha256(archive_bytes).hexdigest()
                material_hash = hashlib.sha256(material_bytes).hexdigest()
                if (
                    archive_hash != item["sha256"]
                    or material_hash != item["sha256"]
                    or len(material_bytes) != item["size"]
                ):
                    raise SystemExit(
                        f"{artifact.filename}: extracted material does not match "
                        f"wheel member {item['path']}"
                    )


def write_license_inventory(
    output: Path,
    materials_directory: Path,
    root: str,
    artifacts: dict[str, Artifact],
) -> None:
    if materials_directory.exists() and any(materials_directory.iterdir()):
        raise SystemExit(f"License materials directory must be empty: {materials_directory}")
    materials_directory.mkdir(parents=True, exist_ok=True)

    records: dict[str, list[dict[str, Any]]] = {}
    for name, artifact in sorted(artifacts.items()):
        materials = license_materials(artifact)
        if not materials:
            raise SystemExit(
                f"Resolved wheel has no LICENSE/COPYING/NOTICE material: {name}=={artifact.version}"
            )
        package_directory = f"{name}-{artifact.version}"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+!-]*", package_directory):
            raise SystemExit(f"{artifact.filename}: unsafe material directory name")
        package_records: list[dict[str, Any]] = []
        for archive_path, content, kind in materials:
            relative_path = Path(package_directory, *PurePosixPath(archive_path).parts)
            destination = materials_directory / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            package_records.append(
                {
                    "path": archive_path,
                    "material_path": relative_path.as_posix(),
                    "kind": kind,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            )
        records[name] = package_records

    verify_license_materials(materials_directory, artifacts, records)

    def package_record(name: str) -> dict[str, Any]:
        artifact = artifacts[name]
        return {
            "name": name,
            "version": artifact.version,
            "artifact": {"filename": artifact.filename, "sha256": artifact.sha256},
            "declared_license": artifact.license,
            "license_files": records[name],
        }

    inventory = {
        "schema_version": 3,
        "license_materials_directory": os.path.relpath(
            materials_directory.resolve(), output.parent.resolve()
        ),
        "root": package_record(root),
        "packages": [package_record(name) for name in sorted(artifacts) if name != root],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_bom(
    bom: dict[str, Any], artifacts: dict[str, Artifact], graph: dict[str, set[str]]
) -> None:
    if (
        bom.get("bomFormat") != "CycloneDX"
        or bom.get("specVersion") != "1.6"
        or not str(bom.get("$schema", "")).endswith("/bom-1.6.schema.json")
    ):
        raise SystemExit("Generated SBOM is not CycloneDX 1.6")
    values = [bom["metadata"]["component"], *bom["components"]]
    by_ref = {item["bom-ref"]: item for item in values}
    expected_refs = {artifact.ref for artifact in artifacts.values()}
    dependency_refs = {item["ref"] for item in bom["dependencies"]}
    if set(by_ref) != expected_refs or dependency_refs != expected_refs:
        raise SystemExit("Generated SBOM failed its dependency-closure completeness check")
    for artifact in artifacts.values():
        item = by_ref[artifact.ref]
        filenames = {
            prop["value"]
            for prop in item.get("properties", [])
            if prop.get("name") == "shiftory:artifact:filename"
        }
        hashes = {
            value["content"] for value in item.get("hashes", []) if value.get("alg") == "SHA-256"
        }
        distribution_refs = [
            value
            for value in item.get("externalReferences", [])
            if value.get("type") == "distribution"
        ]
        referenced_hashes = {
            digest["content"]
            for reference in distribution_refs
            if reference.get("url")
            for digest in reference.get("hashes", [])
            if digest.get("alg") == "SHA-256"
        }
        if (
            item.get("purl") != artifact.ref
            or filenames != {artifact.filename}
            or hashes != {artifact.sha256}
            or referenced_hashes != {artifact.sha256}
        ):
            raise SystemExit(f"SBOM component lacks artifact evidence: {artifact.name}")
    actual_graph = {item["ref"]: set(item["dependsOn"]) for item in bom["dependencies"]}
    expected_graph = {
        artifacts[name].ref: {artifacts[child].ref for child in children}
        for name, children in graph.items()
    }
    if actual_graph != expected_graph:
        raise SystemExit("Generated SBOM dependency graph is incomplete")


def timestamp(value: str | None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif os.environ.get("SOURCE_DATE_EPOCH"):
        parsed = datetime.fromtimestamp(int(os.environ["SOURCE_DATE_EPOCH"]), timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        raise SystemExit("--timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="shiftory")
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--licenses-output", required=True, type=Path)
    parser.add_argument("--license-materials-dir", required=True, type=Path)
    parser.add_argument("--timestamp")
    args = parser.parse_args()

    artifacts, environment = load_artifacts(args.wheelhouse, args.report)
    root = canonical_name(args.root)
    if root not in artifacts:
        raise SystemExit(f"Root distribution is absent from wheelhouse: {root}")
    graphora = artifacts.get("graphora-kg")
    if (
        graphora is None
        or graphora.version != GRAPHORA_VERSION
        or graphora.filename != GRAPHORA_FILENAME
        or graphora.sha256 != GRAPHORA_SHA256
        or graphora.license != "MIT"
    ):
        raise SystemExit("Resolved runtime closure has unexpected graphora-kg artifact facts")
    graphora_requirements = [
        Requirement(value)
        for value in graphora.requires
        if canonical_name(Requirement(value).name) == "tree-sitter"
    ]
    if (
        len(graphora_requirements) != 1
        or str(graphora_requirements[0]) != GRAPHORA_TREE_SITTER_REQUIREMENT
    ):
        raise SystemExit("graphora-kg has an unexpected tree-sitter dependency contract")
    tree_sitter = artifacts.get("tree-sitter")
    if tree_sitter is None or tree_sitter.version != TREE_SITTER_VERSION:
        raise SystemExit("Resolved runtime closure must contain tree-sitter 0.25.2")
    graph = dependency_graph(artifacts, environment)

    reachable: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(graph[name])
    if reachable != set(artifacts):
        extras = sorted(set(artifacts) - reachable)
        raise SystemExit(f"Wheelhouse has artifacts outside runtime closure: {extras}")

    serial_seed = "\n".join(
        f"{artifact.ref}#{artifact.sha256}"
        for artifact in sorted(artifacts.values(), key=lambda x: x.name)
    )
    bom = {
        "$schema": "https://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_seed)}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp(args.timestamp),
            "component": component(artifacts[root], args.output),
            "properties": [
                {"name": f"shiftory:release-environment:{key}", "value": value}
                for key, value in sorted(environment.items())
            ],
        },
        "components": [
            component(artifacts[name], args.output) for name in sorted(artifacts) if name != root
        ],
        "dependencies": [
            {
                "ref": artifacts[name].ref,
                "dependsOn": [artifacts[child].ref for child in sorted(children)],
            }
            for name, children in sorted(graph.items())
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_bom(json.loads(args.output.read_text(encoding="utf-8")), artifacts, graph)
    write_license_inventory(
        args.licenses_output,
        args.license_materials_dir,
        root,
        artifacts,
    )
    print(f"Wrote {args.output} with {len(artifacts)} exact runtime wheel artifacts.")


if __name__ == "__main__":
    main()
