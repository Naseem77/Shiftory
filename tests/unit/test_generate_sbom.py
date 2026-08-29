from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "generate_sbom", ROOT / "scripts" / "generate_sbom.py"
)
assert SPEC is not None and SPEC.loader is not None
generate_sbom = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = generate_sbom
SPEC.loader.exec_module(generate_sbom)


def artifact(name: str, requires: tuple[str, ...] = ()) -> object:
    return generate_sbom.Artifact(
        name=name,
        version="5.0.1" if name == "async-timeout" else "8.1.0",
        filename=f"{name}.whl",
        path=Path(f"{name}.whl"),
        sha256="0" * 64,
        url=f"https://example.invalid/{name}.whl",
        requires=requires,
        license="MIT",
    )


def environment(version: str) -> dict[str, str]:
    major_minor = ".".join(version.split(".")[:2])
    return {
        "python_full_version": version,
        "python_version": major_minor,
        "implementation_name": "cpython",
    }


def test_python310_sbom_graph_requires_conditional_async_timeout() -> None:
    artifacts = {
        "redis": artifact("redis", ('async-timeout>=4.0.3; python_full_version < "3.11.3"',))
    }
    with pytest.raises(SystemExit, match="requires missing async-timeout"):
        generate_sbom.dependency_graph(artifacts, environment("3.10.18"))

    artifacts["async-timeout"] = artifact("async-timeout")
    assert generate_sbom.dependency_graph(artifacts, environment("3.10.18"))["redis"] == {
        "async-timeout"
    }


def test_python312_sbom_graph_excludes_conditional_async_timeout() -> None:
    artifacts = {
        "redis": artifact("redis", ('async-timeout>=4.0.3; python_full_version < "3.11.3"',))
    }
    assert generate_sbom.dependency_graph(artifacts, environment("3.12.11"))["redis"] == set()


def wheel_artifact(path: Path, name: str, license_name: str | None = "MIT") -> object:
    return generate_sbom.Artifact(
        name=name,
        version="1.0",
        filename=path.name,
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        url=f"https://example.invalid/{path.name}",
        requires=(),
        license=license_name,
    )


def test_license_inventory_extracts_and_verifies_all_wheel_materials(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example-1.0.dist-info/licenses/LICENSE.txt", b"license bytes")
        archive.writestr("example/NOTICE", b"notice bytes")
    artifacts = {"example": wheel_artifact(wheel, "example")}
    output = tmp_path / "inventory.json"
    materials = tmp_path / "materials"

    generate_sbom.write_license_inventory(output, materials, "example", artifacts)

    inventory = json.loads(output.read_text(encoding="utf-8"))
    files = inventory["root"]["license_files"]
    assert inventory["schema_version"] == 3
    assert {item["kind"] for item in files} == {"license", "notice"}
    for item in files:
        extracted = materials / item["material_path"]
        assert hashlib.sha256(extracted.read_bytes()).hexdigest() == item["sha256"]

    (materials / files[0]["material_path"]).write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="does not match wheel member"):
        generate_sbom.verify_license_materials(materials, artifacts, {"example": files})


def test_license_inventory_fails_closed_without_wheel_material(tmp_path: Path) -> None:
    wheel = tmp_path / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example/__init__.py", b"")

    with pytest.raises(SystemExit, match="no LICENSE/COPYING/NOTICE material"):
        generate_sbom.write_license_inventory(
            tmp_path / "inventory.json",
            tmp_path / "materials",
            "example",
            {"example": wheel_artifact(wheel, "example")},
        )
