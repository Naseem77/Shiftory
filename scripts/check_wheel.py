#!/usr/bin/env python3
"""Check that the release wheel contains its public and bundled assets."""

from __future__ import annotations

import argparse
import base64
import csv
import email
import hashlib
import io
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "src" / "shiftory"


def expected_package_files() -> set[str]:
    distributable = {".py", ".json"}
    return {
        f"shiftory/{path.relative_to(PACKAGE_ROOT).as_posix()}"
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file()
        and (path.suffix in distributable or path.name == "py.typed" or path.name == "SKILL.md")
    }


def validate_record(archive: zipfile.ZipFile, names: set[str], record_name: str) -> None:
    rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
    if {row[0] for row in rows} != names or any(len(row) != 3 for row in rows):
        raise SystemExit("Wheel RECORD does not exactly enumerate the archive")
    for name, digest, size in rows:
        if name == record_name:
            if digest or size:
                raise SystemExit("Wheel RECORD entry must not hash itself")
            continue
        payload = archive.read(name)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        if digest != f"sha256={encoded}" or size != str(len(payload)):
            raise SystemExit(f"Wheel RECORD hash or size is invalid for {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.wheel) as archive:
        entries = archive.namelist()
        if len(entries) != len(set(entries)):
            raise SystemExit("Wheel contains duplicate archive entries")
        if any(
            name.startswith(("/", "\\")) or ".." in Path(name).parts or "\\" in name
            for name in entries
        ):
            raise SystemExit("Wheel contains an unsafe archive path")
        names = set(entries)

        metadata_names = [name for name in entries if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise SystemExit("Wheel must contain exactly one METADATA file")
        dist_info = metadata_names[0].removesuffix("METADATA")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
        if (
            metadata["Name"] != "shiftory"
            or metadata["Requires-Python"] != ">=3.10"
            or dist_info != f"shiftory-{metadata['Version']}.dist-info/"
        ):
            raise SystemExit(
                "Wheel metadata has an unexpected name, version, or Python requirement"
            )
        expected = expected_package_files() | {
            f"{dist_info}METADATA",
            f"{dist_info}WHEEL",
            f"{dist_info}entry_points.txt",
            f"{dist_info}licenses/LICENSE",
            f"{dist_info}RECORD",
        }
        if names != expected:
            missing = sorted(expected - names)
            unexpected = sorted(names - expected)
            raise SystemExit(
                f"Wheel content is not exact; missing={missing}, unexpected={unexpected}"
            )

        requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
        graphora_requirements = [
            requirement
            for requirement in requirements
            if requirement.name.lower().replace("_", "-") == "graphora-kg"
        ]
        if (
            len(graphora_requirements) != 1
            or str(graphora_requirements[0].specifier) != "==0.2.1"
            or graphora_requirements[0].extras
            or graphora_requirements[0].marker is not None
        ):
            raise SystemExit("Wheel does not preserve the graphora-kg==0.2.1 pin")
        entry_points = archive.read(f"{dist_info}entry_points.txt").decode("utf-8")
        if entry_points.strip() != "[console_scripts]\nshiftory = shiftory.cli:main":
            raise SystemExit("Wheel does not contain the Shiftory console entry point")
        validate_record(archive, names, f"{dist_info}RECORD")

    print(f"Wheel contents valid: {args.wheel}")


if __name__ == "__main__":
    main()
