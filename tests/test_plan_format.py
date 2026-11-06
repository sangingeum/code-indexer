"""The plan format: canonical bytes, the locator, its refusals, its rules.

Leg one of the plan round. Nothing here applies a plan: every test either
builds a document, reads one, or asks a read-only command what it reports.
The rules under test are ADR-0004 sections 1 to 6 and the validate half of 7 --
content addressing, the line-free locator plus its range as evidence, the
coordinate and encoding rules, ordering, and the refusal matrix, each refusal
with the exit code ADR-0003's taxonomy gives it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import plans  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    EXIT_BACKEND,
    EXIT_FAIL,
    EXIT_LOCK,
    EXIT_USAGE,
    AmbiguousNodeError,
    AmbiguousOrderError,
    CarriageReturnInPlanError,
    MixedNewlinesError,
    NotUtf8Error,
    OverlappingEditsError,
    PathEscapesProjectError,
    SubLineEditUnsupportedError,
)

TWO_FUNCTIONS = "def one():\n    return 1\n\ndef two():\n    return 2\n"


def sample(root, rel_path="mod.py", text=TWO_FUNCTIONS, raw=None):
    """A project root holding one file; returns (root, TextFile)."""
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(raw if raw is not None else text.encode("utf-8"))
    return plans.read_text_file(root, rel_path)


def document_for(tf, operation="replace_body", text="    return 41",
                 position="after", index=0):
    """A sealed plan over the given file, built the way the CLI builds one."""
    derived, _verdict = plans.declarations(tf.rel_path, tf.text)
    decl = derived[index]
    if operation == "replace_body":
        edits = [plans.replace_body_edit(tf, decl, text)]
    elif operation == "delete_declaration":
        edits = [plans.delete_declaration_edit(tf, decl)]
    else:
        edits = [plans.insert_symbol_edit(tf, decl, position, text)]
    return plans.build_document(
        operation=operation,
        repository={"project": "sample", "index_revision": "rev-1"},
        files=[plans.file_record(tf, "python")],
        target=plans.locator(tf, decl, "python"),
        edits=edits, created_at="2026-11-07T00:00:00Z")


def checked(document, root, requested=None):
    return plans.validate_plan(document, root, requested=requested)


# ---------------------------------------------------------------------------
# the document: canonical bytes, the plan id, no store
# ---------------------------------------------------------------------------

def test_canonical_bytes_are_utf8_sorted_and_whitespace_free():
    raw = plans.canonical_bytes({"b": 1, "a": {"y": 2, "x": 3}})
    assert raw == b'{"a":{"x":3,"y":2},"b":1}'
    assert not raw.endswith(b"\n")


def test_a_newline_inside_a_string_is_never_a_literal_line_break():
    raw = plans.canonical_bytes({"new_text": "one\ntwo"})
    assert b"\\n" in raw
    assert raw.count(b"\n") == 0


def test_the_plan_id_is_the_hash_of_the_bytes_without_the_id():
    document = {"schema_version": 1, "operation": "replace_body"}
    expected = "sha256:" + hashlib.sha256(
        plans.canonical_bytes(document)).hexdigest()
    assert plans.plan_id_of(document) == expected
    sealed = plans.seal(document)
    assert sealed["plan_id"] == expected
    # Resealing is idempotent: the id member is removed before hashing.
    assert plans.seal(sealed)["plan_id"] == expected


def test_the_file_is_named_by_the_hash_and_holds_the_canonical_bytes(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    plan_dir = str(tmp_path / ".code-indexer" / "plans")
    path = plans.write_plan(document, plan_dir)
    hex_digest = document["plan_id"].split(":", 1)[1]
    assert path == os.path.join(plan_dir, hex_digest + ".json")
    with open(path, "rb") as handle:
        assert handle.read() == plans.canonical_bytes(document)


def test_a_sealed_document_validates_against_its_own_tree(tmp_path):
    tf = sample(str(tmp_path))
    report = checked(document_for(tf), str(tmp_path))
    assert report["passed"] is True, report
    assert report["identity"] == {
        "verified": "structural",
        "basis": ["file_hash", "decl_hash", "region_hash"]}
    names = [entry["check"] for entry in report["checks"]]
    assert names == ["plan hash", "schema", "requests", "paths",
                     "file bytes", "file hash and newline declaration",
                     "replacement text", "node re-derivation",
                     "line and byte cross-check", "region hashes",
                     "overlap and ordering", "syntax verdicts"]


def test_the_report_carries_no_validation_claim(tmp_path):
    tf = sample(str(tmp_path))
    report = checked(document_for(tf), str(tmp_path))
    body = dict(report)
    body.pop("caveat")
    rendered = json.dumps(body).lower()
    for banned in ('"valid"', "compiles", "type-checks"):
        assert banned not in rendered
    assert "structural checks only" in report["caveat"]


def test_an_unknown_member_survives_a_round_trip(tmp_path):
    tf = sample(str(tmp_path))
    document = plans.seal({**document_for(tf), "notes": ["kept verbatim"]})
    path = plans.write_plan(document, str(tmp_path / "plans"))
    reread = plans.load_plan(path)
    assert reread["notes"] == ["kept verbatim"]
    # Reading never rewrites: the file still holds its canonical bytes.
    with open(path, "rb") as handle:
        assert handle.read() == plans.canonical_bytes(reread)


def test_a_hand_edited_plan_is_a_hash_mismatch(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["edits"][0]["new_text"] = "    return 99"
    report = checked(document, str(tmp_path))
    assert report["passed"] is False
    assert report["ErrorType"] == "PlanHashMismatch"
    assert report["exit_code"] == EXIT_USAGE


def test_a_reference_hash_that_differs_from_the_document_is_refused(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    other = "sha256:" + "1" * 64
    report = checked(document, str(tmp_path), requested=other)
    assert report["ErrorType"] == "PlanHashMismatch"


def test_a_path_reference_resolves_and_a_hash_reference_resolves(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    plan_dir = str(tmp_path / "plans")
    path = plans.write_plan(document, plan_dir)
    assert plans.resolve_ref(path, plan_dir, str(tmp_path)) == (None, path)
    requested, resolved = plans.resolve_ref(document["plan_id"], plan_dir,
                                           str(tmp_path))
    assert requested == document["plan_id"]
    assert resolved == path


def test_rename_symbol_is_reserved_and_refused(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["operation"] = "rename_symbol"
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "PlanFormatError"
    assert "reserved" in report["error"]


def test_an_unknown_schema_version_is_refused(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["schema_version"] = 2
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "PlanFormatError"


# ---------------------------------------------------------------------------
# the locator: five identity members plus the range as evidence
# ---------------------------------------------------------------------------

def test_the_locator_carries_the_five_members_plus_its_range(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    target = document["target"]
    assert set(target) == {"path", "language", "qualified_name",
                           "symbol_type", "decl_hash", "range"}
    assert set(target["range"]) == {"start_line", "end_line",
                                     "start_byte", "end_byte"}
    assert target["qualified_name"] == "one"
    assert target["symbol_type"] == "function"
    assert target["decl_hash"].startswith("sha256:")
    assert "symbol_id" not in target


def test_a_file_record_carries_path_hash_newline_and_language(tmp_path):
    tf = sample(str(tmp_path))
    record = document_for(tf)["files"][0]
    assert set(record) == {"path", "expected_hash", "newline", "language"}
    assert record["newline"] == "lf"
    assert record["language"] == "python"
    assert record["expected_hash"] == tf.content_hash()


def test_two_overloads_with_one_signature_are_refused_with_both_listed(
        tmp_path):
    text = ("def dup():\n    return 1\n\n\ndef dup():\n    return 1\n")
    tf = sample(str(tmp_path), "dup.py", text)
    derived, _ = plans.declarations("dup.py", tf.text)
    assert len(derived) == 2
    same = [decl for decl in derived
            if decl.qualified_name == target_name(derived)]
    with pytest.raises(AmbiguousNodeError) as refused:
        plans.find_declaration(derived, "dup.py", "dup", "function",
                               same[0].decl_hash)
    assert "matches 2 nodes" in str(refused.value)
    assert len(refused.value.candidates) == 2
    assert refused.value.exit_code == EXIT_USAGE
    lines = {candidate["range"]["start_line"]
             for candidate in refused.value.candidates}
    assert lines == {1, 5}


def target_name(derived):
    return derived[0].qualified_name


# ---------------------------------------------------------------------------
# the four invalidation cases
# ---------------------------------------------------------------------------

def test_the_file_changed_is_plan_stale_and_exits_five(tmp_path):
    root = str(tmp_path)
    tf = sample(root)
    document = document_for(tf)
    with open(os.path.join(root, "mod.py"), "ab") as handle:
        handle.write(b"\n# a later edit\n")
    report = checked(document, root)
    assert report["passed"] is False
    assert report["ErrorType"] == "PlanStale"
    assert report["exit_code"] == EXIT_LOCK
    assert "the plan is stale" in report["error"]


def test_a_changed_declaration_hash_is_a_locator_mismatch(tmp_path):
    root = str(tmp_path)
    tf = sample(root)
    document = document_for(tf)
    document["target"]["decl_hash"] = "sha256:" + "2" * 64
    report = checked(plans.seal(document), root)
    assert report["ErrorType"] == "LocatorMismatch"
    assert report["exit_code"] == EXIT_LOCK
    assert "declaration hash differs" in report["error"]


def test_a_node_that_is_gone_is_a_locator_mismatch(tmp_path):
    root = str(tmp_path)
    tf = sample(root)
    document = document_for(tf)
    document["target"]["qualified_name"] = "vanished"
    report = checked(plans.seal(document), root)
    assert report["ErrorType"] == "LocatorMismatch"
    assert "no node matching" in report["error"]


def test_two_nodes_with_one_identity_refuse_at_validate(tmp_path):
    root = str(tmp_path)
    text = "def dup():\n    return 1\n\n\ndef dup():\n    return 1\n"
    tf = sample(root, "dup.py", text)
    derived, _ = plans.declarations("dup.py", tf.text)
    document = plans.build_document(
        operation="replace_body",
        repository={"project": "sample", "index_revision": "rev-1"},
        files=[plans.file_record(tf, "python")],
        target=plans.locator(tf, derived[0], "python"),
        edits=[plans.replace_body_edit(tf, derived[0], "    return 2")],
        created_at="2026-11-07T00:00:00Z")
    report = checked(document, root)
    assert report["ErrorType"] == "AmbiguousNode"
    assert "matches 2 nodes" in report["error"]


# ---------------------------------------------------------------------------
# coordinate and encoding rules
# ---------------------------------------------------------------------------

def test_lines_are_one_based_inclusive_and_carry_no_terminator(tmp_path):
    tf = sample(str(tmp_path))
    assert tf.lines[0] == "def one():"
    assert tf.lines[1] == "    return 1"
    assert tf.line_starts[1] == 11
    assert tf.line_end_bytes[1] == 23  # the \n at 23 is not part of the line
    assert tf.line_of_byte(11) == 2


def test_a_byte_order_mark_is_preserved_and_lies_inside_no_range(tmp_path):
    raw = b"\xef\xbb\xbf" + TWO_FUNCTIONS.encode("utf-8")
    tf = sample(str(tmp_path), raw=raw)
    assert tf.has_bom is True
    assert tf.line_starts[0] == 0  # offset 0 is the first byte after the BOM
    document = document_for(tf)
    assert document["edits"][0]["start_byte"] == 11
    assert document["files"][0]["expected_hash"] == \
        "sha256:" + hashlib.sha256(raw).hexdigest()
    report = checked(document, str(tmp_path))
    assert report["passed"] is True, report


def test_crlf_is_declared_and_lf_replacement_text_is_kept_lf(tmp_path):
    text = "def one():\r\n    return 1\r\n"
    tf = sample(str(tmp_path), "win.py", raw=text.encode("utf-8"))
    assert tf.newline == "crlf"
    assert tf.lines == ("def one():", "    return 1")
    assert tf.line_end_bytes[1] == 24  # the \r\n at 24 is not part of the line
    document = document_for(tf)
    assert document["files"][0]["newline"] == "crlf"
    edit = document["edits"][0]
    assert edit["new_text"] == "    return 41"
    assert "\r" not in edit["new_text"]
    assert checked(document, str(tmp_path))["passed"] is True


def test_a_declared_newline_that_disagrees_with_the_file_is_refused(tmp_path):
    tf = sample(str(tmp_path), "win.py",
                raw=b"def one():\r\n    return 1\r\n")
    document = document_for(tf)
    document["files"][0]["newline"] = "lf"
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "NewlineMismatch"
    assert report["exit_code"] == EXIT_FAIL


def test_a_file_with_mixed_terminators_is_refused(tmp_path):
    raw = b"def one():\r\n    return 1\n"
    with pytest.raises(MixedNewlinesError):
        sample(str(tmp_path), "mixed.py", raw=raw)


def test_a_lone_carriage_return_is_a_mixed_convention(tmp_path):
    with pytest.raises(MixedNewlinesError):
        plans.text_file_from_bytes("old.py", b"one\rtwo\n")


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    with pytest.raises(NotUtf8Error):
        sample(str(tmp_path), "latin.py", raw=b"# caf\xe9\ndef one():\n    pass\n")


def test_replacement_text_that_encodes_a_carriage_return_is_refused():
    with pytest.raises(CarriageReturnInPlanError):
        plans.normalize_replacement("    return 1\r\n")


def test_a_plan_that_encodes_a_carriage_return_is_refused(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["edits"][0]["new_text"] = "    return 1\r"
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "CarriageReturnInPlan"


def test_a_final_line_without_a_terminator_is_a_legal_last_line(tmp_path):
    text = "def one():\n    return 1"
    tf = sample(str(tmp_path), "short.py", text)
    assert tf.line_end_bytes[-1] == len(tf.body)
    document = document_for(tf, operation="delete_declaration")
    edit = document["edits"][0]
    assert edit["start_line"] == 1 and edit["end_line"] == 2
    assert edit["end_byte"] == len(tf.body)
    assert checked(document, str(tmp_path))["passed"] is True


def test_insertion_after_the_last_line_is_position_end_with_no_line(tmp_path):
    tf = sample(str(tmp_path))
    derived, _ = plans.declarations("mod.py", tf.text)
    edit = plans.insert_symbol_edit(tf, derived[-1], "after", "def x():\n    pass")
    assert edit["position"] == "end"
    assert "start_line" not in edit and "start_byte" not in edit

def test_a_corrupted_byte_offset_is_caught_by_arithmetic(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["edits"][0]["start_byte"] = 12  # one byte into the line
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "PlanFormatError"
    assert "line 2 starts at 11" in report["error"]


def test_a_body_that_does_not_begin_at_a_line_start_is_refused(tmp_path):
    text = "def one(): return 1\n"
    tf = sample(str(tmp_path), "inline.py", text)
    derived, _ = plans.declarations("inline.py", tf.text)
    with pytest.raises(SubLineEditUnsupportedError):
        plans.replace_body_edit(tf, derived[0], "    return 2")


def test_a_path_that_escapes_the_project_is_refused(tmp_path):
    root = str(tmp_path / "proj")
    os.makedirs(root)
    for bad in ("/etc/passwd", "../outside.py", "src/../../outside.py"):
        with pytest.raises(PathEscapesProjectError) as refused:
            plans.checked_relative_path(root, bad)
        assert refused.value.exit_code == EXIT_USAGE
    with pytest.raises(PathEscapesProjectError):
        plans.checked_relative_path(root, "a//b.py")


def test_a_symlink_out_of_the_project_is_refused(tmp_path):
    root = str(tmp_path / "proj")
    os.makedirs(root)
    outside = tmp_path / "outside.py"
    outside.write_text("def one():\n    pass\n")
    os.symlink(str(outside), os.path.join(root, "link.py"))
    with pytest.raises(PathEscapesProjectError):
        plans.checked_relative_path(root, "link.py")


# ---------------------------------------------------------------------------
# ordering, adjacency and overlap
# ---------------------------------------------------------------------------

def test_the_order_is_descending_lines_within_a_file_and_ascending_paths():
    class Stub:
        def line_count(self):
            return 40

    files = {"a.py": Stub(), "b.py": Stub()}
    edits = [
        {"kind": "delete_declaration", "path": "b.py", "start_line": 3,
         "end_line": 4},
        {"kind": "delete_declaration", "path": "a.py", "start_line": 1,
         "end_line": 2},
        {"kind": "replace_body", "path": "a.py", "start_line": 20,
         "end_line": 22},
    ]
    ordered = plans.ordered_edits(edits, files)
    assert [(edit["path"], edit["start_line"]) for edit in ordered] == [
        ("a.py", 20), ("a.py", 1), ("b.py", 3)]


def test_adjacent_replacements_are_legal(tmp_path):
    tf = sample(str(tmp_path))
    edits = [
        {"kind": "replace_body", "path": "mod.py", "start_line": 1,
         "end_line": 1, "new_text": "x"},
        {"kind": "replace_body", "path": "mod.py", "start_line": 2,
         "end_line": 2, "new_text": "y"},
    ]
    detail = plans.check_edit_order(edits, {"mod.py": tf})
    assert "2 edit(s)" in detail


def test_an_overlap_is_refused_and_not_ordered(tmp_path):
    tf = sample(str(tmp_path))
    edits = [
        {"kind": "replace_body", "path": "mod.py", "start_line": 1,
         "end_line": 2, "new_text": "x"},
        {"kind": "replace_body", "path": "mod.py", "start_line": 2,
         "end_line": 3, "new_text": "y"},
    ]
    with pytest.raises(OverlappingEditsError) as refused:
        plans.check_edit_order(edits, {"mod.py": tf})
    assert refused.value.exit_code == EXIT_USAGE


def test_an_insertion_inside_another_edit_is_an_overlap(tmp_path):
    tf = sample(str(tmp_path))
    edits = [
        {"kind": "replace_body", "path": "mod.py", "start_line": 1,
         "end_line": 3, "new_text": "x"},
        {"kind": "insert_symbol", "path": "mod.py", "position": "before",
         "start_line": 2, "start_byte": 11, "end_byte": 11,
         "new_text": "def x():\n    pass"},
    ]
    with pytest.raises(OverlappingEditsError):
        plans.check_edit_order(edits, {"mod.py": tf})


def test_two_insertions_at_one_point_are_refused(tmp_path):
    tf = sample(str(tmp_path))
    first = {"kind": "insert_symbol", "path": "mod.py", "position": "before",
             "start_line": 4, "start_byte": 25, "end_byte": 25,
             "new_text": "def x():\n    pass"}
    second = {"kind": "insert_symbol", "path": "mod.py",
              "position": "after", "start_line": 3, "start_byte": 24,
              "end_byte": 24, "new_text": "def y():\n    pass"}
    # 'after line 3' and 'before line 4' are the same point.
    second["start_line"] = 4
    with pytest.raises(AmbiguousOrderError) as refused:
        plans.check_edit_order([first, second], {"mod.py": tf})
    assert refused.value.exit_code == EXIT_USAGE


def test_an_insertion_before_an_edit_is_not_an_overlap(tmp_path):
    tf = sample(str(tmp_path))
    edits = [
        {"kind": "replace_body", "path": "mod.py", "start_line": 2,
         "end_line": 2, "new_text": "x"},
        {"kind": "insert_symbol", "path": "mod.py", "position": "before",
         "start_line": 2, "start_byte": 11, "end_byte": 11,
         "new_text": "def x():\n    pass"},
    ]
    plans.check_edit_order(edits, {"mod.py": tf})


# ---------------------------------------------------------------------------
# the semantic request is refused, not promised
# ---------------------------------------------------------------------------

def test_a_semantic_request_is_refused_with_exit_three(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["validation"]["semantic_required"] = True
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "SemanticProviderUnavailable"
    assert report["exit_code"] == EXIT_BACKEND
    assert "no semantic provider" in report["error"]


def test_the_syntax_verdict_is_the_parsers_and_is_reported(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    report = checked(document, str(tmp_path))
    assert report["syntax"] == [{"path": "mod.py", "verdict": "parsed"}]
    statuses = {entry["check"]: entry["status"] for entry in report["checks"]}
    assert statuses["syntax verdicts"] == "reported"
