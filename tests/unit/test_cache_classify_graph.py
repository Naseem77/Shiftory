from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from shiftory.cache.store import CacheStore
from shiftory.classify.rules import classify_file
from shiftory.errors import CacheError, GraphoraError
from shiftory.graph.provider import GraphoraAdapter, enrich_with_graphora
from shiftory.models.core import ChangedLine, ChangeUnit, FileChange, GraphResult, TextHunk

VERIFIED_PROVENANCE = {
    "schema": "shiftory.graphora-provider-provenance/v1",
    "distribution": "graphora-kg",
    "distribution_version": "0.2.1",
    "module_file": "/installed/graphora/__init__.py",
    "direct_url": None,
    "artifact_sha256": None,
    "metadata_license": "MIT",
    "editable": False,
    "package_code_sha256": "74d1761010cc20ad27d3bb61f30a2e01ad19c1b0a4f6737edb8e2951183cc4e4",
    "artifact_verified": True,
    "artifact_errors": [],
}
EDITABLE_PROVENANCE = {
    **VERIFIED_PROVENANCE,
    "module_file": "/checkout/graphora/__init__.py",
    "direct_url": {"dir_info": {"editable": True}, "url": "file:///checkout"},
    "editable": True,
    "artifact_verified": False,
    "artifact_errors": [
        "editable_install",
        "module_origin_mismatch",
        "package_inventory_mismatch",
    ],
}


def _verified_probe(self, request, cwd):
    del self, request, cwd
    return VERIFIED_PROVENANCE


def _adapter(*, timeout_seconds: float = 300.0) -> GraphoraAdapter:
    adapter = GraphoraAdapter.__new__(GraphoraAdapter)
    adapter.timeout_seconds = timeout_seconds
    return adapter


def file_for(path: str, kind: str = "text") -> FileChange:
    line = ChangedLine("line", "after", None, 1, 0, "value", "hash")
    hunk = TextHunk("hunk", 1, 0, 1, 1, "", (), (line,), 1)
    unit = ChangeUnit("unit", kind, ("hunk",))  # type: ignore[arg-type]
    return FileChange(path, path, "modified", None, None, None, None, (unit,), (hunk,), ())


@pytest.mark.parametrize(
    ("path", "classification"),
    [
        ("tests/test_app.py", "tests"),
        ("docs/guide.md", "docs"),
        ("package-lock.json", "dependency"),
        ("settings.yaml", "configuration"),
        ("schema.json", "schema"),
        ("generated/app.min.js", "generated"),
        ("src/app.py", "behavioral"),
    ],
)
def test_classification_rules(path: str, classification: str) -> None:
    assert classify_file(file_for(path))[0] == classification


@pytest.mark.parametrize(
    ("status", "kind", "classification"),
    [
        ("added", "text", "added"),
        ("deleted", "text", "deleted"),
        ("modified", "binary", "binary"),
        ("renamed", "rename", "rename"),
        ("modified", "mode", "mode"),
        ("modified", "unsupported", "unsupported"),
    ],
)
def test_change_nature_classifications_are_extracted(
    status: str, kind: str, classification: str
) -> None:
    file = file_for("asset.dat", kind)
    file = FileChange(
        file.old_path,
        file.new_path,
        status,
        file.old_blob,
        file.new_blob,
        file.old_mode,
        file.new_mode,
        file.units,
        file.hunks,
        file.spans,
    )
    assert classify_file(file) == (classification, "extracted")


def test_formatting_classification_requires_whitespace_equivalence() -> None:
    before = ChangedLine("before", "before", 1, None, 0, "value=1", "hash")
    after = ChangedLine("after", "after", None, 1, 1, "value = 1", "hash")
    hunk = TextHunk("hunk", 1, 1, 1, 1, "", (), (before, after), 1)
    unit = ChangeUnit("unit", "text", ("hunk",))
    file = FileChange(
        "values.txt",
        "values.txt",
        "modified",
        None,
        None,
        None,
        None,
        (unit,),
        (hunk,),
        (),
    )
    assert classify_file(file) == ("formatting", "inferred")


def test_copy_without_text_is_structural() -> None:
    unit = ChangeUnit("unit", "copy", (), {"old_path": "a", "new_path": "b"})
    file = FileChange(
        "a.txt",
        "b.txt",
        "copied",
        None,
        None,
        None,
        None,
        (unit,),
        (),
        (),
    )
    assert classify_file(file) == ("structural", "extracted")


def test_cache_manifest_round_trip_and_scoped_clear(repo_factory) -> None:
    repository = repo_factory()
    cache_root = repository / ".cache"
    store = CacheStore("a" * 64, cache_root=cache_root)
    store.atomic_write("entry.json", {"value": 1})
    assert store.read_manifest("entry.json")["value"] == 1
    assert not list(store.root.rglob("*.partial"))
    lock_path = cache_root / ".locks" / f"{'a' * 64}.lock"
    with store.lock():
        assert lock_path.is_file()
    cleared = store.clear()
    assert cleared.name == "a" * 64
    assert not cleared.exists()
    assert lock_path.is_file()
    assert cache_root.exists()


def test_cache_rejects_unsafe_identity_and_entry_paths(repo_factory) -> None:
    repository = repo_factory()
    with pytest.raises(GraphoraError):
        GraphoraAdapter._changed_paths(("../outside.py",))
    with pytest.raises(CacheError, match="invalid repository cache identity"):
        CacheStore("abc", cache_root=repository / ".cache")

    store = CacheStore("b" * 64, cache_root=repository / ".cache")
    with pytest.raises(CacheError, match="unsafe cache entry path"):
        store.atomic_write("../outside.json", {"value": 1})


def test_cache_ignores_malformed_or_wrong_schema_manifests(repo_factory) -> None:
    repository = repo_factory()
    store = CacheStore("c" * 64, cache_root=repository / ".cache")
    store.ensure()
    (store.root / "broken.json").write_text("{", encoding="utf-8")
    (store.root / "stale.json").write_text('{"schema":"old"}', encoding="utf-8")
    assert store.read_manifest("broken.json") is None
    assert store.read_manifest("stale.json") is None


def test_cache_clear_refuses_repository_path_symlink(repo_factory) -> None:
    repository = repo_factory()
    cache_root = repository / ".cache"
    outside = repository / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("safe", encoding="utf-8")
    store = CacheStore("d" * 64, cache_root=cache_root)
    store.root.parent.mkdir(parents=True)
    store.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(CacheError, match="unsafe cache path"):
        store.clear()
    assert sentinel.read_text(encoding="utf-8") == "safe"


class FailingProvider:
    name = "fake"
    version = "0"

    def enrich(self, snapshot: Path, **kwargs) -> GraphResult:
        raise RuntimeError("offline")


def test_graphora_failure_is_honest_in_auto_and_fatal_when_required(repo_factory) -> None:
    snapshot = repo_factory()
    result = enrich_with_graphora(
        FailingProvider(),
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("app.py",),
    )
    assert result.status == "unavailable"
    assert result.diagnostics[0]["code"] == "graphora_unavailable"
    with pytest.raises(GraphoraError):
        enrich_with_graphora(
            FailingProvider(),
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("app.py",),
        )


def test_editable_graphora_origin_is_explicitly_unavailable(repo_factory) -> None:
    if importlib.metadata.version("graphora-kg") != "0.2.1":
        pytest.skip("installed Graphora provider is not the expected runtime version")
    direct_url = importlib.metadata.distribution("graphora-kg").read_text("direct_url.json")
    if not direct_url or not json.loads(direct_url).get("dir_info", {}).get("editable"):
        pytest.skip("installed Graphora provider is not editable")
    snapshot = repo_factory()
    result = enrich_with_graphora(
        None,
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("app.py",),
    )
    provenance = result.diagnostics[0]["provider_provenance"]
    assert result.status == "unavailable"
    assert provenance["distribution_version"] == "0.2.1"
    assert provenance["editable"] is True
    assert provenance["artifact_verified"] is False
    assert provenance["module_file"].endswith("/graphora/__init__.py")
    assert len(provenance["package_code_sha256"]) == 64
    with pytest.raises(GraphoraError, match="editable"):
        enrich_with_graphora(
            None,
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("app.py",),
        )


def test_unexpected_provider_never_reaches_enrichment_worker(repo_factory, monkeypatch) -> None:
    snapshot = repo_factory()

    def editable_probe(self, request, cwd):
        del self, request, cwd
        return EDITABLE_PROVENANCE

    def unexpected_worker(*args, **kwargs):
        raise AssertionError("unverified Graphora provider reached enrichment")

    monkeypatch.setattr(GraphoraAdapter, "_probe_provider", editable_probe)
    monkeypatch.setattr(GraphoraAdapter, "_run_worker", unexpected_worker)
    result = enrich_with_graphora(
        _adapter(),
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("app.py",),
    )
    assert result.status == "unavailable"
    assert result.diagnostics[0]["provider_provenance"] == EDITABLE_PROVENANCE
    with pytest.raises(GraphoraError, match="editable"):
        enrich_with_graphora(
            _adapter(),
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("app.py",),
        )


@pytest.mark.parametrize(
    ("returncode", "message"),
    [(-11, "signal SIGSEGV (11)"), (7, "status 7")],
)
def test_graphora_worker_signal_and_nonzero_are_isolated(
    repo_factory, monkeypatch, returncode: int, message: str
) -> None:
    snapshot = repo_factory()

    def failed_worker(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode, "", "native failure")

    monkeypatch.setattr(GraphoraAdapter, "_probe_provider", _verified_probe)
    monkeypatch.setattr("shiftory.graph.provider.subprocess.run", failed_worker)
    result = enrich_with_graphora(
        _adapter(),
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("app.py",),
    )
    assert result.status == "unavailable"
    assert message in result.diagnostics[0]["message"]
    assert result.diagnostics[0]["provider_provenance"] == VERIFIED_PROVENANCE

    with pytest.raises(GraphoraError, match=re.escape(message)):
        enrich_with_graphora(
            _adapter(),
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("app.py",),
        )


def test_graphora_worker_malformed_output_is_isolated(repo_factory, monkeypatch) -> None:
    snapshot = repo_factory()
    calls = []

    def malformed_worker(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, '{"schema":"wrong"}\n', "")

    monkeypatch.setattr(GraphoraAdapter, "_probe_provider", _verified_probe)
    monkeypatch.setattr("shiftory.graph.provider.subprocess.run", malformed_worker)
    result = enrich_with_graphora(
        _adapter(),
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("b.py", "a.py", "a.py"),
        changed_lines={"b.py": (3, 2, 2), "a.py": (1,)},
    )
    assert result.status == "unavailable"
    assert "invalid result envelope" in result.diagnostics[0]["message"]
    assert result.diagnostics[0]["provider_provenance"] == VERIFIED_PROVENANCE
    command = calls[0][0][0]
    options = calls[0][1]
    request = json.loads(options["input"])
    assert command[1:5] == ["-I", "-B", "-X", "faulthandler"]
    assert request["schema"] == "shiftory.graphora-worker-request/v1"
    assert request["snapshot"] == str(snapshot.resolve())
    assert request["data_dir"] == str((snapshot / ".graph").resolve())
    assert request["changed_paths"] == ["a.py", "b.py"]
    assert request["changed_lines"] == {"a.py": [1], "b.py": [2, 3]}
    assert options["cwd"] == snapshot.resolve()
    assert options["timeout"] == 300.0
    assert set(options["env"]) == {
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONHASHSEED",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
    }
    with pytest.raises(GraphoraError, match="invalid result envelope"):
        enrich_with_graphora(
            _adapter(),
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("b.py", "a.py"),
            changed_lines={"b.py": (3, 2), "a.py": (1,)},
        )


def test_graphora_worker_timeout_is_isolated(repo_factory, monkeypatch) -> None:
    snapshot = repo_factory()

    def timed_out_worker(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(GraphoraAdapter, "_probe_provider", _verified_probe)
    monkeypatch.setattr("shiftory.graph.provider.subprocess.run", timed_out_worker)
    adapter = _adapter(timeout_seconds=0.01)
    result = enrich_with_graphora(
        adapter,
        "auto",
        snapshot=snapshot,
        project="project",
        data_dir=snapshot / ".graph",
        patch="",
        changed_paths=("app.py",),
    )
    assert result.status == "unavailable"
    assert "timed out after 0.01 seconds" in result.diagnostics[0]["message"]

    with pytest.raises(GraphoraError, match="timed out"):
        enrich_with_graphora(
            adapter,
            "required",
            snapshot=snapshot,
            project="project",
            data_dir=snapshot / ".graph",
            patch="",
            changed_paths=("app.py",),
        )


def test_graphora_importer_paths_are_normalized_without_risk_fields(repo_factory) -> None:
    snapshot = repo_factory()
    (snapshot / "consumer.py").write_text("from app import value\n", encoding="utf-8")
    impact = SimpleNamespace(
        unresolved=[],
        symbols=[
            SimpleNamespace(
                name="value",
                kind="Function",
                path="app.py",
                line=1,
                signature="def value():",
                risk_score=99,
                fix_count=12,
                last_broke_at="ignored",
                callers=[],
                callees=[],
                tests=[],
                importers=["consumer.py"],
            )
        ],
    )

    facts = _adapter()._normalize_impact(
        impact,
        snapshot,
        ("app.py",),
        "after",
        set(),
    )

    importer = next(fact for fact in facts if fact.kind == "importer")
    assert importer.path == "consumer.py"
    assert importer.target is None
    serialized = repr(facts)
    assert "risk_score" not in serialized
    assert "last_broke_at" not in serialized


def test_graphora_confidence_and_paths_are_normalized() -> None:
    assert GraphoraAdapter._line_number("03") == 3
    assert GraphoraAdapter._line_number(True) is None
    fact = GraphoraAdapter._fact(
        "caller", "after", "./src\\app.py", "3", "changed", "caller", "ambiguous", "graphora"
    )
    assert fact.path == "src/app.py"
    assert fact.line == 3
    assert fact.confidence == "ambiguous"


def test_graphora_rejects_malformed_blast_radius(repo_factory) -> None:
    snapshot = repo_factory()
    with pytest.raises(GraphoraError, match="malformed blast radius"):
        _adapter()._normalize_impact(
            SimpleNamespace(symbols=None, unresolved=None),
            snapshot,
            ("app.py",),
            "after",
            set(),
        )
