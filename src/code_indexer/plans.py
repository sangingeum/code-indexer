"""Edit plans: content-addressed documents that name one structural edit.

A plan is an immutable JSON document named by the sha256 of its own canonical
bytes (ADR-0004 section 1), so show, validate and apply read one file and need
no mutable store. This module owns the format and the read-only half of its
lifecycle: canonical bytes and the plan id, the locator, the coordinate and
encoding rules, the ordering, the precondition checks, the syntax verdicts and
the preview diff. It never writes a source file -- that is the apply leg's job,
and ADR-0004 section 6 keeps the two apart on purpose.

What this module reports, it does not claim: every verdict here is structural
(file bytes, parse, region arithmetic). Nothing in the output says the result
compiles or type-checks, and the syntax verdict is the parser's own answer,
never the chunker's fallback (ADR-0004 section 7).
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import ts_chunker
from .chunker import declaration_hash
from .errors import (
    AmbiguousNodeError,
    AmbiguousOrderError,
    ArgumentError,
    CarriageReturnInPlanError,
    IndexerError,
    LocatorMismatchError,
    MixedNewlinesError,
    NewlineMismatchError,
    NotFoundError,
    NotUtf8Error,
    OverlappingEditsError,
    PathEscapesProjectError,
    PlanFormatError,
    PlanHashMismatchError,
    PlanStaleError,
    SemanticProviderUnavailableError,
    SubLineEditUnsupportedError,
)

SCHEMA_VERSION = 1
OPERATIONS = ("replace_body", "insert_symbol", "delete_declaration")
RESERVED_OPERATIONS = ("rename_symbol",)
DEFAULT_PLAN_DIR = ".code-indexer/plans"
NEWLINE_LF = "lf"
NEWLINE_CRLF = "crlf"
PROVIDER_NAME = "tree-sitter-structural"
PROVIDER_VERSION = str(ts_chunker.CHUNKER_VERSION)
# An edit's old text travels with the plan only while it fits; above the cap it
# is omitted rather than truncated (ADR-0004 section 4).
REGION_TEXT_CAP = 8192

_REQUIRED_MEMBERS = {
    "schema_version": int,
    "plan_id": str,
    "operation": str,
    "repository": dict,
    "created_at": str,
    "provider": dict,
    "target": dict,
    "files": list,
    "edits": list,
    "validation": dict,
}
_IDENTITY_MEMBERS = ("path", "language", "qualified_name", "symbol_type",
                     "decl_hash")
_BOM = b"\xef\xbb\xbf"
_TERMINATOR = re.compile(rb"\r\n|\n|\r")
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "*/", "--", ";;", "<!--")

# ---------------------------------------------------------------------------
# the document: canonical bytes and identity
# ---------------------------------------------------------------------------


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """The bytes the plan id covers: UTF-8, sorted keys, no whitespace.

    ``json.dumps`` escapes every control character, so a newline inside a
    replacement is the two bytes ``\\n`` and never a literal line break; there
    is no trailing newline because the encoder emits none.
    """
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def plan_id_of(document: dict[str, Any]) -> str:
    """``sha256:<hex>`` over the canonical bytes with ``plan_id`` absent."""
    payload = {key: value for key, value in document.items()
               if key != "plan_id"}
    return "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def seal(document: dict[str, Any]) -> dict[str, Any]:
    """Return the document with its ``plan_id`` written in (idempotent)."""
    sealed = {key: value for key, value in document.items()
              if key != "plan_id"}
    sealed["plan_id"] = plan_id_of(sealed)
    return sealed


def plan_text(document: dict[str, Any]) -> str:
    """The canonical document as text -- what ``--print`` emits."""
    return canonical_bytes(document).decode("utf-8")


def plan_file_name(plan_id: str) -> str:
    if not plan_id.startswith("sha256:"):
        raise PlanFormatError(f"plan id {plan_id!r} is not a sha256: reference")
    hex_digest = plan_id.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", hex_digest):
        raise PlanFormatError(f"plan id {plan_id!r} is not a sha256: reference")
    return hex_digest + ".json"


def write_plan(document: dict[str, Any], plan_dir: str) -> str:
    """Write one plan file and return its path. The one write of a prepare."""
    declared = document.get("plan_id")
    if declared != plan_id_of(document):
        raise PlanHashMismatchError(
            "refusing to write a plan whose plan_id does not hash to its own "
            "bytes")
    payload = canonical_bytes(document)
    os.makedirs(plan_dir, exist_ok=True)
    path = os.path.join(plan_dir, plan_file_name(declared))
    with open(path, "wb") as handle:
        handle.write(payload)
    return path


def resolve_ref(ref: str, plan_dir: str, root: str) -> tuple[str | None, str]:
    """A plan reference is a path or ``sha256:<hex>`` (ADR-0004 section 1).

    Returns ``(requested_hash, path)``; the hash is ``None`` for a path ref,
    whose identity is self-verified when the document is read.
    """
    if ref.startswith("sha256:"):
        name = plan_file_name(ref)
        path = os.path.join(plan_dir, name)
        if not os.path.isfile(path):
            raise NotFoundError(
                f"no plan {ref} in {plan_dir}: a plan is its file, so a "
                "missing file is a missing plan")
        return ref, path
    if os.path.isabs(ref):
        path = ref
    else:
        candidate = os.path.join(root, ref)
        path = candidate if os.path.isfile(candidate) else os.path.abspath(ref)
    if not os.path.isfile(path):
        raise NotFoundError(f"no plan file at {path}")
    return None, path


def load_plan(path: str) -> dict[str, Any]:
    """Read a plan file as data. Shape and identity checks are ``validate``'s."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise NotFoundError(f"plan file {path} is unreadable: {exc}") from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PlanFormatError(f"plan file {path} is not UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanFormatError(f"plan file {path} is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise PlanFormatError(f"plan file {path} holds {type(document).__name__}, "
                              "not an object")
    return document


def timestamp_now() -> str:
    """RFC 3339, UTC, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# files, lines and bytes (ADR-0004 section 3)
# ---------------------------------------------------------------------------


def checked_relative_path(root: str, rel_path: Any) -> str:
    """The absolute path of a project-relative plan path, or a refusal.

    Absolute, ``..``, and anything that resolves outside the root is
    ``PathEscapesProject`` -- mechanical, not a best effort.
    """
    if not isinstance(rel_path, str) or not rel_path:
        raise PathEscapesProjectError("a plan path is empty")
    if "\x00" in rel_path:
        raise PathEscapesProjectError(
            f"plan path {rel_path!r} holds a NUL byte")
    if (os.path.isabs(rel_path) or rel_path.startswith("/")
            or re.match(r"^[A-Za-z]:", rel_path)):
        raise PathEscapesProjectError(
            f"plan path {rel_path!r} is absolute; a plan path is "
            "project-relative with '/' separators")
    parts = rel_path.split("/")
    if any(part == ".." for part in parts):
        raise PathEscapesProjectError(
            f"plan path {rel_path!r} contains '..'")
    if any(part in ("", ".") for part in parts):
        raise PathEscapesProjectError(
            f"plan path {rel_path!r} is not a plain project-relative path")
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, *parts))
    if full != root_real and not full.startswith(root_real + os.sep):
        raise PathEscapesProjectError(
            f"plan path {rel_path!r} resolves outside the project root {root}")
    return full


@dataclass(frozen=True)
class TextFile:
    """One file as the format sees it: stored bytes, lines, byte offsets.

    ``body`` is the file after any byte-order mark; every offset in a plan is
    an offset into ``body``, so the BOM lies inside no range and is preserved
    verbatim (rule 2). ``line_starts`` and ``line_end_bytes`` are the byte
    geometry the cross-check recomputes from; a line's end byte excludes its
    terminator (rule 3).
    """

    rel_path: str
    raw: bytes
    has_bom: bool
    body: bytes
    text: str
    newline: str
    lines: tuple[str, ...]
    line_starts: tuple[int, ...]
    line_end_bytes: tuple[int, ...]

    def content_hash(self) -> str:
        """Whole-file hash over the stored bytes, BOM included."""
        return "sha256:" + hashlib.sha256(self.raw).hexdigest()

    def line_count(self) -> int:
        return len(self.lines)

    def line_of_byte(self, offset: int) -> int:
        """1-based line holding ``offset`` (EOF counts as the last line)."""
        if not self.lines:
            return 1
        if offset >= len(self.body):
            return len(self.lines)
        return bisect.bisect_right(self.line_starts, offset)

    def line_text(self, line: int) -> str:
        return self.lines[line - 1]


@dataclass(frozen=True)
class Declaration:
    """A declaration node: the locator's five identity facts plus ranges."""

    qualified_name: str
    symbol_type: str
    decl_hash: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    body_start_line: int | None = None
    body_end_line: int | None = None
    body_start_byte: int | None = None
    body_end_byte: int | None = None

    def range_block(self) -> dict[str, int]:
        return {"start_line": self.start_line, "end_line": self.end_line,
                "start_byte": self.start_byte, "end_byte": self.end_byte}

    def body_range_block(self) -> dict[str, int]:
        return {"start_line": self.body_start_line,
                "end_line": self.body_end_line,
                "start_byte": self.body_start_byte,
                "end_byte": self.body_end_byte}

    def candidate(self, rel_path: str) -> dict[str, Any]:
        return {"file": rel_path, "range": self.range_block(),
                "decl_hash": self.decl_hash,
                "qualified_name": self.qualified_name,
                "symbol_type": self.symbol_type}


def _scan_lines(body: bytes) -> tuple[list[str], list[int], list[int], list[bytes]]:
    """Split stored bytes into line texts plus their byte geometry.

    A terminator is not part of its line, and a final line without one is a
    legal last line whose end byte is the end of the bytes (rules 3 and 6).
    """
    starts: list[int] = []
    ends: list[int] = []
    terminators: list[bytes] = []
    start = 0
    for match in _TERMINATOR.finditer(body):
        starts.append(start)
        ends.append(match.start())
        terminators.append(match.group())
        start = match.end()
    if start < len(body):
        starts.append(start)
        ends.append(len(body))
        terminators.append(b"")
    lines = [body[lo:hi].decode("utf-8") for lo, hi in zip(starts, ends)]
    return lines, starts, ends, terminators


def _newline_convention(rel_path: str, terminators: list[bytes]) -> str:
    kinds = {term for term in terminators if term}
    if b"\r" in kinds or len(kinds) > 1:
        shown = ", ".join(sorted(repr(kind) for kind in kinds))
        raise MixedNewlinesError(
            f"{rel_path} mixes line terminators ({shown}); a file with mixed "
            "terminators is refused rather than guessed at")
    if kinds == {b"\r\n"}:
        return NEWLINE_CRLF
    # ``lf`` also covers a file with no terminator at all: there is no observed
    # convention to disagree with a declaration, and LF is the reading of a
    # one-line file that ends at its last byte.
    return NEWLINE_LF


def text_file_from_bytes(rel_path: str, raw: bytes) -> TextFile:
    """Build the format's view of one file from its stored bytes."""
    has_bom = raw.startswith(_BOM)
    body = raw[len(_BOM):] if has_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NotUtf8Error(
            f"{rel_path}: byte {exc.start} of the body is not UTF-8 "
            f"({exc.reason}); a plan never lossy-decodes") from exc
    lines, starts, ends, terminators = _scan_lines(body)
    newline = _newline_convention(rel_path, terminators)
    return TextFile(rel_path=rel_path, raw=raw, has_bom=has_bom, body=body,
                    text=text, newline=newline, lines=tuple(lines),
                    line_starts=tuple(starts), line_end_bytes=tuple(ends))


def read_text_file(root: str, rel_path: str) -> TextFile:
    full = checked_relative_path(root, rel_path)
    if not os.path.isfile(full):
        raise NotFoundError(f"plan path {rel_path!r} is not a file under {root}")
    try:
        with open(full, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise NotFoundError(f"{rel_path}: unreadable ({exc})") from exc
    return text_file_from_bytes(rel_path, raw)


# ---------------------------------------------------------------------------
# declarations: the locator's structural half
# ---------------------------------------------------------------------------


def declarations(rel_path: str, text: str) -> tuple[list[Declaration], str]:
    """Every declaration node in one file, plus the parser's own verdict.

    The walk mirrors the chunker's so the identity agrees with the index: the
    same node types, the same name lookup, the same signature slice and the
    same ``declaration_hash``. The difference is that this one keeps the byte
    range and the body child, which the chunker drops.
    """
    tree, verdict = ts_chunker.parse_tree(rel_path, text)
    if tree is None:
        return [], verdict
    data = text.encode("utf-8")
    out: list[Declaration] = []
    _walk(tree.root_node, data, None, out)
    return out, verdict


def _walk(node, data: bytes, container: str | None,
          out: list[Declaration]) -> None:
    for child in node.children:
        if child.type in ts_chunker._UNIT_TYPES:
            out.append(_declaration(child, data, container))
            name = out[-1].qualified_name
            if child.type in ts_chunker._CONTAINER_TYPES:
                _walk(child, data, name, out)
        elif child.type in ts_chunker._UNIT_TYPES_EXTRACT_ONLY:
            out.append(_declaration(child, data, container))
            _walk(child, data, out[-1].qualified_name, out)
        elif child.child_count:
            _walk(child, data, container, out)


def _declaration(node, data: bytes, container: str | None) -> Declaration:
    name = ts_chunker._symbol_from_node(node, data)
    symbol_type = ts_chunker._TYPE_BY_NODE.get(node.type) or "function"
    signature = ts_chunker._declaration_signature(node, data)
    node_text = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
    first_line = node_text.splitlines()[0] if node_text else ""
    qualified = ts_chunker._join_qualified(container, name) or (name or "")
    body = _body_node(node)
    return Declaration(
        qualified_name=qualified, symbol_type=symbol_type,
        decl_hash=declaration_hash(signature, first_line),
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        start_byte=node.start_byte, end_byte=node.end_byte,
        body_start_line=(body.start_point[0] + 1) if body is not None else None,
        body_end_line=(body.end_point[0] + 1) if body is not None else None,
        body_start_byte=body.start_byte if body is not None else None,
        body_end_byte=body.end_byte if body is not None else None)


def _body_node(node):
    """The body child the chunker's signature split already locates."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ts_chunker._UNIT_TYPES:
                return _body_node(child)
        return None
    body_name = ts_chunker._BODY_NODE_BY_TYPE.get(node.type)
    if body_name is None:
        return None
    return node.child_by_field_name(body_name)


def find_declaration(declarations_: list[Declaration], rel_path: str,
                     qualified_name: str, symbol_type: str,
                     decl_hash: str | None = None) -> Declaration:
    """The ONE node a locator names, or a refusal that lists the candidates.

    Resolution is structural: name and kind narrow the file's nodes, the
    declaration hash proves which declaration, and identical hashes leave the
    locator unable to say which one -- a refusal, never a pick (ADR-0002
    section 5).
    """
    same = [decl for decl in declarations_
            if decl.qualified_name == qualified_name
            and decl.symbol_type == symbol_type]
    if not same:
        raise LocatorMismatchError(
            f"{rel_path}: no node matching {qualified_name!r} "
            f"({symbol_type}); the declaration moved out, was renamed, or "
            "was deleted")
    if decl_hash is None:
        narrowed = same
    else:
        narrowed = [decl for decl in same if decl.decl_hash == decl_hash]
        if not narrowed:
            observed = ", ".join(sorted({decl.decl_hash for decl in same}))
            raise LocatorMismatchError(
                f"{rel_path}: {qualified_name!r} ({symbol_type}) is present "
                f"but its declaration hash differs: planned {decl_hash}, "
                f"observed {observed}")
    if len(narrowed) > 1:
        raise AmbiguousNodeError(
            f"{rel_path}:{qualified_name} ({symbol_type})",
            [decl.candidate(rel_path) for decl in narrowed])
    return narrowed[0]


# ---------------------------------------------------------------------------
# edits: coordinates, ordering, overlap (ADR-0004 sections 3, 5 and 8)
# ---------------------------------------------------------------------------


def normalize_replacement(text: str) -> str:
    """The line block a plan carries: LF only, no trailing terminator.

    Replacement text is written with ``\\n`` and never carries a carriage
    return (rule 5); the newline convention of the file is applied by the
    adapter. One trailing newline is accepted and treated as the end of the
    last line rather than a line of its own.
    """
    if not isinstance(text, str):
        raise ArgumentError("replacement text must be a string")
    if "\r" in text:
        raise CarriageReturnInPlanError(
            "replacement text carries a carriage return; a plan speaks LF "
            "only and the adapter translates to the file's convention")
    block = text[:-1] if text.endswith("\n") else text
    if not block.strip():
        raise ArgumentError(
            "the replacement block is empty; use plan-delete-declaration to "
            "remove a declaration")
    return block


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _line_span(tf: TextFile, start_byte: int, end_byte: int,
               what: str) -> tuple[int, int, int, int]:
    """The whole-line span of a node, or ``SubLineEditUnsupported``.

    A node whose first token does not begin a line, or whose last token does
    not end one, cannot be expressed as a line range -- and a plan has no
    columns -- so it is refused rather than widened.
    """
    if end_byte <= start_byte:
        raise SubLineEditUnsupportedError(
            f"{tf.rel_path}: {what} has no text to replace")
    start_line = tf.line_of_byte(start_byte)
    end_line = tf.line_of_byte(end_byte - 1)
    line_start = tf.line_starts[start_line - 1]
    if tf.body[line_start:start_byte].strip():
        raise SubLineEditUnsupportedError(
            f"{tf.rel_path}:{start_line}: {what} begins mid-line, and a plan "
            "edit is line-granular")
    line_end = tf.line_end_bytes[end_line - 1]
    if end_byte < line_end and tf.body[end_byte:line_end].strip():
        raise SubLineEditUnsupportedError(
            f"{tf.rel_path}:{end_line}: {what} ends mid-line, and a plan "
            "edit is line-granular")
    return start_line, end_line, line_start, line_end


def _range_edit(tf: TextFile, kind: str, start_line: int, end_line: int,
                start_byte: int, end_byte: int,
                new_text: str | None = None) -> dict[str, Any]:
    old = tf.body[start_byte:end_byte]
    edit: dict[str, Any] = {
        "kind": kind,
        "path": tf.rel_path,
        "start_line": start_line,
        "end_line": end_line,
        "start_byte": start_byte,
        "end_byte": end_byte,
        "expected_old_text_hash": _digest(old),
    }
    if len(old) <= REGION_TEXT_CAP:
        edit["expected_old_text"] = old.decode("utf-8")
    if new_text is not None:
        edit["new_text"] = new_text
    return edit


def replace_body_edit(tf: TextFile, decl: Declaration,
                      new_text: str) -> dict[str, Any]:
    if decl.body_start_byte is None or decl.body_end_byte is None:
        raise SubLineEditUnsupportedError(
            f"{tf.rel_path}: {decl.qualified_name!r} has no body node this "
            "tool can locate, so there is no body to replace")
    span = _line_span(tf, decl.body_start_byte, decl.body_end_byte, "the body")
    return _range_edit(tf, "replace_body", *span, new_text=new_text)


def delete_declaration_edit(tf: TextFile,
                            decl: Declaration) -> dict[str, Any]:
    span = _line_span(tf, decl.start_byte, decl.end_byte, "the declaration")
    return _range_edit(tf, "delete_declaration", *span)


def comment_block_top(tf: TextFile, first_line: int) -> int:
    """Top of the contiguous comment block immediately above a declaration."""
    top = first_line
    line = first_line - 1
    while line >= 1:
        stripped = tf.line_text(line).strip()
        if not stripped or not stripped.startswith(_COMMENT_PREFIXES):
            break
        top = line
        line -= 1
    return top


def insert_symbol_edit(tf: TextFile, decl: Declaration, position: str,
                       new_text: str, note: str | None = None) -> dict[str, Any]:
    """One insertion at a declaration boundary, or at the end of the file."""
    if position not in ("before", "after", "end"):
        raise ArgumentError(
            f"--position must be before, after or end, not {position!r}")
    edit: dict[str, Any] = {"kind": "insert_symbol", "path": tf.rel_path,
                            "new_text": new_text}
    if position == "end":
        edit["position"] = "end"
        if note:
            edit["note"] = note
        return edit
    if position == "before":
        boundary = decl.start_line
        block_top = comment_block_top(tf, decl.start_line)
        if block_top < decl.start_line:
            boundary = block_top
            note = (f"insertion moved above the comment block at lines "
                    f"{block_top}-{decl.start_line - 1}"
                    + (" (" + note + ")" if note else ""))
    else:
        boundary = decl.end_line + 1
    if boundary > tf.line_count():
        # After the last line is ``position: end``, never a line number
        # (rule 6).
        edit["position"] = "end"
        if note:
            edit["note"] = note
        return edit
    edit["position"] = position
    edit["start_line"] = boundary
    edit["start_byte"] = tf.line_starts[boundary - 1]
    edit["end_byte"] = tf.line_starts[boundary - 1]
    if note:
        edit["note"] = note
    return edit


def edit_boundary(edit: dict[str, Any], line_count: int) -> int:
    """The line boundary an edit starts at; a point for an insertion."""
    if edit.get("kind") == "insert_symbol" and edit.get("position") == "end":
        return line_count + 1
    return int(edit["start_line"])


def order_key(edit: dict[str, Any], line_count: int) -> tuple:
    """Application order: path ascending, then ``start_line`` DESCENDING.

    Within a file an earlier edit never shifts a later edit's coordinates, and
    at one start line a range edit runs before an insertion at that point, so
    the insertion lands above the replaced block.
    """
    return (edit["path"], -edit_boundary(edit, line_count),
            1 if edit.get("kind") == "insert_symbol" else 0)


def ordered_edits(edits: list[dict[str, Any]],
                  files: dict[str, TextFile]) -> list[dict[str, Any]]:
    return sorted(edits, key=lambda edit: order_key(
        edit, files[edit["path"]].line_count()))


def check_edit_order(edits: list[dict[str, Any]],
                     files: dict[str, TextFile]) -> str:
    """Refuse overlap and ambiguous ordering; describe the order otherwise."""
    by_path: dict[str, list[int]] = {}
    for index, edit in enumerate(edits):
        by_path.setdefault(edit["path"], []).append(index)
    for path, indices in by_path.items():
        tf = files[path]
        count = tf.line_count()
        ranges: list[tuple[int, int, int]] = []
        inserts: list[tuple[int, int]] = []
        for index in indices:
            edit = edits[index]
            if edit.get("kind") == "insert_symbol":
                inserts.append((edit_boundary(edit, count), index))
                continue
            start = int(edit["start_line"])
            end = int(edit["end_line"])
            if start < 1 or end < start or end > count:
                raise PlanFormatError(
                    f"{path}: lines {start}-{end} are not a range inside the "
                    f"file's {count} line(s)")
            ranges.append((start, end, index))
        seen: dict[int, int] = {}
        for boundary, index in inserts:
            if boundary in seen:
                raise AmbiguousOrderError(
                    f"{path}: two insertions at the boundary before line "
                    f"{boundary} -- no rule can honour both, so the order is "
                    "refused rather than invented")
            seen[boundary] = index
        for boundary, _index in inserts:
            for start, end, _other in ranges:
                if start < boundary <= end:
                    raise OverlappingEditsError(
                        f"{path}: the insertion before line {boundary} falls "
                        f"strictly inside lines {start}-{end}")
        for first in range(len(ranges)):
            for second in range(first + 1, len(ranges)):
                a1, b1, _ = ranges[first]
                a2, b2, _ = ranges[second]
                if a1 <= b2 and a2 <= b1:
                    raise OverlappingEditsError(
                        f"{path}: lines {a1}-{b1} and {a2}-{b2} overlap; an "
                        "overlap is refused, never ordered")
    ordered = ordered_edits(edits, files)
    order_text = ", ".join(
        f"{edit['path']}:{edit_boundary(edit, files[edit['path']].line_count())}"
        for edit in ordered)
    return (f"{len(edits)} edit(s) in application order "
            f"(path ascending, then start line descending): {order_text}")


# ---------------------------------------------------------------------------
# building a document
# ---------------------------------------------------------------------------


def file_record(tf: TextFile, language: str) -> dict[str, Any]:
    return {"path": tf.rel_path, "expected_hash": tf.content_hash(),
            "newline": tf.newline, "language": language}


def locator(tf: TextFile, decl: Declaration,
            language: str) -> dict[str, Any]:
    """ADR-0002's key plus the range as evidence -- and nothing else."""
    return {"path": tf.rel_path, "language": language,
            "qualified_name": decl.qualified_name,
            "symbol_type": decl.symbol_type, "decl_hash": decl.decl_hash,
            "range": decl.range_block()}


def build_document(*, operation: str, repository: dict[str, str],
                   files: list[dict[str, Any]], target: dict[str, Any],
                   edits: list[dict[str, Any]], created_at: str | None = None,
                   syntax_required: bool = True,
                   semantic_required: bool = False) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "repository": repository,
        "created_at": created_at or timestamp_now(),
        "provider": {"name": PROVIDER_NAME, "version": PROVIDER_VERSION},
        "target": target,
        "files": files,
        "edits": edits,
        "validation": {"syntax_required": syntax_required,
                       "semantic_required": semantic_required},
    }
    return seal(document)


# ---------------------------------------------------------------------------
# validation: the ladder, reported and never claimed
# ---------------------------------------------------------------------------

VALIDATE_CAVEAT = (
    "structural checks only -- file bytes, the parser's verdict and region "
    "arithmetic; nothing here claims the result compiles or type-checks")

# The basis every phase-1 identity verdict names, in the order it was used.
# ``validate`` and ``apply`` both report it from here, so a reader comparing
# the two surfaces is comparing one constant.
IDENTITY_VERIFIED = "structural"
IDENTITY_BASIS = ("file_hash", "decl_hash", "region_hash")


def identity_block() -> dict[str, Any]:
    """ADR-0004 section 2's identity verdict: structural, with its basis."""
    return {"verified": IDENTITY_VERIFIED, "basis": list(IDENTITY_BASIS)}


@dataclass
class _Checks:
    """The per-check verdicts ``plan-validate`` prints, in the order it ran."""

    entries: list[dict[str, str]] = field(default_factory=list)

    def record(self, name: str, value: Any, describe=None) -> None:
        detail = describe(value) if describe is not None else value
        self.entries.append({"check": name, "status": "ok",
                             "detail": "" if detail is None else str(detail)})

    def refuse(self, name: str, exc: IndexerError) -> None:
        self.entries.append({"check": name, "status": "failed",
                             "detail": f"{exc.error_type}: {exc}"})


def _check_schema(document: dict[str, Any]) -> str:
    missing = [name for name in _REQUIRED_MEMBERS if name not in document]
    if missing:
        raise PlanFormatError(
            "the document is missing required member(s): "
            + ", ".join(sorted(missing)))
    for name, expected in _REQUIRED_MEMBERS.items():
        if not isinstance(document[name], expected):
            raise PlanFormatError(
                f"member {name!r} is {type(document[name]).__name__}, "
                f"expected {expected.__name__}")
    if document["schema_version"] != SCHEMA_VERSION:
        raise PlanFormatError(
            f"schema_version {document['schema_version']} is not the version "
            f"this reader understands ({SCHEMA_VERSION})")
    operation = document["operation"]
    if operation in RESERVED_OPERATIONS:
        raise PlanFormatError(
            f"operation {operation!r} is reserved in the plan format and "
            "refused in phase 1: no structural relabelling of a symbol is "
            "offered, because references are textual and nothing would verify "
            "the rename")
    if operation not in OPERATIONS:
        raise PlanFormatError(
            f"unknown operation {operation!r}; this reader knows "
            + ", ".join(OPERATIONS))
    validation = document["validation"]
    for flag in ("syntax_required", "semantic_required"):
        if not isinstance(validation.get(flag), bool):
            raise PlanFormatError(f"validation.{flag} must be true or false")
    target = document["target"]
    absent = [name for name in _IDENTITY_MEMBERS if name not in target]
    if absent:
        raise PlanFormatError(
            "the locator is missing identity member(s): "
            + ", ".join(absent))
    target_range = target.get("range")
    if not isinstance(target_range, dict) or set(target_range) != {
            "start_line", "end_line", "start_byte", "end_byte"}:
        raise PlanFormatError(
            "target.range must carry start_line, end_line, start_byte and "
            "end_byte and nothing else")
    if not document["files"]:
        raise PlanFormatError("a plan must name at least one touched file")
    for record in document["files"]:
        for name in ("path", "expected_hash", "newline", "language"):
            if name not in record:
                raise PlanFormatError(f"files[] record is missing {name!r}")
        if record["newline"] not in (NEWLINE_LF, NEWLINE_CRLF):
            raise PlanFormatError(
                "files[] newline must be 'lf' or 'crlf', not "
                f"{record['newline']!r}")
    if not document["edits"]:
        raise PlanFormatError("a plan must carry at least one edit")
    for edit in document["edits"]:
        if edit.get("kind") not in OPERATIONS:
            raise PlanFormatError(
                f"edit kind {edit.get('kind')!r} is not one of "
                + ", ".join(OPERATIONS))
        if "path" not in edit:
            raise PlanFormatError("every edit names its file with 'path'")
    return (f"schema_version {SCHEMA_VERSION}, operation {operation}, "
            f"{len(document['files'])} file(s), {len(document['edits'])} edit(s)")


def _check_plan_hash(document: dict[str, Any], requested: str | None) -> str:
    declared = document.get("plan_id")
    plan_file_name(declared or "")
    actual = plan_id_of(document)
    if actual != declared:
        raise PlanHashMismatchError(
            f"the document hashes to {actual} but carries plan_id {declared}: "
            "a plan rewritten after sealing is refused")
    if requested is not None and requested != declared:
        raise PlanHashMismatchError(
            f"the reference names {requested} but the file carries {declared}: "
            "a path whose content hash differs from the requested hash is "
            "refused")
    return f"{declared} recomputed from the document's canonical bytes"


def verify_document(document: dict[str, Any],
                    requested: str | None = None) -> None:
    """The two checks every plan reader owes before it reads anything else.

    A plan's identity is its canonical bytes, so a reader that skips the hash
    believes a file name: the preview path would draw a tampered document that
    ``plan-validate`` and ``plan-apply`` both refuse. The schema runs first so
    a document that is not a plan at all is refused in the format's own
    vocabulary rather than raising somewhere further down.
    """
    _check_schema(document)
    _check_plan_hash(document, requested)


def _check_paths(document: dict[str, Any], root: str) -> str:
    seen = set()
    for record in document["files"]:
        checked_relative_path(root, record["path"])
        seen.add(record["path"])
    for edit in document["edits"]:
        checked_relative_path(root, edit["path"])
        if edit["path"] not in seen:
            raise PlanFormatError(
                f"edit path {edit['path']!r} is not one of the plan's "
                "files[] entries")
    target_path = document["target"]["path"]
    checked_relative_path(root, target_path)
    if target_path not in seen:
        raise PlanFormatError(
            f"target path {target_path!r} is not one of the plan's files[] "
            "entries")
    return (f"{len(seen)} path(s) project-relative and inside the project "
            "root")


def _load_files(document: dict[str, Any], root: str) -> dict[str, TextFile]:
    loaded: dict[str, TextFile] = {}
    for record in document["files"]:
        loaded[record["path"]] = read_text_file(root, record["path"])
    return loaded


def _check_file_preconditions(document: dict[str, Any],
                              files: dict[str, TextFile]) -> str:
    lines = []
    for record in document["files"]:
        tf = files[record["path"]]
        observed = tf.content_hash()
        if observed != record["expected_hash"]:
            raise PlanStaleError(
                f"{record['path']}: the file's hash is {observed} but the plan "
                f"was prepared against {record['expected_hash']}; the plan is "
                "stale and is refused whole")
        if tf.newline != record["newline"]:
            raise NewlineMismatchError(
                f"{record['path']}: the file uses {tf.newline} terminators but "
                f"the plan declares {record['newline']}")
        lines.append(f"{record['path']} {observed} ({tf.newline})")
    return "; ".join(lines)


def _check_replacement_text(document: dict[str, Any]) -> str:
    blocks = 0
    for edit in document["edits"]:
        for member in ("new_text", "expected_old_text"):
            value = edit.get(member)
            if isinstance(value, str) and "\r" in value:
                raise CarriageReturnInPlanError(
                    f"{edit['path']}: {member} carries a carriage return; a "
                    "plan encodes LF and never a literal CR")
        if "new_text" in edit:
            if not edit["new_text"].strip():
                raise PlanFormatError(
                    f"{edit['path']}: an edit's new_text is empty")
            blocks += 1
    return f"{blocks} replacement block(s), no carriage return"


def _check_declaration(document: dict[str, Any],
                       files: dict[str, TextFile]) -> Declaration:
    target = document["target"]
    tf = files[target["path"]]
    derived, verdict = declarations(target["path"], tf.text)
    if verdict == ts_chunker.PARSE_UNAVAILABLE:
        raise LocatorMismatchError(
            f"{target['path']}: the file does not parse with an AST "
            "extractor, so the locator cannot be re-derived (parse_unavailable "
            "is a failed claim, not a pass)")
    decl = find_declaration(derived, target["path"], target["qualified_name"],
                            target["symbol_type"], target["decl_hash"])
    recorded = target["range"]
    moved = (recorded["start_line"] != decl.start_line
             or recorded["end_line"] != decl.end_line)
    detail = (f"{target['path']}:{decl.qualified_name} "
              f"({decl.symbol_type}) re-derived at lines "
              f"{decl.start_line}-{decl.end_line}, decl_hash {decl.decl_hash}")
    if moved:
        detail += (" -- the node moved; it is still the same symbol, and the "
                   "recorded range was lines "
                   f"{recorded['start_line']}-{recorded['end_line']}")
    return detail


def _check_region_hashes(document: dict[str, Any],
                         files: dict[str, TextFile]) -> str:
    checked = 0
    for index, edit in enumerate(document["edits"]):
        expected = edit.get("expected_old_text_hash")
        if expected is None:
            continue
        tf = files[edit["path"]]
        start = int(edit["start_byte"])
        end = int(edit["end_byte"])
        if start < 0 or end < start or end > len(tf.body):
            raise PlanFormatError(
                f"edit {index} ({edit['path']}): bytes {start}-{end} are not "
                f"inside the file's {len(tf.body)} body byte(s)")
        region = tf.body[start:end]
        observed = _digest(region)
        if observed != expected:
            raise PlanStaleError(
                f"{edit['path']}: edit {index} expected the bytes at "
                f"{start}-{end} to hash to {expected}, observed {observed}: "
                "the region the plan saw is not the region on disk")
        if "expected_old_text" in edit:
            text = region.decode("utf-8")
            if text != edit["expected_old_text"]:
                raise PlanStaleError(
                    f"{edit['path']}: edit {index} carries expected_old_text "
                    "that the stored bytes do not reproduce")
        checked += 1
    return f"{checked} region(s) hash to the bytes the plan recorded"


def _check_coordinates(document: dict[str, Any],
                       files: dict[str, TextFile]) -> str:
    """Lines and bytes are both recorded, so arithmetic catches a bad offset."""
    for index, edit in enumerate(document["edits"]):
        tf = files[edit["path"]]
        count = tf.line_count()
        if edit.get("kind") == "insert_symbol":
            if edit.get("position") == "end":
                if "start_line" in edit or "start_byte" in edit:
                    raise PlanFormatError(
                        f"edit {index}: position 'end' carries no line "
                        "number (rule 6)")
                continue
            if "end_line" in edit:
                raise PlanFormatError(
                    f"edit {index}: an insertion has no end_line -- its byte "
                    "span is empty")
            start_line = int(edit["start_line"])
            if start_line < 1 or start_line > count + 1:
                raise PlanFormatError(
                    f"{edit['path']}: edit {index} inserts before line "
                    f"{start_line} of a {count}-line file")
            point = tf.line_starts[start_line - 1] if start_line <= count \
                else len(tf.body)
            if int(edit["start_byte"]) != point \
                    or int(edit["end_byte"]) != point:
                raise PlanFormatError(
                    f"{edit['path']}: edit {index} inserts at "
                    f"{edit['start_byte']}-{edit['end_byte']} but line "
                    f"{start_line} begins at {point}")
            continue
        start_line = int(edit["start_line"])
        end_line = int(edit["end_line"])
        if start_line < 1 or end_line < start_line or end_line > count:
            raise PlanFormatError(
                f"{edit['path']}: edit {index} spans lines {start_line}-"
                f"{end_line} of a {count}-line file")
        if int(edit["start_byte"]) != tf.line_starts[start_line - 1]:
            raise PlanFormatError(
                f"{edit['path']}: edit {index} records start_byte "
                f"{edit['start_byte']} but line {start_line} starts at "
                f"{tf.line_starts[start_line - 1]}")
        expected_end = tf.line_end_bytes[end_line - 1]
        if int(edit["end_byte"]) != expected_end:
            raise PlanFormatError(
                f"{edit['path']}: edit {index} records end_byte "
                f"{edit['end_byte']} but line {end_line} ends at "
                f"{expected_end}")
        if (edit.get("kind") == "insert_symbol"
                and edit["start_byte"] != edit["end_byte"]):
            raise PlanFormatError(
                f"{edit['path']}: edit {index} is an insertion but its byte "
                "span is not empty")
    target = document["target"]
    tf = files[target["path"]]
    recorded = target["range"]
    start_line = int(recorded["start_line"])
    end_line = int(recorded["end_line"])
    count = tf.line_count()
    if start_line < 1 or end_line < start_line or end_line > count:
        raise PlanFormatError(
            f"{target['path']}: the target range {start_line}-{end_line} is "
            f"outside the file's {count} line(s)")
    if tf.line_of_byte(int(recorded["start_byte"])) != start_line:
        raise PlanFormatError(
            f"{target['path']}: target start_byte {recorded['start_byte']} is "
            f"not on line {start_line}")
    if tf.line_of_byte(int(recorded["end_byte"]) - 1) != end_line:
        raise PlanFormatError(
            f"{target['path']}: target end_byte {recorded['end_byte']} is "
            f"not on line {end_line}")
    return "every recorded line and byte offset agrees with the stored bytes"


def _syntax_verdicts(document: dict[str, Any],
                     files: dict[str, TextFile]) -> list[dict[str, str]]:
    """The parser's own verdict per touched file -- never the chunker's."""
    out = []
    for record in document["files"]:
        path = record["path"]
        _chunks, verdict = ts_chunker.parse_file(path, files[path].text)
        out.append({"path": path, "verdict": verdict})
    return out


def _check_requests(document: dict[str, Any]) -> str:
    validation = document["validation"]
    if validation.get("semantic_required"):
        raise SemanticProviderUnavailableError(
            "the plan sets validation.semantic_required, and no semantic "
            "provider exists in phase 1: a plan may not buy a stronger "
            "promise than the tool can keep")
    return ("syntax_required "
            f"{str(validation.get('syntax_required')).lower()}, "
            "semantic_required false")


def run_preconditions(document: dict[str, Any], root: str,
                     requested: str | None = None,
                     checks: _Checks | None = None) -> dict[str, TextFile]:
    """Every read-only precondition, in one order, for validate and apply.

    ``plan-validate`` and ``plan-apply`` both call this and nothing else
    decides what "stale" means, so the two commands cannot disagree about it
    (ADR-0005 section 3): one reads the files, re-derives the node, reproduces
    the regions and checks the ordering, and the first refusal is raised as
    the taxonomy's own error with its own exit code. Nothing here mutates.

    ``checks`` records each verdict when a reporter is given; the caller gets
    the loaded files back because it needs them to order the edits.
    """
    def run(name: str, step, describe=None) -> Any:
        try:
            value = step()
        except IndexerError as exc:
            if checks is not None:
                checks.refuse(name, exc)
            raise
        if checks is not None:
            checks.record(name, value, describe)
        return value

    run("plan hash", lambda: _check_plan_hash(document, requested))
    run("schema", lambda: _check_schema(document))
    run("requests", lambda: _check_requests(document))
    run("paths", lambda: _check_paths(document, root))
    files = run("file bytes", lambda: _load_files(document, root),
                describe=lambda loaded: (
                    f"{len(loaded)} file(s) decoded as UTF-8 and read as "
                    "stored bytes"))
    run("file hash and newline declaration",
        lambda: _check_file_preconditions(document, files))
    run("replacement text", lambda: _check_replacement_text(document))
    run("node re-derivation", lambda: _check_declaration(document, files))
    run("line and byte cross-check", lambda: _check_coordinates(document, files))
    run("region hashes", lambda: _check_region_hashes(document, files))
    run("overlap and ordering",
        lambda: check_edit_order(document["edits"], files))
    return files


def validate_plan(document: dict[str, Any], root: str,
                  requested: str | None = None) -> dict[str, Any]:
    """Run the ladder and report each rung; never claim more than it ran.

    The report carries every check it performed and the first refusal it hit.
    ``passed`` means the structural checks passed; the caveat travels beside
    it so no caller reads it as a compile check.
    """
    checks = _Checks()
    report: dict[str, Any] = {
        "schema": 1,
        "plan_id": document.get("plan_id"),
        "operation": document.get("operation"),
        "ref": requested,
        "identity": identity_block(),
        "syntax": [],
        "caveat": VALIDATE_CAVEAT,
        "checks": [],
    }
    error: IndexerError | None = None
    try:
        files = run_preconditions(document, root, requested, checks)
        syntax = _syntax_verdicts(document, files)
        report["syntax"] = syntax
        checks.entries.append({
            "check": "syntax verdicts", "status": "reported",
            "detail": "; ".join(f"{item['path']} {item['verdict']}"
                                 for item in syntax)})
    except IndexerError as exc:
        error = exc
    report["checks"] = checks.entries
    report["passed"] = error is None
    if error is not None:
        report["ErrorType"] = error.error_type
        report["error"] = str(error)
        report["exit_code"] = error.exit_code
    return report


# ---------------------------------------------------------------------------
# preview: the plan plus the current bytes, applied in memory
# ---------------------------------------------------------------------------


def apply_in_memory(tf: TextFile, edits: list[dict[str, Any]]) -> list[str]:
    """The file's lines after the plan's edits, computed in memory only."""
    lines = list(tf.lines)
    count = tf.line_count()
    for edit in ordered_edits(edits, {tf.rel_path: tf}):
        new_lines = edit["new_text"].split("\n") if "new_text" in edit else []
        if edit["kind"] == "insert_symbol":
            boundary = edit_boundary(edit, count)
            lines[boundary - 1:boundary - 1] = new_lines
        elif edit["kind"] == "replace_body":
            lines[int(edit["start_line"]) - 1:int(edit["end_line"])] = new_lines
        elif edit["kind"] == "delete_declaration":
            del lines[int(edit["start_line"]) - 1:int(edit["end_line"])]
        else:  # pragma: no cover - the schema check refuses unknown kinds
            raise PlanFormatError(f"unknown edit kind {edit['kind']!r}")
    return lines


def preview(document: dict[str, Any], root: str,
            requested: str | None = None) -> list[dict[str, str]]:
    """Every affected file and its unified diff, computed and never written.

    The document's own identity and schema are verified first, then the file
    preconditions: a diff against bytes the plan did not see would be a
    picture of a different tree, so a stale file is refused rather than
    previewed, and a plan that fails its own hash is refused rather than
    drawn.
    """
    verify_document(document, requested)
    files = _load_files(document, root)
    _check_file_preconditions(document, files)
    out: list[dict[str, str]] = []
    for record in document["files"]:
        path = record["path"]
        tf = files[path]
        edits = [edit for edit in document["edits"] if edit["path"] == path]
        new_lines = apply_in_memory(tf, edits)
        before = [line + "\n" for line in tf.lines]
        after = [line + "\n" for line in new_lines]
        diff = "".join(difflib.unified_diff(
            before, after, fromfile="a/" + path, tofile="b/" + path))
        out.append({"path": path, "diff": diff})
    return out


def edit_summary(edit: dict[str, Any]) -> str:
    """One text-mode line per edit: what it does, where, and how much text."""
    block = edit.get("new_text")
    size = (f"{len(block.splitlines())} line(s)" if block is not None
            else "no text")
    if edit["kind"] == "insert_symbol":
        where = ("end of file" if edit.get("position") == "end"
                 else f"line {edit['start_line']}")
        return f"insert_symbol {edit['path']} at {where}, {size}"
    return (f"{edit['kind']} {edit['path']}:{edit['start_line']}-"
            f"{edit['end_line']}, {size}")
