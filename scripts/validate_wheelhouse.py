#!/usr/bin/env python3
"""Validate that a binary wheelhouse exactly matches a pip install report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


def report_sha256(entry: dict[str, Any], filename: str) -> str:
    archive_info = entry.get("download_info", {}).get("archive_info", {})
    digest = archive_info.get("hashes", {}).get("sha256")
    legacy = archive_info.get("hash")
    if not digest and isinstance(legacy, str) and legacy.startswith("sha256="):
        digest = legacy.removeprefix("sha256=")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError(f"Install report entry for {filename} lacks a SHA-256 hash")
    return digest.lower()


def validate_wheelhouse(wheelhouse: Path, report_path: Path) -> int:
    unexpected = sorted(
        path.name for path in wheelhouse.iterdir() if path.is_file() and path.suffix != ".whl"
    )
    if unexpected:
        raise ValueError(f"Wheelhouse contains non-wheel artifacts: {', '.join(unexpected)}")

    wheels = {path.name: path for path in wheelhouse.glob("*.whl")}
    if not wheels:
        raise ValueError("Wheelhouse contains no wheels")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("version") != "1" or not isinstance(report.get("environment"), dict):
        raise ValueError("Input is not a pip installation report version 1")
    entries = report.get("install")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Pip installation report contains no artifacts")

    reported: dict[str, dict[str, Any]] = {}
    for entry in entries:
        url = entry.get("download_info", {}).get("url")
        if not isinstance(url, str):
            raise ValueError("Pip installation report has an artifact without a URL")
        filename = Path(unquote(urlsplit(url).path)).name
        if not filename.endswith(".whl"):
            raise ValueError(f"Install report accepted a non-wheel artifact: {filename}")
        if filename in reported:
            raise ValueError(f"Install report repeats artifact filename: {filename}")
        reported[filename] = entry

    wheel_names = set(wheels)
    report_names = set(reported)
    if wheel_names != report_names:
        raise ValueError(
            "Wheelhouse and install report differ; "
            f"only-in-wheelhouse={sorted(wheel_names - report_names)}, "
            f"only-in-report={sorted(report_names - wheel_names)}"
        )

    for filename, path in wheels.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = report_sha256(reported[filename], filename)
        if actual != expected:
            raise ValueError(f"{filename}: install report SHA-256 does not match wheel bytes")
    return len(wheels)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--expected-count", required=True, type=int)
    args = parser.parse_args()

    try:
        count = validate_wheelhouse(args.wheelhouse, args.report)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    if count != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} wheels, found {count}")
    print(f"Validated {count} wheel artifacts and their install-report SHA-256 hashes.")


if __name__ == "__main__":
    main()
