from __future__ import annotations

import copy
import os
import subprocess

import pytest

from shiftory.chunking.planner import AgentBudget, plan_chunks, plan_identity
from shiftory.chunking.retrieval import retrieve_source_range
from shiftory.diff.identity import stable_id
from shiftory.errors import GitError, RetrievalError
from shiftory.evidence.builder import AnalyzeOptions, analyze_complete
from shiftory.git.repository import ScopeSpec


def _changed_repository(repo_factory):
    repository = repo_factory()
    (repository / "large.py").write_text(
        "\n".join(f"old_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add large source"], cwd=repository, check=True)
    (repository / "large.py").write_text(
        "\n".join(f"new_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    return repository


def _descriptor(repository, evidence):
    return {
        "repository_root": str(repository.resolve()),
        "repository_id": evidence["repository"]["id"],
    }


def _plan(evidence):
    planned = plan_chunks(evidence, AgentBudget(3_000))
    assert planned.plan["retrieval_ranges"]
    return planned


def test_retrieval_reads_only_a_recorded_hash_verified_range(repo_factory) -> None:
    repository = _changed_repository(repo_factory)
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    planned = _plan(evidence)
    record = planned.plan["retrieval_ranges"][0]

    result = retrieve_source_range(
        _descriptor(repository, evidence), evidence, planned.plan, record["id"]
    )

    assert result["schema"] == "shiftory.retrieval/v1"
    assert result["range_id"] == record["id"]
    assert result["actual_bytes"] == record["response_bytes"] <= 3_000
    assert result["text"].startswith("old_001")
    with pytest.raises(RetrievalError, match="not uniquely recorded"):
        retrieve_source_range(_descriptor(repository, evidence), evidence, planned.plan, "unknown")


def test_retrieval_rejects_traversal_hash_and_range_manifest_tampering(
    repo_factory,
) -> None:
    repository = _changed_repository(repo_factory)
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    planned = _plan(evidence)

    traversal = copy.deepcopy(planned.plan)
    traversal_record = traversal["retrieval_ranges"][0]
    traversal_record["path"] = "../secret"
    identity_payload = {
        key: traversal_record[key]
        for key in (
            "citation_id",
            "path",
            "side",
            "start_line",
            "end_line",
            "content_hash",
        )
    }
    traversal_record["id"] = stable_id("source-range", identity_payload)
    traversal["id"] = plan_identity(traversal)
    with pytest.raises(RetrievalError, match="path is unsafe"):
        retrieve_source_range(
            _descriptor(repository, evidence),
            evidence,
            traversal,
            traversal_record["id"],
        )

    bad_hash = copy.deepcopy(planned.plan)
    bad_record = bad_hash["retrieval_ranges"][0]
    bad_record["content_hash"] = "0" * 64
    identity_payload = {
        key: bad_record[key]
        for key in (
            "citation_id",
            "path",
            "side",
            "start_line",
            "end_line",
            "content_hash",
        )
    }
    bad_record["id"] = stable_id("source-range", identity_payload)
    bad_hash["id"] = plan_identity(bad_hash)
    with pytest.raises(RetrievalError, match="content-hash verification"):
        retrieve_source_range(
            _descriptor(repository, evidence), evidence, bad_hash, bad_record["id"]
        )

    bad_range = copy.deepcopy(planned.plan)
    bad_range["retrieval_ranges"][0]["end_line"] = 999
    bad_range["id"] = plan_identity(bad_range)
    with pytest.raises(RetrievalError, match="outside its global citation"):
        retrieve_source_range(
            _descriptor(repository, evidence),
            evidence,
            bad_range,
            bad_range["retrieval_ranges"][0]["id"],
        )


def test_retrieval_fails_closed_when_mutable_source_changes(repo_factory) -> None:
    repository = _changed_repository(repo_factory)
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    planned = _plan(evidence)
    range_id = planned.plan["retrieval_ranges"][0]["id"]
    (repository / "large.py").write_text("changed again\n", encoding="utf-8")

    with pytest.raises(GitError, match="changed during recorded source retrieval"):
        retrieve_source_range(_descriptor(repository, evidence), evidence, planned.plan, range_id)


def test_committed_retrieval_uses_git_objects_not_mutable_checkout(repo_factory) -> None:
    repository = _changed_repository(repo_factory)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "change large source"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    evidence = analyze_complete(
        AnalyzeOptions(
            repo=repository,
            graphora="off",
            scope=ScopeSpec(commit=head),
        )
    ).to_dict()
    planned = _plan(evidence)
    record = planned.plan["retrieval_ranges"][-1]
    (repository / "large.py").write_text("unrelated checkout state\n", encoding="utf-8")

    result = retrieve_source_range(
        _descriptor(repository, evidence), evidence, planned.plan, record["id"]
    )

    assert "new_120" in result["text"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX filename semantics")
def test_retrieval_accepts_a_recorded_posix_backslash_filename(repo_factory) -> None:
    repository = repo_factory()
    path = repository / r"dir\large.py"
    path.write_text(
        "\n".join(f"old_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add backslash path"], cwd=repository, check=True)
    path.write_text(
        "\n".join(f"new_{index:03d} = {index}" for index in range(1, 121)) + "\n",
        encoding="utf-8",
    )
    evidence = analyze_complete(AnalyzeOptions(repo=repository, graphora="off")).to_dict()
    planned = _plan(evidence)
    record = next(
        value for value in planned.plan["retrieval_ranges"] if value["path"] == r"dir\large.py"
    )

    result = retrieve_source_range(
        _descriptor(repository, evidence), evidence, planned.plan, record["id"]
    )

    assert result["path"] == r"dir\large.py"
    assert result["text"].startswith(("old_001", "new_001"))
