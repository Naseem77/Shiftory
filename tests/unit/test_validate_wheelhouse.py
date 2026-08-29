from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "validate_wheelhouse", ROOT / "scripts" / "validate_wheelhouse.py"
)
assert SPEC is not None and SPEC.loader is not None
validate_wheelhouse = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_wheelhouse
SPEC.loader.exec_module(validate_wheelhouse)


def write_report(path: Path, filename: str, digest: str) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "1",
                "environment": {"python_version": "3.10"},
                "install": [
                    {
                        "download_info": {
                            "url": f"https://files.pythonhosted.org/{filename}",
                            "archive_info": {"hashes": {"sha256": digest}},
                        }
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_validate_wheelhouse_checks_exact_report_hash(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "example-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes")
    report = tmp_path / "report.json"
    write_report(report, wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest())

    assert validate_wheelhouse.validate_wheelhouse(wheelhouse, report) == 1

    wheel.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="does not match wheel bytes"):
        validate_wheelhouse.validate_wheelhouse(wheelhouse, report)
