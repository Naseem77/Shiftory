from __future__ import annotations

import hashlib
import subprocess

import pytest
from jsonschema import validate

from shiftory.evidence import builder as builder_module
from shiftory.evidence.builder import (
    AnalyzeOptions,
    _apply_evidence_budget,
    _merge_graph_results,
    analyze,
)
from shiftory.git import source as source_module
from shiftory.models.core import (
    ChangeUnit,
    Evidence,
    FileChange,
    GraphFact,
    GraphResult,
    SourceCitation,
)
from shiftory.models.json import canonical_json
from shiftory.schemas import load_schema


def _hierarchy_ids(evidence: dict) -> set[str]:
    return {
        item["id"]
        for file in evidence["files"]
        for collection in ("units", "hunks", "spans")
        for item in file[collection]
    } | {
        line["id"] for file in evidence["files"] for hunk in file["hunks"] for line in hunk["lines"]
    }


def test_evidence_hierarchy_citations_metrics_and_schema(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text(
        "def value():\n    first = 2\n    return first\n",
        encoding="utf-8",
    )

    evidence = analyze(
        AnalyzeOptions(repo=repository, graphora="off", max_evidence_bytes=1_000_000)
    ).to_dict()
    file = evidence["files"][0]
    hunk_ids = {hunk["id"] for hunk in file["hunks"]}
    span_ids = {span["id"] for span in file["spans"]}
    line_ids = {line["id"] for hunk in file["hunks"] for line in hunk["lines"]}

    assert {
        hunk_id for unit in file["units"] if unit["kind"] == "text" for hunk_id in unit["hunk_ids"]
    } == hunk_ids
    assert {span_id for hunk in file["hunks"] for span_id in hunk["span_ids"]} == span_ids
    assert {line_id for span in file["spans"] for line_id in span["line_ids"]} == line_ids
    assert len(_hierarchy_ids(evidence)) == (
        evidence["metrics"]["units"]
        + evidence["metrics"]["hunks"]
        + evidence["metrics"]["spans"]
        + evidence["metrics"]["changed_lines"]
    )

    citations = file["citations"]
    assert len(citations) == evidence["metrics"]["source_citations"] == len(file["spans"])
    for citation in citations:
        assert citation["start_line"] <= citation["end_line"]
        assert citation["content_hash"] == hashlib.sha256(citation["text"].encode()).hexdigest()
        assert citation["retrieval"] is None
        assert citation["omitted"] is False

    metrics = evidence["metrics"]
    assert metrics["changed_lines"] == metrics["added_lines"] + metrics["deleted_lines"]
    assert metrics["files"] == len(evidence["files"])
    assert metrics["raw_patch_bytes"] > 0
    assert metrics["evidence_bytes"] == len(canonical_json(evidence).encode())
    validate(evidence, load_schema("evidence"))


def test_budget_preserves_hierarchy_and_records_every_omitted_context(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text(
        "def value():\n" + "\n".join(f"    value_{index} = {index}" for index in range(400)) + "\n",
        encoding="utf-8",
    )
    complete = analyze(
        AnalyzeOptions(repo=repository, graphora="off", max_evidence_bytes=1_000_000)
    ).to_dict()
    bounded = analyze(
        AnalyzeOptions(repo=repository, graphora="off", max_evidence_bytes=1)
    ).to_dict()

    assert _hierarchy_ids(bounded) == _hierarchy_ids(complete)
    bounded_citations = [citation for file in bounded["files"] for citation in file["citations"]]
    assert bounded_citations
    omitted_citations = [citation for citation in bounded_citations if citation["omitted"]]
    assert omitted_citations
    assert all(
        citation["text"] is None and citation["omitted"] and citation["retrieval"]
        for citation in omitted_citations
    )
    assert {item["citation_id"] for item in bounded["omissions"]} == {
        citation["id"] for citation in omitted_citations
    }
    assert bounded["metrics"]["omitted_source_contexts"] == len(omitted_citations)
    assert bounded["metrics"]["evidence_bytes"] == len(canonical_json(bounded).encode())
    budget = next(
        item for item in bounded["diagnostics"] if item["code"] == "evidence_budget_exceeded"
    )
    assert budget["actual_bytes"] == bounded["metrics"]["evidence_bytes"]
    assert canonical_json(bounded) == canonical_json(
        analyze(AnalyzeOptions(repo=repository, graphora="off", max_evidence_bytes=1)).to_dict()
    )
    validate(bounded, load_schema("evidence"))

    fitting_budget = bounded["metrics"]["evidence_bytes"] + 256
    fitting = analyze(
        AnalyzeOptions(
            repo=repository,
            graphora="off",
            max_evidence_bytes=fitting_budget,
        )
    ).to_dict()
    assert fitting["metrics"]["evidence_bytes"] <= fitting_budget
    assert any(item["code"] == "evidence_context_omitted" for item in fitting["diagnostics"])


def _synthetic_budget_evidence(citation_count: int) -> Evidence:
    citations = tuple(
        SourceCitation(
            f"citation-{index:05d}",
            "large.py",
            "after",
            index + 1,
            index + 1,
            f"{index:05d}:" + ("source context " * 64),
            hashlib.sha256(str(index).encode()).hexdigest(),
        )
        for index in range(citation_count)
    )
    file = FileChange(
        "large.py",
        "large.py",
        "modified",
        "old",
        "new",
        "100644",
        "100644",
        (ChangeUnit("unit-kept", "text"),),
        (),
        (),
        citations,
        "source",
        "extracted",
    )
    fact = GraphFact(
        "fact-kept",
        "definition",
        "after",
        "large.py",
        1,
        "value",
        None,
        "extracted",
        "graphora:test",
    )
    return Evidence(
        "shiftory.evidence/v1",
        "test",
        {"identity": "comparison-kept"},
        {"id": "repository-kept"},
        (file,),
        GraphResult("available", "graphora", "test", (fact,)),
        ({"id": "group-kept", "unit_ids": ["unit-kept"]},),
        (),
        (),
        {"omitted_source_contexts": 0, "evidence_bytes": 0},
    )


@pytest.mark.parametrize(
    ("citation_count", "budget_ratio", "expected_code"),
    [
        (6_000, 0.0, "evidence_budget_exceeded"),
        (16_000, 0.65, "evidence_context_omitted"),
    ],
)
def test_budget_serialization_is_bounded_for_large_synthetic_ledgers(
    monkeypatch,
    citation_count: int,
    budget_ratio: float,
    expected_code: str,
) -> None:
    evidence = _synthetic_budget_evidence(citation_count)
    complete_size = len(canonical_json(evidence.to_dict()).encode())
    budget = max(1, int(complete_size * budget_ratio))
    serializations = 0
    real_canonical_json = builder_module.canonical_json

    def counted_canonical_json(value) -> str:
        nonlocal serializations
        serializations += 1
        return real_canonical_json(value)

    monkeypatch.setattr(builder_module, "canonical_json", counted_canonical_json)
    bounded = _apply_evidence_budget(evidence, budget)
    result = bounded.to_dict()

    assert serializations <= 8
    assert result["files"][0]["units"][0]["id"] == "unit-kept"
    assert result["graph"]["facts"][0]["id"] == "fact-kept"
    assert result["groups"][0]["id"] == "group-kept"
    assert len(result["files"][0]["citations"]) == citation_count
    assert any(item["code"] == expected_code for item in result["diagnostics"])
    assert result["metrics"]["evidence_bytes"] == len(canonical_json(result).encode())
    if expected_code == "evidence_context_omitted":
        assert result["metrics"]["evidence_bytes"] <= budget
        assert 0 < result["metrics"]["omitted_source_contexts"] < citation_count
    else:
        assert result["metrics"]["evidence_bytes"] > budget


def test_source_reads_are_per_file_and_fresh_per_analysis(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    original_lines = [f"value_{index} = {index}" for index in range(400)]
    (repository / "app.py").write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "large source"], cwd=repository, check=True)

    process_calls = 0
    real_optional_git = source_module._optional_git

    def counted_optional_git(*args, **kwargs):
        nonlocal process_calls
        process_calls += 1
        return real_optional_git(*args, **kwargs)

    monkeypatch.setattr(source_module, "_optional_git", counted_optional_git)
    first_lines = [
        line if index % 2 == 0 else f"value_{index} = {index + 1}"
        for index, line in enumerate(original_lines)
    ]
    (repository / "app.py").write_text("\n".join(first_lines) + "\n", encoding="utf-8")
    first = analyze(
        AnalyzeOptions(
            repo=repository,
            graphora="off",
            context_lines=0,
            max_evidence_bytes=10_000_000,
        )
    ).to_dict()
    first_after_hashes = {
        citation["content_hash"]
        for citation in first["files"][0]["citations"]
        if citation["side"] == "after"
    }

    second_lines = [
        line if index % 2 == 0 else f"value_{index} = {index + 2}"
        for index, line in enumerate(original_lines)
    ]
    (repository / "app.py").write_text("\n".join(second_lines) + "\n", encoding="utf-8")
    second = analyze(
        AnalyzeOptions(
            repo=repository,
            graphora="off",
            context_lines=0,
            max_evidence_bytes=10_000_000,
        )
    ).to_dict()
    second_after_hashes = {
        citation["content_hash"]
        for citation in second["files"][0]["citations"]
        if citation["side"] == "after"
    }

    assert first["metrics"]["spans"] >= 400
    assert second["metrics"]["spans"] >= 400
    assert process_calls == 4
    assert first_after_hashes != second_after_hashes


def test_rename_citations_use_precise_before_and_after_paths(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text(
        "def value():\n    first = 1\n    second = 2\n    return first + second\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-qam", "expand source"], cwd=repository, check=True)
    subprocess.run(["git", "mv", "app.py", "renamed.py"], cwd=repository, check=True)
    (repository / "renamed.py").write_text(
        "def value():\n    first = 1\n    second = 2\n    return first + second + 1\n",
        encoding="utf-8",
    )

    evidence = analyze(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    file = evidence["files"][0]
    assert file["classification"] == "rename"
    paths = {(citation["side"], citation["path"]) for citation in file["citations"]}
    assert paths == {("before", "app.py"), ("after", "renamed.py")}


def test_graph_merge_retains_unresolved_facts_and_provenance() -> None:
    before_fact = GraphFact(
        "before-fact",
        "definition",
        "before",
        "app.py",
        1,
        "value",
        None,
        "extracted",
        "graphora:tree-sitter",
    )
    unavailable_fact = GraphFact(
        "unavailable-fact",
        "unsupported",
        "after",
        "",
        None,
        "value",
        None,
        "unavailable",
        "graphora:unsupported-language",
    )
    merged = _merge_graph_results(
        GraphResult("available", "graphora", "0.2.1", (before_fact,)),
        GraphResult("unavailable", "graphora", "0.2.1", (unavailable_fact,)),
    )

    assert merged.status == "unavailable"
    assert merged.facts == (before_fact, unavailable_fact)
    assert merged.facts[1].confidence == "unavailable"
    assert merged.facts[1].provenance == "graphora:unsupported-language"
    assert merged.diagnostics[0]["side"] == "after"
