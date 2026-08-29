from __future__ import annotations

import pytest

from shiftory.diff.parser import parse_patch
from shiftory.errors import CoverageError

PATCH = b"""diff --git a/app.py b/app.py
index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def value():
-    return 1
+    result = 2
+    return result
"""


def test_constructs_canonical_line_span_hunk_unit_hierarchy() -> None:
    first = parse_patch(PATCH)
    second = parse_patch(PATCH)
    assert first == second
    file = first[0]
    assert len(file.units) == 1
    assert len(file.hunks) == 1
    assert [span.side for span in file.spans] == ["before", "after"]
    assert file.spans[0].replacement_span_id == file.spans[1].id
    assert len(file.hunks[0].lines) == 3
    assert len({line.id for line in file.hunks[0].lines}) == 3


def test_hunk_content_resembling_file_headers_does_not_replace_paths() -> None:
    patch = b"""diff --git a/query.sql b/query.sql
--- a/query.sql
+++ b/query.sql
@@ -1 +1 @@
--- removed comment
+++ added comment
"""

    file = parse_patch(patch)[0]

    assert file.old_path == "query.sql"
    assert file.new_path == "query.sql"
    assert [line.content for line in file.hunks[0].lines] == [
        "-- removed comment",
        "++ added comment",
    ]


def test_replacements_are_linked_only_within_the_same_change_block() -> None:
    patch = b"""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
-old first
+new first
 keep one
-deleted but not replaced
 keep two
+added but not replacement
"""
    spans = parse_patch(patch)[0].spans

    assert [span.side for span in spans] == ["before", "after", "before", "after"]
    assert spans[0].replacement_span_id == spans[1].id
    assert spans[1].replacement_span_id == spans[0].id
    assert spans[2].replacement_span_id is None
    assert spans[3].replacement_span_id is None


def test_multiple_replacement_blocks_are_linked_independently() -> None:
    patch = b"""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,5 +1,6 @@
-old first
+new first
 keep
-old second a
-old second b
+new second a
+new second b
+new second c
 tail
"""
    first = parse_patch(patch)[0]
    second = parse_patch(patch)[0]

    assert first == second
    assert [(span.end_line - span.start_line + 1) for span in first.spans] == [1, 1, 2, 3]
    for before, after in (first.spans[:2], first.spans[2:]):
        assert before.replacement_span_id == after.id
        assert after.replacement_span_id == before.id


def test_addition_and_deletion_only_blocks_have_no_replacement_links() -> None:
    patch = b"""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,4 +1,4 @@
-deleted only
 keep one
+added only
 keep two
 tail
"""
    spans = parse_patch(patch)[0].spans

    assert [span.side for span in spans] == ["before", "after"]
    assert all(span.replacement_span_id is None for span in spans)


def test_no_newline_markers_do_not_split_an_adjacent_replacement_block() -> None:
    patch = b"""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""
    file = parse_patch(patch)[0]
    before, after = file.spans

    assert before.replacement_span_id == after.id
    assert after.replacement_span_id == before.id
    assert file.units[0].metadata["no_newline"] == [
        {"hunk_id": file.hunks[0].id, "sides": ["after", "before"]}
    ]


def test_parses_no_newline_marker_without_creating_a_line() -> None:
    patch = PATCH.rstrip(b"\n") + b"\n\\ No newline at end of file\n"
    file = parse_patch(patch)[0]
    assert len(file.hunks[0].lines) == 3
    assert file.units[0].metadata["no_newline"] == [
        {"hunk_id": file.hunks[0].id, "sides": ["after"]}
    ]


def test_inventory_non_text_metadata() -> None:
    patch = b"""diff --git a/old.bin b/new.bin
similarity index 100%
rename from old.bin
rename to new.bin
old mode 100644
new mode 100755
Binary files a/old.bin and b/new.bin differ
"""
    kinds = {unit.kind for unit in parse_patch(patch)[0].units}
    assert kinds == {"binary", "mode", "rename"}


def test_mode_only_path_with_spaces_is_not_split() -> None:
    patch = b"""diff --git a/space name.txt b/space name.txt
old mode 100644
new mode 100755
"""
    file = parse_patch(patch)[0]
    assert file.old_path == file.new_path == "space name.txt"


def test_rejects_inconsistent_hunk_ranges() -> None:
    with pytest.raises(CoverageError, match="line totals"):
        parse_patch(PATCH.replace(b"@@ -1,2 +1,3 @@", b"@@ -1,9 +1,3 @@"))


def test_preserves_cr_content_and_marks_undecodable_changed_lines() -> None:
    patch = b"""diff --git a/raw b/raw
index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222 100644
--- a/raw
+++ b/raw
@@ -1 +1 @@
-old\r
+new\xff
"""
    file = parse_patch(patch)[0]
    before, after = file.hunks[0].lines
    assert before.content == "old\r"
    assert after.content == r"new\xff"
    assert file.units[0].metadata["undecodable_line_ids"] == [after.id]


def test_parses_quoted_control_paths_and_preserves_backslashes() -> None:
    cases = {
        b'"a/tab\\tname" "b/tab\\tname"': "tab\tname",
        b'"a/line\\nname" "b/line\\nname"': "line\nname",
        b'"a/back\\\\name" "b/back\\\\name"': "back\\name",
        b"a/space b/name b/space b/name": "space b/name",
    }
    for header, expected in cases.items():
        patch = b"diff --git " + header + b"\nold mode 100644\nnew mode 100755\n"
        file = parse_patch(patch)[0]
        assert file.old_path == file.new_path == expected


def test_extended_rename_paths_do_not_drop_real_a_or_b_components() -> None:
    patch = b"""diff --git a/a/old.txt b/b/new.txt
similarity index 100%
rename from a/old.txt
rename to b/new.txt
"""
    file = parse_patch(patch)[0]
    assert file.old_path == "a/old.txt"
    assert file.new_path == "b/new.txt"
    assert file.units[0].metadata["similarity"] == "100%"


def test_inventories_copy_empty_file_and_submodule_metadata() -> None:
    patch = b"""diff --git a/source b/copy
similarity index 100%
copy from source
copy to copy
diff --git a/empty b/empty
new file mode 100644
index 0000000000000000000000000000000000000000..e69de29bb2d1d6434b8b29ae775ad8c2e48c5391
diff --git a/vendor b/vendor
index 1111111111111111111111111111111111111111..2222222222222222222222222222222222222222 160000
--- a/vendor
+++ b/vendor
@@ -1 +1 @@
-Subproject commit 1111111111111111111111111111111111111111
+Subproject commit 2222222222222222222222222222222222222222
"""
    files = parse_patch(patch)
    assert [file.new_path for file in files] == ["copy", "empty", "vendor"]
    assert [unit.kind for unit in files[0].units] == ["copy"]
    assert [unit.kind for unit in files[1].units] == ["mode"]
    assert {unit.kind for unit in files[2].units} == {"submodule", "text"}


def test_hierarchy_has_exact_single_parent_ownership() -> None:
    file = parse_patch(PATCH)[0]
    unit = next(unit for unit in file.units if unit.kind == "text")
    assert unit.hunk_ids == tuple(hunk.id for hunk in file.hunks)
    assert tuple(span.id for span in file.spans) == file.hunks[0].span_ids
    owned = [line_id for span in file.spans for line_id in span.line_ids]
    assert owned == [line.id for line in file.hunks[0].lines]


def test_rejects_patch_data_outside_file_records() -> None:
    with pytest.raises(CoverageError, match="outside"):
        parse_patch(b"not a git patch\n")
