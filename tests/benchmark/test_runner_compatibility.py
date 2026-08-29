from __future__ import annotations

import importlib
import sys


def test_runner_imports_without_exiting() -> None:
    runner = importlib.import_module("benchmarks.runner")

    assert runner.__name__ == "benchmarks.runner"


def test_runner_uses_toml_loader_for_running_python() -> None:
    runner = importlib.import_module("benchmarks.runner")
    expected_loader = "tomllib" if sys.version_info >= (3, 11) else "tomli"

    assert runner.tomllib.__name__ == expected_loader
    assert runner.tomllib.loads('scenario = "compatible"') == {"scenario": "compatible"}
