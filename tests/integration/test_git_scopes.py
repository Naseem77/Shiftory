from __future__ import annotations

import importlib.metadata
import json
import shlex
import subprocess
import sys

import pytest

import shiftory.git.repository as repository_module
import shiftory.git.source as source_module
from shiftory.cache.store import CacheStore
from shiftory.errors import GitError, GitFilterError, ScopeError
from shiftory.evidence.builder import AnalyzeOptions, analyze, build_evidence
from shiftory.git.repository import (
    ScopeSpec,
    acquire_patch,
    resolve_comparison,
    resolve_repository,
)
from shiftory.git.source import materialize_snapshot, source_bytes
from shiftory.models.core import GraphResult
from shiftory.models.json import canonical_json

_graphora_direct_url = importlib.metadata.distribution("graphora-kg").read_text("direct_url.json")
_graphora_editable = bool(
    _graphora_direct_url
    and json.loads(_graphora_direct_url).get("dir_info", {}).get("editable") is True
)


def changed(repository, scope: ScopeSpec) -> dict:
    return analyze(AnalyzeOptions(repo=repository, scope=scope, graphora="off")).to_dict()


def add_submodule(repository, repo_factory):
    submodule = repo_factory()
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(submodule),
            "vendor",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "commit", "-qam", "add submodule"], cwd=repository, check=True)
    (submodule / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "advance submodule"], cwd=submodule, check=True)
    subprocess.run(["git", "fetch", "-q"], cwd=repository / "vendor", check=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repository / "vendor", check=True)
    return submodule


def test_working_staged_unstaged_and_untracked_scopes(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
    (repository / "new file.py").write_text("answer = 42\n", encoding="utf-8")

    staged = changed(repository, ScopeSpec(staged=True))
    unstaged = changed(repository, ScopeSpec(unstaged=True))
    working = changed(repository, ScopeSpec())

    assert staged["metrics"]["added_lines"] == 1
    assert unstaged["metrics"]["added_lines"] == 1
    assert working["metrics"]["files"] == 2
    assert {file["new_path"] for file in working["files"]} == {"app.py", "new file.py"}


def test_path_scope_exact_recursive_multiple_and_prefix_boundary(repo_factory) -> None:
    repository = repo_factory()
    auth = repository / "src" / "auth"
    auth.mkdir(parents=True)
    (auth / "login.py").write_text("login = 1\n", encoding="utf-8")
    (auth / "nested").mkdir()
    (auth / "nested" / "token.py").write_text("token = 1\n", encoding="utf-8")
    (repository / "src" / "authentication.py").write_text("other = 1\n", encoding="utf-8")
    (repository / "docs").mkdir()
    (repository / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")

    exact = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(paths=("src/auth/login.py",)),
            graphora="off",
        )
    ).to_dict()
    recursive = analyze(
        AnalyzeOptions(repo=repository, scope=ScopeSpec(paths=("src/auth",)), graphora="off")
    ).to_dict()
    multiple = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(paths=("src/auth/login.py", "docs")),
            graphora="off",
        )
    ).to_dict()

    assert [file["new_path"] for file in exact["files"]] == ["src/auth/login.py"]
    assert {file["new_path"] for file in recursive["files"]} == {
        "src/auth/login.py",
        "src/auth/nested/token.py",
    }
    assert {file["new_path"] for file in multiple["files"]} == {
        "docs/guide.md",
        "src/auth/login.py",
    }
    assert recursive["comparison"]["paths"] == ["src/auth"]


def test_path_scope_accepts_inside_absolute_and_rejects_unsafe_or_unmatched(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    absolute = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(paths=(str(repository / "app.py"),)),
            graphora="off",
        )
    ).to_dict()

    assert absolute["comparison"]["paths"] == ["app.py"]
    assert [file["new_path"] for file in absolute["files"]] == ["app.py"]
    for path, message in [
        (str(repository.parent / "outside.py"), "outside the repository"),
        ("src/../app.py", "traversal"),
        (".", "Repository root"),
        ("missing.py", "does not match any changed path"),
    ]:
        with pytest.raises(ScopeError, match=message):
            analyze(
                AnalyzeOptions(
                    repo=repository,
                    scope=ScopeSpec(paths=(path,)),
                    graphora="off",
                )
            )


def test_path_scope_matches_rename_sides_and_deleted_files_in_committed_scope(repo_factory) -> None:
    repository = repo_factory()
    (repository / "deleted.py").write_text("deleted = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add paths"], cwd=repository, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    subprocess.run(["git", "mv", "app.py", "renamed.py"], cwd=repository, check=True)
    (repository / "deleted.py").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "rename and delete"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

    for selected in ("app.py", "renamed.py"):
        result = analyze(
            AnalyzeOptions(
                repo=repository,
                scope=ScopeSpec(range=f"{base}..{head}", paths=(selected,)),
                graphora="off",
            )
        ).to_dict()
        files = [(file["old_path"], file["new_path"], file["status"]) for file in result["files"]]
        assert files == [("app.py", "renamed.py", "renamed")]
    deleted = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(range=f"{base}..{head}", paths=("deleted.py",)),
            graphora="off",
        )
    ).to_dict()
    assert [(file["old_path"], file["new_path"], file["status"]) for file in deleted["files"]] == [
        ("deleted.py", None, "deleted")
    ]


def test_path_scope_includes_untracked_files_and_is_identity_bound(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    nested = repository / "new"
    nested.mkdir()
    (nested / "one.py").write_text("one = 1\n", encoding="utf-8")
    (nested / "two.py").write_text("two = 2\n", encoding="utf-8")

    app = analyze(
        AnalyzeOptions(repo=repository, scope=ScopeSpec(paths=("app.py",)), graphora="off")
    ).to_dict()
    new_first = analyze(
        AnalyzeOptions(repo=repository, scope=ScopeSpec(paths=("new",)), graphora="off")
    ).to_dict()
    new_second = analyze(
        AnalyzeOptions(repo=repository, scope=ScopeSpec(paths=("new",)), graphora="off")
    ).to_dict()

    assert {file["new_path"] for file in new_first["files"]} == {"new/one.py", "new/two.py"}
    assert app["comparison"]["identity"] != new_first["comparison"]["identity"]
    assert canonical_json(new_first) == canonical_json(new_second)


def test_path_scope_treats_git_pathspec_syntax_literally(repo_factory) -> None:
    repository = repo_factory()
    literal = repository / ":(attr)odd.py"
    literal.write_text("value = 1\n", encoding="utf-8")

    result = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(paths=(":(attr)odd.py",)),
            graphora="off",
        )
    ).to_dict()

    assert [file["new_path"] for file in result["files"]] == [":(attr)odd.py"]


@pytest.mark.parametrize("scope", [ScopeSpec(), ScopeSpec(unstaged=True), ScopeSpec(staged=True)])
def test_diff_paths_ignore_configured_mnemonic_prefixes(repo_factory, scope: ScopeSpec) -> None:
    repository = repo_factory()
    subprocess.run(["git", "config", "diff.mnemonicPrefix", "true"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    if scope.staged:
        subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)

    result = changed(repository, scope)

    assert result["files"][0]["old_path"] == "app.py"
    assert result["files"][0]["new_path"] == "app.py"


def test_pr_scope_uses_merge_base_when_base_tip_advanced(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    common = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    subprocess.run(["git", "switch", "-qc", "feature"], cwd=repository, check=True)
    (repository / "app.py").write_text("feature = True\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "feature"], cwd=repository, check=True)
    feature = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    subprocess.run(["git", "switch", "-q", "-"], cwd=repository, check=True)
    (repository / "base.py").write_text("base = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "advance base"], cwd=repository, check=True)
    base_tip = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    monkeypatch.setattr(
        repository_module,
        "_resolve_pr",
        lambda _root, _number, _remote: {"base": base_tip, "head": feature},
    )

    comparison = resolve_comparison(repository, ScopeSpec(pr=1))
    result = changed(repository, ScopeSpec(pr=1))

    assert comparison.base_sha == common
    assert comparison.head_sha == feature
    assert {file["new_path"] for file in result["files"]} == {"app.py"}


def test_default_scope_is_nul_safe_and_includes_empty_untracked_files(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
    unusual = {
        "space b/name.py": b"value = 1\n",
        "tab\tname.py": b"value = 2\n",
        "line\nname.py": b"value = 3\n",
        "back\\name.py": b"value = 4\n",
        "empty.py": b"",
    }
    for name, content in unusual.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    first = changed(repository, ScopeSpec())
    second = changed(repository, ScopeSpec())
    unstaged = changed(repository, ScopeSpec(unstaged=True))

    assert canonical_json(first) == canonical_json(second)
    assert {file["new_path"] for file in first["files"]} == {"app.py", *unusual}
    assert first["metrics"]["added_lines"] == 5
    assert first["metrics"]["deleted_lines"] == 1
    empty = next(file for file in first["files"] if file["new_path"] == "empty.py")
    assert [unit["kind"] for unit in empty["units"]] == ["mode"]
    assert {file["new_path"] for file in unstaged["files"]} == {"app.py"}


def test_each_working_scope_maps_to_the_correct_source_side(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
    root = resolve_repository(repository)

    staged = resolve_comparison(root, ScopeSpec(staged=True))
    unstaged = resolve_comparison(root, ScopeSpec(unstaged=True))
    working = resolve_comparison(root, ScopeSpec())

    assert source_bytes(staged, "app.py", "before").endswith(b"return 1\n")
    assert source_bytes(staged, "app.py", "after").endswith(b"return 2\n")
    assert source_bytes(unstaged, "app.py", "before").endswith(b"return 2\n")
    assert source_bytes(unstaged, "app.py", "after").endswith(b"return 3\n")
    assert source_bytes(working, "app.py", "before").endswith(b"return 1\n")
    assert source_bytes(working, "app.py", "after").endswith(b"return 3\n")

    subprocess.run(["git", "reset", "-q", "HEAD", "--", "app.py"], cwd=repository, check=True)
    reset_unstaged = resolve_comparison(root, ScopeSpec(unstaged=True))
    reset_working = resolve_comparison(root, ScopeSpec())
    assert reset_unstaged.identity != unstaged.identity
    assert reset_working.identity != working.identity


@pytest.mark.parametrize("scope", [ScopeSpec(), ScopeSpec(unstaged=True), ScopeSpec(staged=True)])
@pytest.mark.parametrize("attributes", [False, True], ids=["autocrlf", "attributes"])
def test_git_normalized_crlf_sources_match_patch_without_checkout_mutation(
    repo_factory, scope: ScopeSpec, attributes: bool
) -> None:
    repository = repo_factory()
    if attributes:
        (repository / ".gitattributes").write_text("*.py text eol=crlf\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitattributes"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "attributes"], cwd=repository, check=True)
    else:
        subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=repository, check=True)
    worktree_content = b"def value():\r\n    return 2\r\n"
    (repository / "app.py").write_bytes(worktree_content)
    if scope.staged:
        subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    status_before = subprocess.check_output(
        ["git", "status", "--porcelain=v2", "-z"], cwd=repository
    )
    index_before = subprocess.check_output(["git", "write-tree"], cwd=repository)
    objects_before = subprocess.check_output(["git", "count-objects", "-v"], cwd=repository)

    result = changed(repository, scope)

    file = next(item for item in result["files"] if item["new_path"] == "app.py")
    after_lines = [
        line["content"]
        for hunk in file["hunks"]
        for line in hunk["lines"]
        if line["side"] == "after"
    ]
    assert after_lines == ["    return 2"]
    after_citation = next(item for item in file["citations"] if item["side"] == "after")
    assert after_citation["text"] == "    return 2"
    assert (repository / "app.py").read_bytes() == worktree_content
    assert subprocess.check_output(["git", "write-tree"], cwd=repository) == index_before
    assert subprocess.check_output(["git", "count-objects", "-v"], cwd=repository) == objects_before
    assert (
        subprocess.check_output(["git", "status", "--porcelain=v2", "-z"], cwd=repository)
        == status_before
    )


@pytest.mark.parametrize("scope", [ScopeSpec(), ScopeSpec(unstaged=True), ScopeSpec(staged=True)])
def test_clean_filter_sources_and_snapshots_match_git_patch(repo_factory, scope: ScopeSpec) -> None:
    repository = repo_factory()
    filter_script = repository / "clean_filter.py"
    filter_script.write_text(
        "import sys\n"
        "content = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(content.replace(b'WORK', b'CLEAN'))\n",
        encoding="utf-8",
    )
    (repository / ".gitattributes").write_text("*.txt filter=canonical text\n", encoding="utf-8")
    note = repository / "note.txt"
    note.write_text("value=OLD\n", encoding="utf-8")
    subprocess.run(
        [
            "git",
            "config",
            "filter.canonical.clean",
            f"{shlex.quote(sys.executable)} clean_filter.py",
        ],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "add", ".gitattributes", "clean_filter.py", "note.txt"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "filtered base"], cwd=repository, check=True)
    worktree_content = b"value=WORK\r\n"
    note.write_bytes(worktree_content)
    if scope.staged:
        subprocess.run(["git", "add", "note.txt"], cwd=repository, check=True)
    status_before = subprocess.check_output(
        ["git", "status", "--porcelain=v2", "-z"], cwd=repository
    )
    objects_before = subprocess.check_output(["git", "count-objects", "-v"], cwd=repository)
    comparison = resolve_comparison(resolve_repository(repository), scope)

    result = analyze(AnalyzeOptions(repo=repository, scope=scope, graphora="off")).to_dict()

    file = next(item for item in result["files"] if item["new_path"] == "note.txt")
    assert [
        line["content"]
        for hunk in file["hunks"]
        for line in hunk["lines"]
        if line["side"] == "after"
    ] == ["value=CLEAN"]
    assert next(item for item in file["citations"] if item["side"] == "after")["text"] == (
        "value=CLEAN"
    )
    assert source_bytes(comparison, "note.txt", "after") == b"value=CLEAN\n"
    cache = CacheStore(
        comparison.repository_id,
        cache_root=repository.parent / f"{repository.name}-{comparison.mode}-filter-cache",
    )
    snapshot, _key = materialize_snapshot(comparison, cache, "after")
    assert snapshot is not None
    assert (snapshot / "note.txt").read_bytes() == b"value=CLEAN\n"
    assert note.read_bytes() == worktree_content
    assert subprocess.check_output(["git", "count-objects", "-v"], cwd=repository) == objects_before
    assert (
        subprocess.check_output(["git", "status", "--porcelain=v2", "-z"], cwd=repository)
        == status_before
    )


def test_unavailable_clean_filter_fails_with_typed_diagnostic(repo_factory) -> None:
    repository = repo_factory()
    (repository / ".gitattributes").write_text("*.py filter=broken text\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitattributes"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "filter attributes"], cwd=repository, check=True)
    subprocess.run(["git", "config", "filter.broken.clean", "false"], cwd=repository, check=True)
    subprocess.run(["git", "config", "filter.broken.required", "true"], cwd=repository, check=True)
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    with pytest.raises(GitFilterError) as failed:
        changed(repository, ScopeSpec())

    assert failed.value.code == "git_filter_error"
    assert failed.value.details
    assert failed.value.details["reason"] == "git_filter_failed"
    assert "clean filter 'broken' failed" in failed.value.details["cause"]


def test_untracked_nested_repository_is_stable_non_text_gitlink_evidence(repo_factory) -> None:
    repository = repo_factory()
    nested = repository / "vendor"
    nested.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=nested, check=True)
    subprocess.run(["git", "config", "user.name", "Nested Test"], cwd=nested, check=True)
    subprocess.run(
        ["git", "config", "user.email", "nested@example.invalid"], cwd=nested, check=True
    )
    (nested / "nested.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=nested, check=True)
    subprocess.run(["git", "commit", "-qm", "nested base"], cwd=nested, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=nested, text=True).strip()
    (nested / "nested.py").write_text("value = 2\n", encoding="utf-8")
    outer_status = subprocess.check_output(
        ["git", "status", "--porcelain=v2", "-z"], cwd=repository
    )

    first = changed(repository, ScopeSpec())
    second = changed(repository, ScopeSpec())

    assert canonical_json(first) == canonical_json(second)
    file = next(item for item in first["files"] if item["new_path"] == "vendor")
    assert file["status"] == "added"
    assert file["new_mode"] == "160000"
    assert {unit["kind"] for unit in file["units"]} == {"mode", "submodule"}
    pointer = next(unit for unit in file["units"] if unit["kind"] == "submodule")
    assert pointer["metadata"] == {"old_object": None, "new_object": head}
    assert file["hunks"] == file["spans"] == file["citations"] == []
    assert (
        subprocess.check_output(["git", "status", "--porcelain=v2", "-z"], cwd=repository)
        == outer_status
    )


def test_source_lookup_treats_pathspec_syntax_as_a_literal_path(repo_factory) -> None:
    repository = repo_factory()
    path = repository / ":(attr)odd.py"
    path.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "literal path"], cwd=repository, check=True)
    path.write_text("value = 2\n", encoding="utf-8")

    result = changed(repository, ScopeSpec())

    assert [file["new_path"] for file in result["files"]] == [":(attr)odd.py"]
    assert result["metrics"]["added_lines"] == result["metrics"]["deleted_lines"] == 1


def test_untracked_symlink_is_mapped_without_following_its_target(repo_factory) -> None:
    repository = repo_factory()
    (repository / "link").symlink_to("app.py")

    result = changed(repository, ScopeSpec())

    file = result["files"][0]
    assert file["new_path"] == "link"
    assert file["new_mode"] == "120000"
    assert file["hunks"][0]["lines"][0]["content"] == "app.py"


def test_commit_range_and_branch_resolve_immutable_shas(repo_factory) -> None:
    repository = repo_factory()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

    commit = changed(repository, ScopeSpec(commit=head))
    range_evidence = changed(repository, ScopeSpec(range=f"{base}..{head}"))
    branch = changed(repository, ScopeSpec(branch=base))

    assert commit["comparison"]["base_sha"] == base
    assert range_evidence["comparison"]["head_sha"] == head
    assert branch["comparison"]["base_sha"] == base
    assert commit["metrics"]["hunks"] == range_evidence["metrics"]["hunks"] == 1


def test_root_commit_compares_against_the_empty_tree(repo_factory) -> None:
    repository = repo_factory()
    result = changed(repository, ScopeSpec(commit="HEAD"))
    assert result["metrics"]["files"] == 1
    assert result["metrics"]["added_lines"] == 2
    assert result["metrics"]["deleted_lines"] == 0


def test_two_dot_three_dot_and_branch_use_exact_merge_base_semantics(repo_factory) -> None:
    repository = repo_factory()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    main_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repository, text=True
    ).strip()
    subprocess.run(["git", "checkout", "-qb", "other"], cwd=repository, check=True)
    (repository / "other.py").write_text("other = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "other"], cwd=repository, check=True)
    other = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    subprocess.run(["git", "checkout", "-q", main_branch], cwd=repository, check=True)
    (repository / "main.py").write_text("main = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "main"], cwd=repository, check=True)

    two_dot = changed(repository, ScopeSpec(range="other..HEAD"))
    three_dot = changed(repository, ScopeSpec(range="other...HEAD"))
    branch = changed(repository, ScopeSpec(branch="other"))

    assert two_dot["comparison"]["base_sha"] == other
    assert three_dot["comparison"]["base_sha"] == base
    assert three_dot["comparison"]["base_label"] == base
    assert branch["comparison"]["base_sha"] == base
    assert {file["new_path"] or file["old_path"] for file in two_dot["files"]} == {
        "main.py",
        "other.py",
    }
    assert {file["new_path"] for file in three_dot["files"]} == {"main.py"}


def test_merge_commit_requires_and_honors_explicit_parent(repo_factory) -> None:
    repository = repo_factory()
    main_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repository, text=True
    ).strip()
    subprocess.run(["git", "checkout", "-qb", "side"], cwd=repository, check=True)
    (repository / "side.py").write_text("side = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "side.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "side"], cwd=repository, check=True)
    subprocess.run(["git", "checkout", "-q", main_branch], cwd=repository, check=True)
    (repository / "main.py").write_text("main = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "main"], cwd=repository, check=True)
    subprocess.run(["git", "merge", "--no-ff", "-qm", "merge", "side"], cwd=repository, check=True)
    merge = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

    with pytest.raises(ScopeError, match="explicit --parent"):
        changed(repository, ScopeSpec(commit=merge))
    result = changed(repository, ScopeSpec(commit=merge, parent=1))
    assert {file["new_path"] for file in result["files"]} == {"side.py"}


def test_revision_and_unrelated_merge_base_fail_with_typed_diagnostics(repo_factory) -> None:
    repository = repo_factory()
    with pytest.raises(GitError, match="Unable to resolve revision") as missing:
        changed(repository, ScopeSpec(commit="does-not-exist"))
    assert missing.value.details and "recovery" in missing.value.details

    main_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repository, text=True
    ).strip()
    subprocess.run(["git", "checkout", "--orphan", "unrelated"], cwd=repository, check=True)
    subprocess.run(["git", "rm", "-qf", "app.py"], cwd=repository, check=True)
    (repository / "root.py").write_text("root = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "root.py"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated"], cwd=repository, check=True)
    subprocess.run(["git", "checkout", "-q", main_branch], cwd=repository, check=True)
    with pytest.raises(GitError, match="merge base") as unrelated:
        changed(repository, ScopeSpec(branch="unrelated"))
    assert unrelated.value.details and "recovery" in unrelated.value.details


def test_source_mapping_fails_closed_if_worktree_changes_after_patch_acquisition(
    repo_factory,
) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    comparison = resolve_comparison(resolve_repository(repository), ScopeSpec())
    patch = acquire_patch(comparison)
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")

    with pytest.raises(GitError, match="changed during evidence construction"):
        build_evidence(
            comparison,
            patch,
            options=AnalyzeOptions(repo=repository, graphora="off"),
        )


@pytest.mark.parametrize(
    ("scope", "stage_pointer"),
    [
        (ScopeSpec(), False),
        (ScopeSpec(unstaged=True), False),
        (ScopeSpec(staged=True), True),
    ],
)
def test_submodule_pointer_changes_are_non_text_evidence(
    repo_factory, scope: ScopeSpec, stage_pointer: bool
) -> None:
    repository = repo_factory()
    add_submodule(repository, repo_factory)
    if stage_pointer:
        subprocess.run(["git", "add", "vendor"], cwd=repository, check=True)

    result = analyze(
        AnalyzeOptions(
            repo=repository,
            scope=scope,
            graphora="auto",
            cache_dir=repository.parent / f"{repository.name}-{scope}-cache",
        ),
        provider=CapturingProvider(),
    ).to_dict()

    file = next(item for item in result["files"] if item["new_path"] == "vendor")
    assert [unit["kind"] for unit in file["units"]] == ["submodule"]
    pointer = file["units"][0]["metadata"]
    assert pointer == {"old_object": file["old_blob"], "new_object": file["new_blob"]}
    assert pointer["old_object"] != pointer["new_object"]
    assert file["hunks"] == file["spans"] == file["citations"] == []
    assert result["metrics"]["units"] == 1
    assert result["metrics"]["changed_lines"] == result["metrics"]["source_citations"] == 0
    assert result["metrics"]["raw_patch_bytes"] > 0


@pytest.mark.parametrize(
    ("scope", "mutate"),
    [
        (
            ScopeSpec(staged=True),
            lambda repository: (
                (repository / "app.py").write_text(
                    "def value():\n    return 3\n", encoding="utf-8"
                ),
                subprocess.run(["git", "add", "app.py"], cwd=repository, check=True),
            ),
        ),
        (
            ScopeSpec(unstaged=True),
            lambda repository: (repository / "app.py").write_text(
                "def value():\n    return 3\n", encoding="utf-8"
            ),
        ),
        (
            ScopeSpec(),
            lambda repository: (repository / "app.py").write_text(
                "def value():\n    return 3\n", encoding="utf-8"
            ),
        ),
    ],
)
def test_patch_acquisition_rejects_index_and_worktree_races(
    repo_factory, monkeypatch, scope: ScopeSpec, mutate
) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    if scope.staged:
        subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
    comparison = resolve_comparison(resolve_repository(repository), scope)
    original = repository_module._git
    raced = False

    def racing_git(root, args, **kwargs):
        nonlocal raced
        result = original(root, args, **kwargs)
        if args and args[0] == "diff" and not raced:
            raced = True
            mutate(repository)
        return result

    monkeypatch.setattr(repository_module, "_git", racing_git)
    with pytest.raises(GitError, match="changed during patch acquisition"):
        acquire_patch(comparison)


def test_working_patch_acquisition_rejects_untracked_race(repo_factory, monkeypatch) -> None:
    repository = repo_factory()
    untracked = repository / "new.py"
    untracked.write_text("value = 1\n", encoding="utf-8")
    comparison = resolve_comparison(resolve_repository(repository), ScopeSpec())
    original = repository_module._untracked_patches

    def racing_untracked(root, context_lines):
        patch = original(root, context_lines)
        untracked.write_text("value = 2\n", encoding="utf-8")
        return patch

    monkeypatch.setattr(repository_module, "_untracked_patches", racing_untracked)
    with pytest.raises(GitError, match="changed during patch acquisition"):
        acquire_patch(comparison)


@pytest.mark.parametrize("scope", [ScopeSpec(), ScopeSpec(staged=True)])
def test_snapshot_materialization_does_not_publish_raced_mutable_state(
    repo_factory, monkeypatch, scope: ScopeSpec
) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    if scope.staged:
        subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
        function_name = "_materialize_index"
    else:
        function_name = "_materialize_worktree"
    comparison = resolve_comparison(resolve_repository(repository), scope)
    cache = CacheStore(
        comparison.repository_id,
        cache_root=repository.parent / f"{repository.name}-race-cache",
    )
    original = getattr(source_module, function_name)

    def racing_materialize(*args):
        files = original(*args)
        (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")
        if scope.staged:
            subprocess.run(["git", "add", "app.py"], cwd=repository, check=True)
        return files

    monkeypatch.setattr(source_module, function_name, racing_materialize)
    with pytest.raises(GitError, match="changed during after snapshot materialization"):
        materialize_snapshot(comparison, cache, "after")
    sources = cache.root / "sources"
    assert not sources.exists() or not any(sources.rglob("manifest.json"))


def test_cached_mutable_snapshot_rejects_stale_comparison(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    comparison = resolve_comparison(resolve_repository(repository), ScopeSpec())
    cache = CacheStore(
        comparison.repository_id,
        cache_root=repository.parent / f"{repository.name}-stale-cache",
    )
    materialize_snapshot(comparison, cache, "after")
    (repository / "app.py").write_text("def value():\n    return 3\n", encoding="utf-8")

    with pytest.raises(GitError, match="changed during after snapshot materialization"):
        materialize_snapshot(comparison, cache, "after")


def test_real_git_inventory_covers_rename_copy_binary_and_mode(repo_factory) -> None:
    repository = repo_factory()
    (repository / "source.py").write_text("source = True\n", encoding="utf-8")
    (repository / "binary.dat").write_bytes(bytes(range(256)))
    (repository / "mode.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "inventory base"], cwd=repository, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    subprocess.run(["git", "mv", "app.py", "renamed.py"], cwd=repository, check=True)
    (repository / "copied.py").write_text("source = True\n", encoding="utf-8")
    (repository / "binary.dat").write_bytes(bytes(reversed(range(256))))
    (repository / "mode.sh").chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "all metadata"], cwd=repository, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()

    result = changed(repository, ScopeSpec(range=f"{base}..{head}"))
    kinds = {
        (file["new_path"] or file["old_path"], unit["kind"])
        for file in result["files"]
        for unit in file["units"]
    }
    assert ("renamed.py", "rename") in kinds
    assert ("copied.py", "copy") in kinds
    assert ("binary.dat", "binary") in kinds
    assert ("mode.sh", "mode") in kinds


def test_deleted_untracked_binary_executable_and_ignored_files_are_exact(repo_factory) -> None:
    repository = repo_factory()
    (repository / ".gitignore").write_text("ignored.bin\n", encoding="utf-8")
    (repository / "deleted.py").write_text("first = 1\nsecond = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "tracked cases"], cwd=repository, check=True)

    (repository / "deleted.py").unlink()
    (repository / "untracked.bin").write_bytes(b"\x00\xff\x01\xfe" * 128)
    executable = repository / "-executable.sh"
    executable.write_text("#!/bin/sh\nprintf 'ok\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    (repository / "ignored.bin").write_bytes(b"\x00ignored")

    result = changed(repository, ScopeSpec())
    files = {file["new_path"] or file["old_path"]: file for file in result["files"]}

    assert set(files) == {"deleted.py", "untracked.bin", "-executable.sh"}
    deleted = files["deleted.py"]
    assert deleted["status"] == "deleted"
    assert {line["side"] for hunk in deleted["hunks"] for line in hunk["lines"]} == {"before"}
    assert {(citation["side"], citation["path"]) for citation in deleted["citations"]} == {
        ("before", "deleted.py")
    }
    assert {unit["kind"] for unit in files["untracked.bin"]["units"]} == {"binary", "mode"}
    executable_units = {unit["kind"] for unit in files["-executable.sh"]["units"]}
    assert executable_units == {"text", "mode"}
    mode = next(unit for unit in files["-executable.sh"]["units"] if unit["kind"] == "mode")
    assert mode["metadata"] == {"old_mode": None, "new_mode": "100755"}


class CapturingProvider:
    name = "fake"
    version = "0.2.1"

    def __init__(self) -> None:
        self.sides: list[str] = []

    def enrich(self, snapshot, *, side="after", **kwargs) -> GraphResult:
        assert (snapshot / "app.py").is_file()
        self.sides.append(side)
        return GraphResult("available", self.name, self.version)


class PathScopeCapturingProvider:
    name = "fake"
    version = "0.2.1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], dict[str, tuple[int, ...]]]] = []

    def enrich(
        self,
        snapshot,
        *,
        side="after",
        changed_paths=(),
        changed_lines=None,
        **kwargs,
    ) -> GraphResult:
        assert (snapshot / "selected.py").is_file()
        assert (snapshot / "context.py").is_file()
        self.calls.append((side, changed_paths, changed_lines or {}))
        return GraphResult("available", self.name, self.version)


def test_path_scope_keeps_full_graphora_snapshots_with_selected_change_focus(repo_factory) -> None:
    repository = repo_factory()
    (repository / "selected.py").write_text("selected = 1\n", encoding="utf-8")
    (repository / "context.py").write_text("context = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "add graph files"], cwd=repository, check=True)
    (repository / "selected.py").write_text("selected = 2\n", encoding="utf-8")
    (repository / "context.py").write_text("context = 2\n", encoding="utf-8")
    provider = PathScopeCapturingProvider()

    analyze(
        AnalyzeOptions(
            repo=repository,
            scope=ScopeSpec(paths=("selected.py",)),
            graphora="auto",
            cache_dir=repository.parent / f"{repository.name}-path-graphora",
        ),
        provider=provider,
    )

    assert [(side, paths) for side, paths, _lines in provider.calls] == [
        ("before", ("selected.py",)),
        ("after", ("selected.py",)),
    ]
    assert all(set(lines) == {"selected.py"} for _side, _paths, lines in provider.calls)


def test_before_after_snapshots_and_warm_cache_are_deterministic(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    cache = repository.parent / f"{repository.name}-cache"
    provider = CapturingProvider()
    options = AnalyzeOptions(repo=repository, graphora="auto", cache_dir=cache)

    cold = analyze(options, provider=provider).to_dict()
    warm = analyze(options, provider=provider).to_dict()

    assert provider.sides == ["before", "after", "before", "after"]
    assert canonical_json(cold) == canonical_json(warm)
    assert len(canonical_json(cold).encode()) == cold["metrics"]["evidence_bytes"]
    assert cold["graph"]["status"] == "available"


@pytest.mark.skipif(_graphora_editable, reason="requires an installed Graphora artifact")
def test_real_pinned_graphora_reports_before_after_static_relationships(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text(
        "def helper():\n    return 1\n\ndef value():\n    return helper()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-qam", "functions"], cwd=repository, check=True)
    (repository / "app.py").write_text(
        "def helper():\n    return 2\n\ndef value():\n    return helper()\n",
        encoding="utf-8",
    )

    result = analyze(
        AnalyzeOptions(
            repo=repository,
            graphora="required",
            cache_dir=repository.parent / f"{repository.name}-graphora",
        )
    ).to_dict()

    assert result["graph"]["version"] == "0.2.1"
    definitions = [
        fact
        for fact in result["graph"]["facts"]
        if fact["kind"] == "definition" and fact["symbol"] == "helper"
    ]
    assert {fact["side"] for fact in definitions} == {"before", "after"}
    assert all(fact["provenance"] == "graphora:tree-sitter" for fact in definitions)
    assert {
        (fact["side"], fact["target"])
        for fact in result["graph"]["facts"]
        if fact["kind"] == "caller" and fact["symbol"] == "helper"
    } == {("before", "value"), ("after", "value")}


@pytest.mark.skipif(_graphora_editable, reason="requires an installed Graphora artifact")
def test_graphora_rename_uses_each_side_path(repo_factory) -> None:
    repository = repo_factory()
    (repository / "app.py").write_text(
        "def helper():\n    return 1\n\ndef value():\n    return helper()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "commit", "-qam", "functions"], cwd=repository, check=True)
    subprocess.run(["git", "mv", "app.py", "renamed.py"], cwd=repository, check=True)
    (repository / "renamed.py").write_text(
        "def helper():\n    return 2\n\ndef value():\n    return helper()\n",
        encoding="utf-8",
    )

    result = analyze(
        AnalyzeOptions(
            repo=repository,
            graphora="required",
            cache_dir=repository.parent / f"{repository.name}-rename-graphora",
        )
    ).to_dict()

    definitions = {
        (fact["side"], fact["path"])
        for fact in result["graph"]["facts"]
        if fact["kind"] == "definition" and fact["symbol"] == "helper"
    }
    assert definitions == {("before", "app.py"), ("after", "renamed.py")}
