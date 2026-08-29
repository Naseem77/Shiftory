from __future__ import annotations

import importlib.metadata
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import graphora
import pytest

from shiftory.cache.store import CACHE_SCHEMA
from shiftory.errors import GraphoraError
from shiftory.graph.provider import (
    GRAPH_CACHE_SCHEMA,
    GRAPHORA_PACKAGE_CODE_SHA256,
    GRAPHORA_VERSION,
    GRAPHORA_WHEEL_SHA256,
    GraphoraAdapter,
    enrich_with_graphora,
)

_direct_url = importlib.metadata.distribution("graphora-kg").read_text("direct_url.json")
_editable = bool(
    _direct_url and json.loads(_direct_url).get("dir_info", {}).get("editable") is True
)
pytestmark = pytest.mark.skipif(
    _editable,
    reason="real Graphora contract requires a non-editable installed artifact",
)


def _fixture(repo_factory) -> Path:
    snapshot = repo_factory()
    (snapshot / "app.py").write_text(
        "def helper():\n    return 1\n\ndef changed():\n    return helper()\n",
        encoding="utf-8",
    )
    (snapshot / "consumer.py").write_text(
        "from app import changed\n\ndef consume():\n    return changed()\n",
        encoding="utf-8",
    )
    tests = snapshot / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import changed\n\ndef test_changed():\n    assert changed() == 1\n",
        encoding="utf-8",
    )
    return snapshot


def _data_dir(snapshot: Path, source_key: str = "after-source") -> Path:
    return snapshot / ".cache" / "repositories" / ("a" * 64) / "graphora" / source_key


def _enrich(snapshot: Path, source_key: str = "after-source"):
    return GraphoraAdapter().enrich(
        snapshot,
        project="caller-project-is-not-cache-identity",
        data_dir=_data_dir(snapshot, source_key),
        patch="",
        changed_paths=("app.py",),
        side="after",
        changed_lines={"app.py": (4, 5)},
    )


def test_pinned_package_root_contract_and_embedded_backend(repo_factory) -> None:
    assert importlib.metadata.version("graphora-kg") == GRAPHORA_VERSION
    assert graphora.__version__ == GRAPHORA_VERSION
    for name in (
        "open_store",
        "index_repository",
        "parse_code_file",
        "blast_radius",
    ):
        assert callable(getattr(graphora, name))
    assert "backend" in inspect.signature(graphora.open_store).parameters
    assert "store" in inspect.signature(graphora.index_repository).parameters

    result = _enrich(_fixture(repo_factory))

    assert result.status == "available"
    provenance = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic["code"] == "graphora_provider_provenance"
    )
    assert provenance["distribution_version"] == GRAPHORA_VERSION
    assert provenance["module_file"] == str(Path(graphora.__file__).resolve())
    assert provenance["editable"] is False
    assert provenance["artifact_verified"] is True
    assert provenance["artifact_errors"] == []
    assert provenance["metadata_license"] == "MIT"
    assert provenance["package_code_sha256"] == GRAPHORA_PACKAGE_CODE_SHA256
    if provenance["artifact_sha256"] is not None:
        assert provenance["artifact_sha256"] == GRAPHORA_WHEEL_SHA256
    kinds = {fact.kind for fact in result.facts}
    assert {
        "definition",
        "changed_symbol",
        "caller",
        "callee",
        "static_test",
        "importer",
    } <= kinds
    assert all(not Path(fact.path).is_absolute() for fact in result.facts)
    assert all(fact.line is None or fact.line >= 1 for fact in result.facts)
    assert {fact.provenance for fact in result.facts if fact.kind == "definition"} <= {
        "graphora:tree-sitter",
        "graphora:regex",
        "graphora:unknown-parser",
    }
    serialized = repr(result)
    assert "risk_score" not in serialized
    assert "fix_count" not in serialized
    assert "last_broke_at" not in serialized


def test_pinned_artifact_excludes_incompatible_tree_sitter_runtime() -> None:
    assert importlib.metadata.metadata("graphora-kg")["License"] == "MIT"
    requirements = importlib.metadata.requires("graphora-kg") or []
    tree_sitter = next(
        requirement for requirement in requirements if requirement.startswith("tree-sitter!")
    )
    assert tree_sitter == "tree-sitter!=0.26.0,>=0.23"
    assert importlib.metadata.version("tree-sitter") == "0.25.2"


def test_graph_cache_reuses_valid_index_and_rebuilds_corruption(repo_factory, monkeypatch) -> None:
    snapshot = _fixture(repo_factory)
    first = _enrich(snapshot)
    assert first.cache_key is not None
    artifact = _data_dir(snapshot) / first.cache_key
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == GRAPH_CACHE_SCHEMA
    assert manifest["cache_schema"] == CACHE_SCHEMA
    assert manifest["graphora_version"] == GRAPHORA_VERSION
    assert manifest["cache_key"] == first.cache_key
    assert manifest["repository_id"] == "a" * 64
    assert manifest["source_snapshot_id"] == "after-source"
    graph_file = next(artifact.glob("shiftory-*.json"))

    def unexpected_index(*args, **kwargs):
        raise AssertionError("Graphora ran in the main Shiftory process")

    monkeypatch.setattr(graphora, "index_repository", unexpected_index)
    assert _enrich(snapshot).facts == first.facts

    graph_file.write_text("{corrupt", encoding="utf-8")
    rebuilt = _enrich(snapshot)
    assert rebuilt.status == "available"
    assert rebuilt.facts == first.facts
    json.loads(graph_file.read_text(encoding="utf-8"))

    (snapshot / "app.py").write_text(
        (snapshot / "app.py").read_text(encoding="utf-8").replace("return 1", "return 2"),
        encoding="utf-8",
    )
    changed = _enrich(snapshot)
    assert changed.cache_key != first.cache_key


def test_graph_cache_separates_snapshots_and_side_facts(repo_factory) -> None:
    snapshot = _fixture(repo_factory)
    after = _enrich(snapshot, "after-source")
    before = GraphoraAdapter().enrich(
        snapshot,
        project="same-project",
        data_dir=_data_dir(snapshot, "before-source"),
        patch="",
        changed_paths=("app.py",),
        side="before",
        changed_lines={"app.py": (4,)},
    )
    assert before.cache_key != after.cache_key
    assert {fact.side for fact in before.facts} == {"before"}
    assert {fact.side for fact in after.facts} == {"after"}


def test_enclosing_and_ambiguous_symbol_semantics(repo_factory) -> None:
    snapshot = _fixture(repo_factory)
    (snapshot / "alternate.py").write_text(
        "def changed():\n    return 2\n",
        encoding="utf-8",
    )
    result = GraphoraAdapter().enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "enclosing"),
        patch="",
        changed_paths=("app.py",),
        changed_lines={"app.py": (5,)},
    )
    enclosing = next(fact for fact in result.facts if fact.kind == "enclosing_symbol")
    assert enclosing.symbol == "changed"
    assert enclosing.confidence == "inferred"
    assert any(
        fact.kind == "definition" and fact.path == "alternate.py" and fact.confidence == "ambiguous"
        for fact in result.facts
    )


def test_module_level_change_does_not_seed_preceding_symbol(repo_factory) -> None:
    snapshot = _fixture(repo_factory)
    (snapshot / "app.py").write_text(
        "def helper():\n    return 1\n\ndef changed():\n    return helper()\n\nmodule_value = 2\n",
        encoding="utf-8",
    )

    result = GraphoraAdapter().enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "module-level"),
        patch="",
        changed_paths=("app.py",),
        changed_lines={"app.py": (7,)},
    )

    unresolved = [fact for fact in result.facts if fact.kind == "changed_file"]
    assert [(fact.path, fact.line, fact.confidence) for fact in unresolved] == [
        ("app.py", 7, "unresolved")
    ]
    assert unresolved[0].provenance.endswith(":no-verified-containment")
    assert not {
        "changed_symbol",
        "enclosing_symbol",
        "caller",
        "callee",
        "static_test",
        "importer",
    } & {fact.kind for fact in result.facts}


def test_adjacent_and_nested_symbols_use_verified_source_ranges(repo_factory) -> None:
    snapshot = repo_factory()
    (snapshot / "app.py").write_text(
        "def first():\n"
        "    return 1\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 2\n"
        "    return inner()\n\n"
        "def adjacent():\n"
        "    return 3\n",
        encoding="utf-8",
    )
    adapter = GraphoraAdapter()

    inner = adapter.enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "nested-inner"),
        patch="",
        changed_paths=("app.py",),
        changed_lines={"app.py": (6,)},
    )
    outer = adapter.enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "nested-outer"),
        patch="",
        changed_paths=("app.py",),
        changed_lines={"app.py": (7,)},
    )
    adjacent = adapter.enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "adjacent"),
        patch="",
        changed_paths=("app.py",),
        changed_lines={"app.py": (10,)},
    )

    assert {fact.symbol for fact in inner.facts if fact.kind == "enclosing_symbol"} == {"inner"}
    assert {fact.symbol for fact in outer.facts if fact.kind == "enclosing_symbol"} == {"outer"}
    assert {fact.symbol for fact in adjacent.facts if fact.kind == "enclosing_symbol"} == {
        "adjacent"
    }


def test_resolved_and_module_level_lines_are_reported_independently(repo_factory) -> None:
    snapshot = repo_factory()
    (snapshot / "app.py").write_text(
        "def changed():\n    return 1\n\nmodule_value = 2\n",
        encoding="utf-8",
    )

    result = GraphoraAdapter().enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "mixed-containment"),
        patch="",
        changed_paths=("app.py",),
        side="before",
        changed_lines={"app.py": (2, 4)},
    )

    assert {fact.symbol for fact in result.facts if fact.kind == "enclosing_symbol"} == {"changed"}
    unresolved = next(fact for fact in result.facts if fact.kind == "changed_file")
    assert (unresolved.side, unresolved.path, unresolved.line) == ("before", "app.py", 4)
    assert all(
        fact.line is None
        or 1 <= fact.line <= len((snapshot / fact.path).read_text(encoding="utf-8").splitlines())
        for fact in result.facts
        if fact.path
    )


def test_brace_ranges_are_verified_and_unstructured_ranges_stay_unresolved(repo_factory) -> None:
    snapshot = repo_factory()
    (snapshot / "app.js").write_text(
        "function first() {\n"
        "  return 1;\n"
        "}\n\n"
        "function second() {\n"
        "  return 2;\n"
        "}\n\n"
        "const moduleValue = 3;\n",
        encoding="utf-8",
    )
    (snapshot / "app.rb").write_text(
        "def first\n  1\nend\n\ndef second\n  2\nend\n",
        encoding="utf-8",
    )
    adapter = GraphoraAdapter()

    javascript = adapter.enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "javascript-containment"),
        patch="",
        changed_paths=("app.js",),
        changed_lines={"app.js": (6, 9)},
    )
    ruby = adapter.enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "ruby-unresolved"),
        patch="",
        changed_paths=("app.rb",),
        changed_lines={"app.rb": (6,)},
    )

    assert {fact.symbol for fact in javascript.facts if fact.kind == "enclosing_symbol"} == {
        "second"
    }
    assert {(fact.path, fact.line) for fact in javascript.facts if fact.kind == "changed_file"} == {
        ("app.js", 9)
    }
    assert not [fact for fact in ruby.facts if fact.kind == "enclosing_symbol"]
    ruby_unresolved = next(fact for fact in ruby.facts if fact.kind == "changed_file")
    assert (ruby_unresolved.path, ruby_unresolved.line) == ("app.rb", 6)


def test_regex_fallback_is_explicit_and_never_claims_extracted(repo_factory) -> None:
    snapshot = _fixture(repo_factory)

    def regex_parse(path, content):
        del path, content
        return SimpleNamespace(
            symbols=[
                SimpleNamespace(
                    name="changed",
                    line=4,
                    signature="def changed():",
                    confidence="extracted",
                )
            ],
            parser_used="regex",
        )

    fake_graphora = SimpleNamespace(parse_code_file=regex_parse)
    facts, _, _ = GraphoraAdapter()._parse_changed_files(
        fake_graphora,
        snapshot,
        ("app.py",),
        {"app.py": (4,)},
        "after",
    )

    changed = next(fact for fact in facts if fact.kind == "changed_symbol")
    assert changed.provenance == "graphora:regex"
    assert changed.confidence == "inferred"


def test_unsupported_unresolved_and_off_semantics(repo_factory) -> None:
    snapshot = _fixture(repo_factory)
    (snapshot / "notes.xyz").write_text("not code\n", encoding="utf-8")
    unsupported = GraphoraAdapter().enrich(
        snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "unsupported"),
        patch="",
        changed_paths=("notes.xyz",),
        changed_lines={"notes.xyz": (1,)},
    )
    assert unsupported.status == "available"
    fact = next(fact for fact in unsupported.facts if fact.kind == "unsupported")
    assert fact.confidence == "unavailable"
    assert fact.provenance == "graphora:unsupported-language"
    with pytest.raises(GraphoraError, match="incomplete: unsupported fact"):
        enrich_with_graphora(
            GraphoraAdapter(),
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=_data_dir(snapshot, "unsupported-required"),
            patch="",
            changed_paths=("notes.xyz",),
            changed_lines={"notes.xyz": (1,)},
        )

    disabled = enrich_with_graphora(
        None,
        "off",
        snapshot=snapshot,
        project="project",
        data_dir=_data_dir(snapshot, "off"),
        patch="",
        changed_paths=("app.py",),
    )
    assert disabled.status == "disabled"
    assert not _data_dir(snapshot, "off").exists()
