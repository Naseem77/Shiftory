#!/usr/bin/env python3
"""Exercise the complete benchmark path on the committed fixture without a network."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = ROOT / ".ci-work" / "benchmark-smoke"


def main() -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=WORK_ROOT) as temporary_workspace:
        workspace = Path(temporary_workspace)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "benchmarks" / "runner.py"),
                "--workspace",
                str(workspace),
                "offline",
            ],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
