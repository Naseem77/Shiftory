"""Deterministic Git patch parser and exact change ledger construction."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Literal

from shiftory.diff.identity import stable_id
from shiftory.errors import CoverageError, GitError
from shiftory.git.repository import normalize_path
from shiftory.models.core import ChangedLine, ChangeSpan, ChangeUnit, FileChange, TextHunk

_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_NO_NEWLINE = b"\\ No newline at end of file"


@dataclass(slots=True)
class _FileRecord:
    header: bytes
    body: list[bytes]


@dataclass(slots=True)
class _PendingLine:
    side: Literal["before", "after"]
    old_line: int | None
    new_line: int | None
    ordinal: int
    change_block: int
    content: bytes
    no_newline: bool = False


@dataclass(frozen=True, slots=True)
class _HunkAccounting:
    no_newline_sides: tuple[str, ...]
    undecodable_line_ids: tuple[str, ...]


def _decode_display(value: bytes) -> str:
    return value.decode("utf-8", "backslashreplace")


def _decode_path(value: bytes) -> str:
    return value.decode("utf-8", "surrogateescape")


def _without_lf(value: bytes) -> bytes:
    return value[:-1] if value.endswith(b"\n") else value


def _structural_line(value: bytes) -> bytes:
    return _without_lf(value).removesuffix(b"\r")


def _patch_lines(patch: bytes) -> list[bytes]:
    if not patch:
        return []
    pieces = patch.split(b"\n")
    lines = [piece + b"\n" for piece in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


def _unquote_path(value: bytes, *, strip_prefix: bool) -> str:
    value = value.removesuffix(b"\t")
    if value == b"/dev/null":
        return "/dev/null"
    if value.startswith(b'"'):
        if not value.endswith(b'"'):
            raise CoverageError("Malformed quoted path in Git patch")
        inner = value[1:-1]
        output = bytearray()
        index = 0
        escapes = {
            ord("a"): 7,
            ord("b"): 8,
            ord("t"): 9,
            ord("n"): 10,
            ord("v"): 11,
            ord("f"): 12,
            ord("r"): 13,
            ord("\\"): 92,
            ord('"'): 34,
        }
        while index < len(inner):
            current = inner[index]
            if current != 92:
                output.append(current)
                index += 1
                continue
            index += 1
            if index >= len(inner):
                raise CoverageError("Truncated escape in quoted Git path")
            current = inner[index]
            if 48 <= current <= 55:
                end = index + 1
                while end < len(inner) and end - index < 3 and 48 <= inner[end] <= 55:
                    end += 1
                output.append(int(inner[index:end], 8))
                index = end
                continue
            output.append(escapes.get(current, current))
            index += 1
        value = bytes(output)
    if strip_prefix and value.startswith((b"a/", b"b/")):
        value = value[2:]
    try:
        return normalize_path(_decode_path(value))
    except GitError as exc:
        raise CoverageError(str(exc), details=exc.details) from exc


def _header_paths(value: bytes) -> tuple[str, str] | None:
    separators = [match.start() for match in re.finditer(rb' (?=(?:"?b/))', value)]
    candidates: list[tuple[str, str]] = []
    for separator in separators:
        try:
            old_path = _unquote_path(value[:separator], strip_prefix=True)
            new_path = _unquote_path(value[separator + 1 :], strip_prefix=True)
        except CoverageError:
            continue
        candidates.append((old_path, new_path))
    if not candidates:
        return None
    identical = [candidate for candidate in candidates if candidate[0] == candidate[1]]
    if len(identical) == 1:
        return identical[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _records(patch: bytes) -> list[_FileRecord]:
    records: list[_FileRecord] = []
    current: _FileRecord | None = None
    preamble: list[bytes] = []
    for line in _patch_lines(patch):
        if line.startswith(b"diff --git "):
            if current is not None:
                records.append(current)
            current = _FileRecord(line, [])
        elif current is None:
            preamble.append(line)
        else:
            current.body.append(line)
    if current is not None:
        records.append(current)
    if any(line.strip() for line in preamble) or (patch and not records):
        raise CoverageError("Patch contains data outside a Git file record")
    return records


def _metadata(record: _FileRecord) -> dict[str, Any]:
    header = _structural_line(record.header).removeprefix(b"diff --git ")
    header_paths = _header_paths(header)
    result: dict[str, Any] = {
        "old_path": header_paths[0] if header_paths else None,
        "new_path": header_paths[1] if header_paths else None,
        "old_blob": None,
        "new_blob": None,
        "old_mode": None,
        "new_mode": None,
        "status": "modified",
        "binary": False,
        "similarity": None,
    }
    new_file = False
    deleted_file = False
    for raw_line in record.body:
        line = _structural_line(raw_line)
        if line.startswith(b"--- "):
            path = _unquote_path(line[4:], strip_prefix=True)
            result["old_path"] = None if path == "/dev/null" else path
        elif line.startswith(b"+++ "):
            path = _unquote_path(line[4:], strip_prefix=True)
            result["new_path"] = None if path == "/dev/null" else path
        elif line.startswith(b"index "):
            match = re.fullmatch(
                rb"index ([0-9a-f]+)\.\.([0-9a-f]+)(?: ([0-7]+))?",
                line,
            )
            if not match:
                raise CoverageError(f"Malformed index header: {_decode_display(line)}")
            result["old_blob"], result["new_blob"] = (
                match[1].decode("ascii"),
                match[2].decode("ascii"),
            )
            if match[3]:
                result["old_mode"] = result["new_mode"] = match[3].decode("ascii")
        elif line.startswith(b"old mode "):
            result["old_mode"] = line[9:].decode("ascii")
        elif line.startswith(b"new mode "):
            result["new_mode"] = line[9:].decode("ascii")
        elif line.startswith(b"new file mode "):
            new_file = True
            result["status"], result["new_mode"] = "added", line[14:].decode("ascii")
        elif line.startswith(b"deleted file mode "):
            deleted_file = True
            result["status"], result["old_mode"] = "deleted", line[18:].decode("ascii")
        elif line.startswith(b"rename from "):
            result["status"] = "renamed"
            result["old_path"] = _unquote_path(line[12:], strip_prefix=False)
        elif line.startswith(b"rename to "):
            result["new_path"] = _unquote_path(line[10:], strip_prefix=False)
        elif line.startswith(b"copy from "):
            result["status"] = "copied"
            result["old_path"] = _unquote_path(line[10:], strip_prefix=False)
        elif line.startswith(b"copy to "):
            result["new_path"] = _unquote_path(line[8:], strip_prefix=False)
        elif line.startswith(b"similarity index "):
            result["similarity"] = line[17:].decode("ascii")
        elif line.startswith((b"Binary files ", b"GIT binary patch")):
            result["binary"] = True
    if new_file:
        result["old_path"] = None
        if result["old_blob"] and not result["old_blob"].strip("0"):
            result["old_blob"] = None
    elif deleted_file:
        result["new_path"] = None
        if result["new_blob"] and not result["new_blob"].strip("0"):
            result["new_blob"] = None
    if result["old_path"] is None and not new_file:
        result["status"] = "added"
    elif result["new_path"] is None and not deleted_file:
        result["status"] = "deleted"
    if result["status"] == "added" and result["new_path"] is None:
        raise CoverageError("Added patch record has no after path")
    if result["status"] == "deleted" and result["old_path"] is None:
        raise CoverageError("Deleted patch record has no before path")
    return result


def _raw_hunks(body: list[bytes]) -> list[list[bytes]]:
    hunks: list[list[bytes]] = []
    current: list[bytes] | None = None
    for line in body:
        if line.startswith(b"@@ "):
            if current is not None:
                hunks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        hunks.append(current)
    return hunks


def _text_unit_id(metadata: dict[str, Any], raw_hunks: list[list[bytes]]) -> str:
    return stable_id(
        "unit",
        {
            **_identity_metadata(metadata),
            "kind": "text",
            "patch_hash": hashlib.sha256(
                b"".join(line for hunk in raw_hunks for line in hunk)
            ).hexdigest(),
        },
    )


def _parse_hunk(
    raw: list[bytes],
    metadata: dict[str, Any],
    unit_id: str,
) -> tuple[TextHunk, list[ChangeSpan], _HunkAccounting]:
    header = _structural_line(raw[0])
    match = _HUNK.fullmatch(header)
    if not match:
        raise CoverageError(f"Malformed Git hunk header: {_decode_display(header)}")
    old_start, old_count = int(match[1]), int(match[2] or b"1")
    new_start, new_count = int(match[3]), int(match[4] or b"1")
    heading = _decode_display(match[5]).strip()
    hunk_id = stable_id(
        "hunk",
        {
            "old_path": metadata["old_path"],
            "new_path": metadata["new_path"],
            "old_blob": metadata["old_blob"],
            "new_blob": metadata["new_blob"],
            "status": metadata["status"],
            "old_range": [old_start, old_count],
            "new_range": [new_start, new_count],
            "patch_hash": hashlib.sha256(b"".join(raw)).hexdigest(),
        },
    )
    old_line, new_line = old_start, new_start
    pending: list[_PendingLine] = []
    ordinal = 0
    change_block = -1
    in_change_block = False
    previous_marker: bytes | None = None
    no_newline_sides: set[str] = set()
    for raw_line in raw[1:]:
        line = _without_lf(raw_line)
        if line == _NO_NEWLINE or line.removesuffix(b"\r") == _NO_NEWLINE:
            if previous_marker is None:
                raise CoverageError(f"Orphan no-newline marker in hunk {hunk_id}")
            sides = (
                ("before", "after")
                if previous_marker == b" "
                else ("before",)
                if previous_marker == b"-"
                else ("after",)
            )
            no_newline_sides.update(sides)
            if pending and previous_marker in (b"-", b"+"):
                pending[-1].no_newline = True
            previous_marker = None
            continue
        if not line:
            raise CoverageError(f"Unexpected empty patch line in hunk {hunk_id}")
        marker, content = line[:1], line[1:]
        previous_marker = marker
        if marker == b" ":
            in_change_block = False
            old_line += 1
            new_line += 1
        elif marker == b"-":
            if not in_change_block:
                change_block += 1
                in_change_block = True
            pending.append(_PendingLine("before", old_line, None, ordinal, change_block, content))
            ordinal += 1
            old_line += 1
        elif marker == b"+":
            if not in_change_block:
                change_block += 1
                in_change_block = True
            pending.append(_PendingLine("after", None, new_line, ordinal, change_block, content))
            ordinal += 1
            new_line += 1
        else:
            raise CoverageError(f"Unexpected patch line in hunk {hunk_id}")
    if old_line - old_start != old_count or new_line - new_start != new_count:
        raise CoverageError(
            f"Hunk {hunk_id} line totals do not match its declared source ranges",
            details={
                "declared_old": old_count,
                "parsed_old": old_line - old_start,
                "declared_new": new_count,
                "parsed_new": new_line - new_start,
            },
        )
    groups = _pending_spans(pending)
    spans: list[ChangeSpan] = []
    lines: list[ChangedLine] = []
    undecodable: list[str] = []
    for span_ordinal, group in enumerate(groups):
        first, last = group[0], group[-1]
        start = first.old_line if first.side == "before" else first.new_line
        end = last.old_line if last.side == "before" else last.new_line
        assert start is not None and end is not None
        span_id = stable_id(
            "span",
            {
                "unit": unit_id,
                "hunk": hunk_id,
                "side": first.side,
                "start": start,
                "end": end,
                "ordinal": span_ordinal,
                "lines": [
                    {
                        "coordinate": item.old_line if item.side == "before" else item.new_line,
                        "ordinal": item.ordinal,
                        "content_hash": hashlib.sha256(item.content).hexdigest(),
                    }
                    for item in group
                ],
            },
        )
        span_lines: list[ChangedLine] = []
        for item in group:
            content_hash = hashlib.sha256(item.content).hexdigest()
            line_id = stable_id(
                "line",
                {
                    "unit": unit_id,
                    "hunk": hunk_id,
                    "span": span_id,
                    "side": item.side,
                    "coordinate": item.old_line if item.side == "before" else item.new_line,
                    "ordinal": item.ordinal,
                    "content_hash": content_hash,
                    "no_newline": item.no_newline,
                },
            )
            try:
                content_text = item.content.decode("utf-8")
            except UnicodeDecodeError:
                content_text = _decode_display(item.content)
                undecodable.append(line_id)
            span_lines.append(
                ChangedLine(
                    line_id,
                    item.side,
                    item.old_line,
                    item.new_line,
                    item.ordinal,
                    content_text,
                    content_hash,
                )
            )
        lines.extend(span_lines)
        spans.append(
            ChangeSpan(
                span_id,
                first.side,
                start,
                end,
                tuple(line.id for line in span_lines),
            )
        )
    spans = _link_replacements(spans, [group[0].change_block for group in groups])
    lines.sort(key=lambda item: item.ordinal)
    hunk = TextHunk(
        hunk_id,
        old_start,
        old_count,
        new_start,
        new_count,
        heading,
        tuple(span.id for span in spans),
        tuple(lines),
        sum(len(line) for line in raw),
    )
    return (
        hunk,
        spans,
        _HunkAccounting(tuple(sorted(no_newline_sides)), tuple(undecodable)),
    )


def _pending_spans(lines: list[_PendingLine]) -> list[list[_PendingLine]]:
    groups: list[list[_PendingLine]] = []
    for line in lines:
        coordinate = line.old_line if line.side == "before" else line.new_line
        if groups:
            previous = groups[-1][-1]
            previous_coordinate = (
                previous.old_line if previous.side == "before" else previous.new_line
            )
            if (
                previous.change_block == line.change_block
                and previous.side == line.side
                and coordinate == (previous_coordinate or 0) + 1
            ):
                groups[-1].append(line)
                continue
        groups.append([line])
    return groups


def _link_replacements(spans: list[ChangeSpan], change_blocks: list[int]) -> list[ChangeSpan]:
    result = spans[:]
    for index in range(len(spans) - 1):
        left, right = spans[index], spans[index + 1]
        if (
            change_blocks[index] == change_blocks[index + 1]
            and left.side == "before"
            and right.side == "after"
        ):
            result[index] = replace(left, replacement_span_id=right.id)
            result[index + 1] = replace(right, replacement_span_id=left.id)
    return result


def _identity_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in (
            "old_path",
            "new_path",
            "old_blob",
            "new_blob",
            "old_mode",
            "new_mode",
            "status",
        )
    }


def _non_text_units(metadata: dict[str, Any]) -> list[ChangeUnit]:
    identity = _identity_metadata(metadata)
    units: list[ChangeUnit] = []
    if metadata["binary"]:
        units.append(
            ChangeUnit(
                stable_id("unit", {**identity, "kind": "binary"}),
                "binary",
                metadata={"binary": True},
            )
        )
    if metadata["old_mode"] != metadata["new_mode"] and (
        metadata["old_mode"] is not None or metadata["new_mode"] is not None
    ):
        units.append(
            ChangeUnit(
                stable_id("unit", {**identity, "kind": "mode"}),
                "mode",
                metadata={
                    "old_mode": metadata["old_mode"],
                    "new_mode": metadata["new_mode"],
                },
            )
        )
    if metadata["status"] in ("renamed", "copied"):
        kind: Literal["rename", "copy"] = "rename" if metadata["status"] == "renamed" else "copy"
        units.append(
            ChangeUnit(
                stable_id("unit", {**identity, "kind": kind}),
                kind,
                metadata={
                    "old_path": metadata["old_path"],
                    "new_path": metadata["new_path"],
                    "similarity": metadata["similarity"],
                },
            )
        )
    if metadata["old_mode"] == "160000" or metadata["new_mode"] == "160000":
        units.append(
            ChangeUnit(
                stable_id("unit", {**identity, "kind": "submodule"}),
                "submodule",
                metadata={
                    "old_object": metadata["old_blob"],
                    "new_object": metadata["new_blob"],
                },
            )
        )
    return units


def parse_patch(patch: bytes) -> tuple[FileChange, ...]:
    records = _records(patch)
    files: list[FileChange] = []
    all_ids: set[str] = set()
    expected_hunks = 0
    expected_added = 0
    expected_deleted = 0
    for record in records:
        metadata = _metadata(record)
        raw_hunks = _raw_hunks(record.body)
        expected_hunks += len(raw_hunks)
        expected_added += sum(line.startswith(b"+") for hunk in raw_hunks for line in hunk[1:])
        expected_deleted += sum(line.startswith(b"-") for hunk in raw_hunks for line in hunk[1:])
        hunks: list[TextHunk] = []
        spans: list[ChangeSpan] = []
        accounting: list[_HunkAccounting] = []
        text_unit_id = _text_unit_id(metadata, raw_hunks) if raw_hunks else None
        if text_unit_id:
            for raw_hunk in raw_hunks:
                hunk, hunk_spans, hunk_accounting = _parse_hunk(raw_hunk, metadata, text_unit_id)
                hunks.append(hunk)
                spans.extend(hunk_spans)
                accounting.append(hunk_accounting)
        units: list[ChangeUnit] = []
        if text_unit_id:
            text_metadata: dict[str, Any] = {}
            no_newline = [
                {"hunk_id": hunk.id, "sides": list(item.no_newline_sides)}
                for hunk, item in zip(hunks, accounting, strict=True)
                if item.no_newline_sides
            ]
            undecodable = [line_id for item in accounting for line_id in item.undecodable_line_ids]
            if no_newline:
                text_metadata["no_newline"] = no_newline
            if undecodable:
                text_metadata["undecodable_line_ids"] = undecodable
            units.append(
                ChangeUnit(
                    text_unit_id,
                    "text",
                    tuple(hunk.id for hunk in hunks),
                    text_metadata,
                )
            )
        units.extend(_non_text_units(metadata))
        if not units:
            identity = _identity_metadata(metadata)
            units.append(
                ChangeUnit(
                    stable_id("unit", {**identity, "kind": "unsupported"}),
                    "unsupported",
                    metadata=identity,
                )
            )
        file_change = FileChange(
            metadata["old_path"],
            metadata["new_path"],
            metadata["status"],
            metadata["old_blob"],
            metadata["new_blob"],
            metadata["old_mode"],
            metadata["new_mode"],
            tuple(units),
            tuple(hunks),
            tuple(spans),
        )
        _validate_file(file_change, all_ids)
        files.append(file_change)
    parsed_hunks = sum(len(file.hunks) for file in files)
    parsed_added = sum(
        line.side == "after" for file in files for hunk in file.hunks for line in hunk.lines
    )
    parsed_deleted = sum(
        line.side == "before" for file in files for hunk in file.hunks for line in hunk.lines
    )
    if (parsed_hunks, parsed_added, parsed_deleted) != (
        expected_hunks,
        expected_added,
        expected_deleted,
    ):
        raise CoverageError(
            "Parsed patch totals do not match the patch inventory",
            details={
                "expected": [expected_hunks, expected_added, expected_deleted],
                "parsed": [parsed_hunks, parsed_added, parsed_deleted],
            },
        )
    return tuple(
        sorted(files, key=lambda item: (item.new_path or item.old_path or "", item.status))
    )


def _validate_file(file: FileChange, all_ids: set[str]) -> None:
    if not file.units:
        raise CoverageError("A file change has no change units")
    hunk_ids = [hunk.id for hunk in file.hunks]
    span_by_id = {span.id: span for span in file.spans}
    line_by_id = {line.id: line for hunk in file.hunks for line in hunk.lines}
    if len(hunk_ids) != len(set(hunk_ids)):
        raise CoverageError("Duplicate hunk identity within a file")
    if len(span_by_id) != len(file.spans):
        raise CoverageError("Duplicate span identity within a file")
    if len(line_by_id) != sum(len(hunk.lines) for hunk in file.hunks):
        raise CoverageError("Duplicate line identity within a file")
    span_references: Counter[str] = Counter()
    for hunk in file.hunks:
        if not hunk.lines:
            raise CoverageError(f"Textual hunk {hunk.id} contains no changed lines")
        span_references.update(hunk.span_ids)
        hunk_line_ids = {line.id for line in hunk.lines}
        referenced_lines = {
            line_id
            for span_id in hunk.span_ids
            for line_id in span_by_id.get(span_id, ChangeSpan("", "before", 0, 0, ())).line_ids
        }
        if referenced_lines != hunk_line_ids:
            raise CoverageError(f"Hunk {hunk.id} does not exactly own its changed lines")
    if set(span_references) != set(span_by_id) or any(
        count != 1 for count in span_references.values()
    ):
        raise CoverageError("Every span must belong to exactly one hunk")
    line_references = Counter(line_id for span in file.spans for line_id in span.line_ids)
    if set(line_references) != set(line_by_id) or any(
        count != 1 for count in line_references.values()
    ):
        raise CoverageError("Every changed line must belong to exactly one span")
    for span in file.spans:
        if not span.line_ids or span.start_line > span.end_line:
            raise CoverageError(f"Invalid span {span.id}")
        span_lines = [line_by_id[line_id] for line_id in span.line_ids]
        coordinates = [
            line.old_line if span.side == "before" else line.new_line for line in span_lines
        ]
        if any(line.side != span.side for line in span_lines) or coordinates != list(
            range(span.start_line, span.end_line + 1)
        ):
            raise CoverageError(f"Non-contiguous or mixed-side span {span.id}")
        if span.replacement_span_id:
            replacement = span_by_id.get(span.replacement_span_id)
            if (
                replacement is None
                or replacement.side == span.side
                or replacement.replacement_span_id != span.id
            ):
                raise CoverageError(f"Invalid replacement link for span {span.id}")
    hunk_references = Counter(
        hunk_id for unit in file.units if unit.kind == "text" for hunk_id in unit.hunk_ids
    )
    if set(hunk_references) != set(hunk_ids) or any(
        count != 1 for count in hunk_references.values()
    ):
        raise CoverageError("Every textual hunk must belong to exactly one text unit")
    if any(unit.kind != "text" and unit.hunk_ids for unit in file.units):
        raise CoverageError("Only text units may own textual hunks")
    ids = {
        *(unit.id for unit in file.units),
        *hunk_ids,
        *span_by_id,
        *line_by_id,
    }
    expected_id_count = len(file.units) + len(file.hunks) + len(file.spans) + len(line_by_id)
    if len(ids) != expected_id_count or ids & all_ids:
        raise CoverageError("Stable identity collision detected")
    all_ids.update(ids)
