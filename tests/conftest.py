from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest


@pytest.fixture
def repo_factory() -> Iterator[Callable[[], Path]]:
    root = Path.cwd() / ".pytest-workspaces" / uuid.uuid4().hex
    root.mkdir(parents=True)

    def create() -> Path:
        repository = root / uuid.uuid4().hex
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Shiftory Test"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "shiftory@example.invalid"],
            cwd=repository,
            check=True,
        )
        (repository / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
        return repository

    yield create
    shutil.rmtree(root)
    parent = root.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
