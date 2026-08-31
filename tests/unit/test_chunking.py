from __future__ import annotations

import copy
import os
import subprocess
import time

import pytest
from jsonschema import Draft202012Validator

from shiftory.chunking import planner as planner_module
from shiftory.chunking.composer import compose_chunks
from shiftory.chunking.planner import (
    AgentBudget,
    chunk_identity,
    estimate_tokens,
    plan_chunks,
    plan_identity,
    sha256_json,
)
from shiftory.errors import ChunkBudgetError, CompositionError
from shiftory.evidence.builder import AnalyzeOptions, analyze_complete
from shiftory.models.json import canonical_json
from shiftory.schemas import load_schema


def _large_evidence(repo_factory, file_count: int = 6) -> dict:
    repository = repo_factory()
    for index in range(file_count):
        (repository / f"file{index}.py").write_text(
            f"def value_{index}():\n    return 1\n", encoding="utf-8"
        )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add files"], cwd=repository, check=True)
    for index in range(file_count):
        (repository / f"file{index}.py").write_text(
            f"def value_{index}():\n    return {index + 2}\n", encoding="utf-8"
        )
    return analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()


def _chunk_explanations(chunks: tuple[dict, ...]) -> tuple[dict, ...]:
    outputs = []
    for chunk in chunks:
        owner_id = f"change-{chunk['index']}"
        targets = [
            target["evidence_id"]
            for work_item in chunk["work_items"]
            for target in work_item["ownership_targets"]
        ]
        citations = [
            context["citation_id"]
            for work_item in chunk["work_items"]
            for context in work_item["contexts"]
        ]
        outputs.append(
            {
                "schema": "shiftory.chunk-explanation/v1",
                "chunk_id": chunk["id"],
                "comparison_identity": chunk["comparison_identity"],
                "ledger_sha256": chunk["ledger_sha256"],
                "summary": f"Chunk {chunk['index']} changes return values.",
                "items": [
                    {
                        "id": owner_id,
                        "kind": "behavioral",
                        "title": f"Change return values in chunk {chunk['index']}",
                        "before": "The functions returned one.",
                        "after": "The functions return their new values.",
                        "confidence": "extracted",
                        "citations": citations,
                    }
                ],
                "coverage_owners": [
                    {"evidence_id": target, "owner_id": owner_id} for target in targets
                ],
            }
        )
    return tuple(outputs)


def test_token_estimate_and_effective_budget_are_explicit() -> None:
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1) == 1
    assert estimate_tokens(4) == 1
    assert estimate_tokens(5) == 2
    budget = AgentBudget(10_000, 1_000)
    assert budget.effective_max_bytes == 4_000
    assert budget.to_dict()["token_estimate_formula"] == "ceil(canonical_utf8_bytes/4)"


def test_chunk_explanation_schema_matches_final_item_constraints() -> None:
    invalid = {
        "schema": "shiftory.chunk-explanation/v1",
        "chunk_id": "chunk",
        "comparison_identity": "comparison",
        "ledger_sha256": "0" * 64,
        "summary": "Summary.",
        "items": [
            {
                "id": "item",
                "kind": "behavioral",
                "title": "Missing before and after",
                "confidence": "extracted",
                "citations": [],
            }
        ],
        "coverage_owners": [],
    }
    assert list(Draft202012Validator(load_schema("chunk-explanation")).iter_errors(invalid))


def test_fallback_planning_is_strict_schema_valid_and_deterministic(repo_factory) -> None:
    evidence = _large_evidence(repo_factory)
    budget = AgentBudget(4_000, 1_000)

    first = plan_chunks(evidence, budget)
    second = plan_chunks(evidence, budget)

    assert first == second
    assert first.plan["grouping_strategy"] == "deterministic-fallback"
    assert len(first.chunks) >= 2
    assert first.plan["id"] == plan_identity(first.plan)
    assert not list(Draft202012Validator(load_schema("chunk-plan")).iter_errors(first.plan))
    for chunk in first.chunks:
        assert chunk["id"] == chunk_identity(chunk)
        assert chunk["budget"]["actual_bytes"] == len(canonical_json(chunk).encode())
        assert chunk["budget"]["actual_bytes"] <= budget.effective_max_bytes
        assert chunk["budget"]["estimated_tokens"] == estimate_tokens(
            chunk["budget"]["actual_bytes"]
        )
        assert not list(Draft202012Validator(load_schema("chunk")).iter_errors(chunk))


def test_graph_relationships_co_locate_changed_files_without_owning_them(repo_factory) -> None:
    evidence = _large_evidence(repo_factory, file_count=3)
    evidence["graph"] = {
        **evidence["graph"],
        "status": "available",
        "facts": [
            {
                "id": "fact-definition",
                "kind": "definition",
                "side": "after",
                "path": "file0.py",
                "line": 1,
                "symbol": "shared",
                "target": None,
                "confidence": "extracted",
                "provenance": "graphora:tree-sitter",
            },
            {
                "id": "fact-caller",
                "kind": "caller",
                "side": "after",
                "path": "file2.py",
                "line": None,
                "symbol": "shared",
                "target": "caller",
                "confidence": "inferred",
                "provenance": "graphora:unknown-parser",
            },
        ],
    }

    planned = plan_chunks(evidence, AgentBudget(4_000))

    first_paths = {item["file"]["new_path"] for item in planned.chunks[0]["work_items"]}
    assert planned.plan["grouping_strategy"] == "graph-guided"
    assert first_paths == {"file0.py", "file2.py"}
    planned_targets = {
        target for entry in planned.plan["chunks"] for target in entry["ownership_target_ids"]
    }
    graph_ids = {fact["id"] for fact in evidence["graph"]["facts"]}
    assert not planned_targets & graph_ids


def test_graph_unavailable_uses_the_same_deterministic_hierarchy_order(
    repo_factory,
) -> None:
    evidence = _large_evidence(repo_factory, file_count=3)
    disabled = plan_chunks(evidence, AgentBudget(4_000))
    unavailable_evidence = copy.deepcopy(evidence)
    unavailable_evidence["graph"] = {
        **unavailable_evidence["graph"],
        "status": "unavailable",
        "facts": [],
        "diagnostics": [{"code": "graphora_unavailable", "message": "Graphora is unavailable."}],
    }

    unavailable = plan_chunks(unavailable_evidence, AgentBudget(4_000))

    disabled_paths = [
        [item["file"]["new_path"] for item in chunk["work_items"]] for chunk in disabled.chunks
    ]
    unavailable_paths = [
        [item["file"]["new_path"] for item in chunk["work_items"]] for chunk in unavailable.chunks
    ]
    assert unavailable.plan["grouping_strategy"] == "deterministic-fallback"
    assert unavailable_paths == disabled_paths


def test_repeated_symbol_grouping_uses_bounded_union_work(monkeypatch) -> None:
    path_count = 2_000
    paths = {f"file-{index:04d}.py" for index in range(path_count)}
    facts = [
        {
            "kind": kind,
            "side": "after",
            "symbol": "shared",
            "path": path,
        }
        for kind in ("definition", "caller")
        for path in sorted(paths)
    ]
    unions = 0
    real_union = planner_module._DisjointSet.union

    def counted_union(self, left, right):
        nonlocal unions
        unions += 1
        return real_union(self, left, right)

    monkeypatch.setattr(planner_module._DisjointSet, "union", counted_union)
    started = time.perf_counter()
    strategy, components = planner_module._graph_components(
        {"graph": {"status": "available", "facts": facts}}, paths
    )
    elapsed = time.perf_counter() - started

    assert strategy == "graph-guided"
    assert set(components.values()) == {"file-0000.py"}
    assert unions < path_count * 2
    assert elapsed < 2


def test_many_small_chunks_index_graph_facts_once_and_remain_deterministic(
    repo_factory,
) -> None:
    evidence = _large_evidence(repo_factory, file_count=200)

    class CountingFact(dict):
        path_reads = 0

        def get(self, key, default=None):
            if key == "path":
                type(self).path_reads += 1
            return super().get(key, default)

    evidence["graph"] = {
        **evidence["graph"],
        "status": "available",
        "facts": [
            CountingFact(
                {
                    "id": f"fact-{kind}-{index:04d}",
                    "kind": kind,
                    "side": "after",
                    "path": f"file{index}.py",
                    "line": 1 if kind == "definition" else None,
                    "symbol": "shared",
                    "target": None if kind == "definition" else "shared",
                    "confidence": "extracted",
                    "provenance": "graphora:test",
                }
            )
            for kind in ("definition", "caller")
            for index in range(200)
        ],
    }
    budget = AgentBudget(3_000)

    first = plan_chunks(evidence, budget)
    path_reads = CountingFact.path_reads
    CountingFact.path_reads = 0
    second = plan_chunks(evidence, budget)

    assert len(first.chunks) >= 50
    assert first == second
    assert path_reads < 5_000
    assert CountingFact.path_reads < 5_000
    assert all(
        chunk["budget"]["actual_bytes"] <= budget.effective_max_bytes for chunk in first.chunks
    )


def test_concentrated_omitted_graph_facts_bound_chunk_finalization(
    repo_factory, monkeypatch
) -> None:
    evidence = _large_evidence(repo_factory, file_count=1)
    budget = AgentBudget(3_000)
    chunk = plan_chunks(evidence, budget).chunks[0]
    facts = [
        {
            "id": f"fact-{index:05d}",
            "kind": "definition",
            "side": "after",
            "path": "file0.py",
            "line": 1,
            "symbol": f"symbol-{index:05d}",
            "target": None,
            "confidence": "extracted",
            "provenance": "graphora:test",
        }
        for index in range(5_000)
    ]
    indexed = planner_module._index_graph_facts(facts)
    finalizations = 0
    real_finalize = planner_module._finalize_chunk

    def counted_finalize(value):
        nonlocal finalizations
        finalizations += 1
        return real_finalize(value)

    monkeypatch.setattr(planner_module, "_finalize_chunk", counted_finalize)
    first = planner_module._include_graph_facts(chunk, indexed, budget)
    first_finalizations = finalizations
    finalizations = 0
    second = planner_module._include_graph_facts(chunk, indexed, budget)

    assert first == second
    assert 0 < len(first["omitted_graph_fact_ids"]) < len(facts)
    assert first["budget"]["actual_bytes"] <= budget.effective_max_bytes
    assert first_finalizations < 10
    assert finalizations < 10


def test_irreducible_atom_fails_instead_of_breaking_the_cap(repo_factory) -> None:
    evidence = _large_evidence(repo_factory, file_count=1)
    with pytest.raises(ChunkBudgetError, match="irreducible ownership atom") as caught:
        plan_chunks(evidence, AgentBudget(1_000))
    assert caught.value.details
    assert caught.value.details["required_bytes"] > 1_000


def test_impossible_zero_budget_empty_comparison_fails_typed(repo_factory) -> None:
    repository = repo_factory()
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    with pytest.raises(ChunkBudgetError, match="empty-comparison evidence"):
        plan_chunks(evidence, AgentBudget(0))


def test_exact_budget_edge_accepts_fit_and_rejects_one_byte_less(repo_factory) -> None:
    evidence = _large_evidence(repo_factory, file_count=1)
    lower, upper = 0, 10_000
    while lower < upper:
        candidate = (lower + upper) // 2
        try:
            plan_chunks(evidence, AgentBudget(candidate))
        except ChunkBudgetError:
            lower = candidate + 1
        else:
            upper = candidate
    exact = lower
    fitted = plan_chunks(evidence, AgentBudget(exact))

    assert fitted.chunks[0]["budget"]["actual_bytes"] <= exact
    with pytest.raises(ChunkBudgetError):
        plan_chunks(evidence, AgentBudget(exact - 1))


def test_one_oversized_source_line_fails_as_an_irreducible_retrieval(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("small = 1\nold_" + "x" * 6_000 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "large line"], cwd=repository, check=True)
    (repository / "app.py").write_text("small = 2\nnew_" + "y" * 6_000 + "\n", encoding="utf-8")
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()

    with pytest.raises(ChunkBudgetError, match="single recorded source line"):
        plan_chunks(evidence, AgentBudget(3_000))


def test_large_range_segmentation_bounds_serialized_probe_work(monkeypatch) -> None:
    text = "\n".join(f"value_{index:05d}=" + "x" * 20 for index in range(5_000))
    citation = {
        "id": "citation",
        "path": "large.py",
        "side": "after",
        "start_line": 1,
        "end_line": 5_000,
        "content_hash": "0" * 64,
    }
    real_retrieval_record = planner_module._retrieval_record
    probed_characters = 0

    def counted_retrieval_record(*args, **kwargs):
        nonlocal probed_characters
        probed_characters += len(kwargs["text"])
        return real_retrieval_record(*args, **kwargs)

    monkeypatch.setattr(planner_module, "_retrieval_record", counted_retrieval_record)
    retrievals = {}
    range_ids = planner_module._retrieval_ranges(
        citation,
        text=text,
        comparison_identity="comparison",
        ledger_sha256="0" * 64,
        budget=AgentBudget(1_000),
        retrievals=retrievals,
    )

    assert len(range_ids) > 1
    assert probed_characters < len(text) * 8


def test_non_text_units_are_atomic_and_compose_without_changed_lines(repo_factory) -> None:
    repository = repo_factory()
    os.chmod(repository / "app.py", 0o755)
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    planned = plan_chunks(evidence, AgentBudget(3_000))
    chunk = planned.chunks[0]
    target = chunk["work_items"][0]["ownership_targets"][0]
    output = {
        "schema": "shiftory.chunk-explanation/v1",
        "chunk_id": chunk["id"],
        "comparison_identity": chunk["comparison_identity"],
        "ledger_sha256": chunk["ledger_sha256"],
        "summary": "The executable mode changes.",
        "items": [
            {
                "id": "mode-change",
                "kind": "structural",
                "title": "Make the source executable",
                "statement": "The source file gains executable mode.",
                "confidence": "extracted",
                "citations": [target["evidence_id"]],
            }
        ],
        "coverage_owners": [{"evidence_id": target["evidence_id"], "owner_id": "mode-change"}],
    }

    composed = compose_chunks(evidence, planned.plan, planned.chunks, (output,))

    assert target["kind"] == "non_text_unit"
    assert composed["coverage_owners"] == output["coverage_owners"]


def test_composition_expands_span_owners_to_exact_global_line_ownership(
    repo_factory,
) -> None:
    evidence = _large_evidence(repo_factory)
    planned = plan_chunks(evidence, AgentBudget(4_000))
    outputs = _chunk_explanations(planned.chunks)

    composed = compose_chunks(evidence, planned.plan, planned.chunks, outputs)

    line_ids = {
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    }
    span_ids = {span["id"] for file in evidence["files"] for span in file["spans"]}
    owner_ids = {owner["evidence_id"] for owner in composed["coverage_owners"]}
    assert line_ids | span_ids <= owner_ids
    assert composed["summary"].split("\n\n")[0].startswith("Chunk 1")


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda plan, chunks, outputs: outputs.__setitem__(0, outputs[1]),
            "binds to a different chunk",
        ),
        (
            lambda plan, chunks, outputs: outputs[1]["coverage_owners"].append(
                copy.deepcopy(outputs[0]["coverage_owners"][0])
            ),
            "owns unassigned targets",
        ),
        (
            lambda plan, chunks, outputs: outputs[1]["items"].__setitem__(
                0, {**outputs[1]["items"][0], "id": outputs[0]["items"][0]["id"]}
            ),
            "duplicate explanation item id",
        ),
        (
            lambda plan, chunks, outputs: plan["chunks"][0].__setitem__("payload_sha256", "0" * 64),
            "plan identity is invalid",
        ),
    ],
)
def test_composition_rejects_duplicate_stale_and_tampered_inputs(
    repo_factory, mutation, match
) -> None:
    evidence = _large_evidence(repo_factory)
    planned = plan_chunks(evidence, AgentBudget(4_000))
    plan = copy.deepcopy(planned.plan)
    chunks = list(copy.deepcopy(planned.chunks))
    outputs = list(copy.deepcopy(_chunk_explanations(planned.chunks)))
    mutation(plan, chunks, outputs)

    with pytest.raises(CompositionError, match="composition failed") as caught:
        compose_chunks(evidence, plan, tuple(chunks), tuple(outputs))
    assert match in str(caught.value.details)


def test_composition_rejects_missing_chunk_and_modified_ledger(repo_factory) -> None:
    evidence = _large_evidence(repo_factory)
    planned = plan_chunks(evidence, AgentBudget(4_000))
    outputs = _chunk_explanations(planned.chunks)
    with pytest.raises(CompositionError) as missing:
        compose_chunks(evidence, planned.plan, planned.chunks, outputs[:-1])
    assert "all planned chunks" in str(missing.value.details)

    changed = copy.deepcopy(evidence)
    changed["diagnostics"].append({"code": "tampered", "message": "tampered"})
    with pytest.raises(CompositionError) as tampered:
        compose_chunks(changed, planned.plan, planned.chunks, outputs)
    assert "ledger digest is stale" in str(tampered.value.details)
    assert planned.plan["ledger_sha256"] == sha256_json(evidence)
