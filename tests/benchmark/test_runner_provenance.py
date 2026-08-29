from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from benchmarks import runner


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def committed_implementation(repository: Path) -> None:
    files = {
        "pyproject.toml": (
            "[project]\n"
            'name = "shiftory"\n'
            'version = "9.8.7"\n'
            'requires-python = ">=3.11"\n'
            'dependencies = ["graphora-kg==0.2.1", "jsonschema>=4,<5"]\n'
        ),
        "constraints-dev.txt": ("graphora-kg==0.2.1\ntree-sitter==0.25.2\njsonschema==4.25.1\n"),
        "src/shiftory/__init__.py": '__version__ = "9.8.7"\n',
        "src/shiftory/cli.py": (
            "import sys\nif '--version' in sys.argv:\n    print('shiftory 9.8.7')\n"
        ),
        "src/shiftory/render/__init__.py": "",
        "src/shiftory/render/evidence.py": (
            "def render_evidence_markdown(evidence):\n"
            "    return f\"# trusted evidence\\n\\n{evidence['marker']}\\n\"\n"
        ),
        "benchmarks/runner.py": "# committed runner\n",
        "benchmarks/scenarios.toml": "schema_version = 1\n",
        "benchmarks/golden/demo/assertions.json": '{"schema": "test"}\n',
        "benchmarks/fixtures/demo/metadata.json": '{"fixture": true}\n',
        "docs/benchmarks/demo/metrics-v1.json": "{}\n",
        "docs/benchmarks/demo/report.md": "# old\n",
    }
    for name, content in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "benchmark implementation")


def publication_metrics(
    repository: Path,
    implementation: dict,
    execution: dict,
) -> dict:
    renderer_identity = runner.evidence_renderer_identity(execution, repository)
    return {
        "implementation": implementation,
        "execution": execution,
        "evidence_markdown_renderer": renderer_identity,
        "runs": [
            {"evidence_markdown_renderer": renderer_identity},
            {"evidence_markdown_renderer": renderer_identity},
        ],
    }


def test_publication_rejects_dirty_shiftory_worktree(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    (repository / "src/shiftory/__init__.py").write_text(
        '__version__ = "dirty"\n', encoding="utf-8"
    )
    monkeypatch.setattr(runner, "ROOT", repository)

    with pytest.raises(runner.BenchmarkError, match="requires a clean Shiftory worktree"):
        runner.publish({})

    assert (repository / "docs/benchmarks/demo/metrics-v1.json").read_text() == "{}\n"


def test_clean_publication_is_bound_to_committed_implementation(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    identity = runner.implementation_identity(repository)
    head = git(repository, "rev-parse", "HEAD")
    source = (repository / "src/shiftory/__init__.py").read_bytes()
    source_entry = next(
        entry
        for entry in identity["manifest"]["files"]
        if entry["path"] == "src/shiftory/__init__.py"
    )

    assert identity["shiftory_commit"] == head
    assert identity["shiftory_worktree_clean"] is True
    assert source_entry["sha256"] == hashlib.sha256(source).hexdigest()
    assert source_entry["committed_blob"] == git(
        repository, "rev-parse", "HEAD:src/shiftory/__init__.py"
    )
    assert identity["runner_sha256"] == hashlib.sha256(b"# committed runner\n").hexdigest()
    assert identity["package"]["version"] == "9.8.7"
    assert identity["package"]["dependencies"] == [
        "graphora-kg==0.2.1",
        "jsonschema>=4,<5",
    ]

    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})
    execution = runner.executed_package_identity(repository)
    metrics = publication_metrics(repository, identity, execution)
    runner.publish({"demo": (metrics, "# new\n")})

    published = json.loads(
        (repository / "docs/benchmarks/demo/metrics-v1.json").read_text(encoding="utf-8")
    )
    assert published["implementation"] == identity


def test_clean_publication_rejects_results_from_another_commit(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    stale_identity = runner.implementation_identity(repository)
    (repository / "src/shiftory/__init__.py").write_text(
        '__version__ = "9.8.8"\n', encoding="utf-8"
    )
    git(repository, "add", "src/shiftory/__init__.py")
    git(repository, "commit", "-m", "change implementation")
    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})

    with pytest.raises(runner.BenchmarkError, match="not bound to the current"):
        runner.publish({"demo": ({"implementation": stale_identity}, "# stale\n")})


def test_adversarial_pythonpath_cannot_impersonate_checkout(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    external = repository.parent / "other-checkout"
    package = external / "src" / "shiftory"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "external"\n', encoding="utf-8")
    (package / "cli.py").write_text(
        "import sys\nif '--version' in sys.argv:\n    print('shiftory external')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(external / "src"))
    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})
    monkeypatch.chdir(external)

    implementation = runner.implementation_identity(repository)
    local_execution = runner.executed_package_identity(repository)
    external_execution = runner.executed_package_identity(external)

    assert runner.shiftory_process(["--version"]).stdout.strip() == "shiftory 9.8.7"
    assert local_execution["distribution"] == "shiftory"
    assert local_execution["import_root"] == "repository:src"
    assert local_execution["module_file"] == "src/shiftory/__init__.py"
    assert local_execution["package_code"] == implementation["package_code"]
    assert external_execution["package_code"] != implementation["package_code"]

    with pytest.raises(runner.BenchmarkError, match="executed code other than"):
        runner.publish(
            {
                "demo": (
                    {
                        "implementation": implementation,
                        "execution": external_execution,
                    },
                    "# forged\n",
                )
            }
        )
    assert (repository / "docs/benchmarks/demo/metrics-v1.json").read_text() == "{}\n"


def test_external_evidence_renderer_cannot_influence_artifact_or_publication(
    repo_factory, monkeypatch
) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    external = repository.parent / "renderer-checkout"
    package = external / "src" / "shiftory"
    (package / "render").mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "external"\n', encoding="utf-8")
    (package / "render" / "__init__.py").write_text("", encoding="utf-8")
    external_renderer = package / "render" / "evidence.py"
    external_renderer.write_text(
        "def render_evidence_markdown(evidence):\n    return '# FORGED EXTERNAL EVIDENCE\\n'\n",
        encoding="utf-8",
    )

    injected_shiftory = types.ModuleType("shiftory")
    injected_shiftory.__file__ = str(package / "__init__.py")
    injected_shiftory.__path__ = [str(package)]
    injected_render = types.ModuleType("shiftory.render")
    injected_render.__file__ = str(package / "render" / "__init__.py")
    injected_render.__path__ = [str(package / "render")]
    injected_evidence = types.ModuleType("shiftory.render.evidence")
    injected_evidence.__file__ = str(external_renderer)
    injected_evidence.render_evidence_markdown = lambda evidence: "# INJECTED SYS.MODULES\n"
    monkeypatch.setitem(sys.modules, "shiftory", injected_shiftory)
    monkeypatch.setitem(sys.modules, "shiftory.render", injected_render)
    monkeypatch.setitem(sys.modules, "shiftory.render.evidence", injected_evidence)
    monkeypatch.setenv("PYTHONPATH", str(external / "src"))
    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})
    monkeypatch.chdir(external)

    implementation = runner.implementation_identity(repository)
    execution = runner.executed_package_identity(repository)
    markdown, renderer_identity = runner.render_evidence(
        {"marker": "local artifact"}, execution, root=repository
    )

    assert markdown == "# trusted evidence\n\nlocal artifact\n"
    assert renderer_identity["distribution"] == "shiftory"
    assert renderer_identity["module"] == "shiftory.render.evidence"
    assert renderer_identity["module_file"] == "src/shiftory/render/evidence.py"
    assert (
        renderer_identity["module_sha256"]
        == hashlib.sha256((repository / "src/shiftory/render/evidence.py").read_bytes()).hexdigest()
    )
    assert renderer_identity["package_code_sha256"] == execution["package_code"]["sha256"]

    metrics = publication_metrics(repository, implementation, execution)
    forged_metrics = json.loads(json.dumps(metrics))
    forged_metrics["evidence_markdown_renderer"]["module_file"] = "src/forged/render/evidence.py"
    with pytest.raises(runner.BenchmarkError, match="renderer is not bound"):
        runner.publish({"demo": (forged_metrics, "# forged\n")})
    assert (repository / "docs/benchmarks/demo/report.md").read_text() == "# old\n"

    runner.publish({"demo": (metrics, markdown)})
    assert (repository / "docs/benchmarks/demo/report.md").read_text() == markdown
    assert (
        json.loads((repository / "docs/benchmarks/demo/metrics-v1.json").read_text())[
            "evidence_markdown_renderer"
        ]
        == renderer_identity
    )


def test_semantic_hash_excludes_environment_paths_and_timings(tmp_path) -> None:
    provenance = {
        "shiftory_package_code_sha256": "1" * 64,
        "evidence_renderer_sha256": "2" * 64,
        "runner_sha256": "3" * 64,
        "golden_inputs_sha256": "4" * 64,
    }

    def artifacts(name: str, local_root: Path) -> dict[str, Path]:
        directory = tmp_path / name
        directory.mkdir()
        evidence = {
            "graph": {
                "facts": [{"id": "fact_1", "kind": "definition", "path": "src/demo.py"}],
                "diagnostics": [
                    {
                        "distribution": "graphora-kg",
                        "module_file": str(local_root / "site-packages/graphora/__init__.py"),
                        "direct_url": {"url": (local_root / "graphora.whl").as_uri()},
                        "artifact_sha256": "5" * 64,
                        "package_code_sha256": "6" * 64,
                        "artifact_verified": True,
                    }
                ],
                "workspace_path": str(local_root / "workspace"),
                "cache_dir": str(local_root / "cache"),
            },
            "recorded_at_utc": name,
            "analysis_seconds": 1 if name == "cold" else 9,
        }
        json_path = directory / "evidence.json"
        json_path.write_text(json.dumps(evidence), encoding="utf-8")
        markdown_path = directory / "evidence.md"
        markdown_path.write_text(f"fact\nworkspace: {local_root}/workspace\n")
        return {"evidence_json": json_path, "evidence_markdown": markdown_path}

    cold_root, warm_root = tmp_path / "install-a", tmp_path / "install-b"
    cold_paths = artifacts("cold", cold_root)
    warm_paths = artifacts("warm", warm_root)
    assertions = {"passed": True}
    cold, cold_raw = runner.semantic_bundle(
        cold_paths, assertions, provenance, excluded_roots=(cold_root,)
    )
    warm, warm_raw = runner.semantic_bundle(
        warm_paths, assertions, provenance, excluded_roots=(warm_root,)
    )
    assert cold == warm
    assert cold_raw != warm_raw

    changed = json.loads(warm_paths["evidence_json"].read_text())
    changed["graph"]["facts"][0]["kind"] = "caller"
    warm_paths["evidence_json"].write_text(json.dumps(changed))
    changed_semantics, _ = runner.semantic_bundle(
        warm_paths, assertions, provenance, excluded_roots=(warm_root,)
    )
    assert changed_semantics != cold
    changed_provenance, _ = runner.semantic_bundle(
        cold_paths,
        assertions,
        {**provenance, "runner_sha256": "7" * 64},
        excluded_roots=(cold_root,),
    )
    assert changed_provenance != cold


def test_publication_rejects_injected_local_roots(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})
    implementation = runner.implementation_identity(repository)
    execution = runner.executed_package_identity(repository)
    metrics = publication_metrics(repository, implementation, execution)
    metrics["injected_workspace"] = str(repository / ".cache/private-user")

    with pytest.raises(runner.BenchmarkError, match="Refusing to publish metrics JSON"):
        runner.publish({"demo": (metrics, f"private root: {repository}\n")})
    assert (repository / "docs/benchmarks/demo/metrics-v1.json").read_text() == "{}\n"

    metrics.pop("injected_workspace")
    with pytest.raises(runner.BenchmarkError, match="Refusing to publish report Markdown"):
        runner.publish({"demo": (metrics, f"private root: {repository}\n")})

    metrics["operator"] = Path.home().name
    with pytest.raises(runner.BenchmarkError, match="local username"):
        runner.publish({"demo": (metrics, "# otherwise portable\n")})


def test_privacy_scan_accepts_relative_imports_and_rejects_nested_local_paths(
    tmp_path,
) -> None:
    relative_imports = [
        "contains \"import envFormData from '../env/classes/FormData.js';\"",
        (
            'does not contain "import PlatformFormData from '
            "'../platform/node/classes/FormData.js';\""
        ),
    ]
    portable_metrics = {
        "acquisition": {"repository": "https://github.com/axios/axios.git"},
        "environment": {"shiftory_module": "src/shiftory/__init__.py"},
        "runs": [{"assertions": {"results": [{"detail": item} for item in relative_imports]}}],
    }
    runner.validate_public_artifacts(
        portable_metrics,
        "\n".join(relative_imports),
        roots=(tmp_path,),
    )

    local_root = tmp_path / "local-install"
    private_fields = [
        (
            {"acquisition": {"workspace": str(local_root / "sources/axios")}},
            r'\$\["acquisition"\]\["workspace"\]',
        ),
        (
            {"environment": {"python": str(local_root / "bin/python")}},
            r'\$\["environment"\]\["python"\]',
        ),
        (
            {"runs": [{"cache": {"path": str(local_root / "cache/scenario")}}]},
            r'\$\["runs"\]\[0\]\["cache"\]\["path"\]',
        ),
        (
            {
                "graph": {
                    "diagnostics": [
                        {
                            "code": "graphora_provider_provenance",
                            "module_file": str(local_root / "site-packages/graphora/__init__.py"),
                            "direct_url": (local_root / "wheelhouse/graphora.whl").as_uri(),
                        }
                    ]
                }
            },
            r'\$\["graph"\]\["diagnostics"\]\[0\]\["module_file"\]',
        ),
        (
            {
                "graph": {
                    "diagnostics": [
                        {
                            "code": "graphora_provider_provenance",
                            "direct_url": (local_root / "wheelhouse/graphora.whl").as_uri(),
                        }
                    ]
                }
            },
            r'\$\["graph"\]\["diagnostics"\]\[0\]\["direct_url"\]',
        ),
    ]
    for private_metrics, location in private_fields:
        with pytest.raises(
            runner.BenchmarkError,
            match=rf"metrics JSON {location}",
        ):
            runner.validate_public_artifacts(
                private_metrics,
                "# report\n",
                roots=(local_root,),
            )

    with pytest.raises(runner.BenchmarkError, match=r"report Markdown line 2"):
        runner.validate_public_artifacts(
            portable_metrics,
            f"# report\nGenerated from {local_root / 'workspace'}\n",
            roots=(local_root,),
        )


def test_published_artifacts_contain_no_local_roots(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    committed_implementation(repository)
    monkeypatch.setattr(runner, "ROOT", repository)
    monkeypatch.setattr(runner, "load_scenarios", lambda: {"demo": {}})
    implementation = runner.implementation_identity(repository)
    execution = runner.executed_package_identity(repository)
    runner.publish(
        {
            "demo": (
                publication_metrics(repository, implementation, execution),
                "# portable report\n",
            )
        }
    )

    combined = (repository / "docs/benchmarks/demo/metrics-v1.json").read_text() + (
        repository / "docs/benchmarks/demo/report.md"
    ).read_text()
    assert str(repository) not in combined
    assert str(Path.home()) not in combined
    assert Path.home().name not in combined
    assert "site-packages" not in combined
    assert "copilot-worktrees" not in combined
