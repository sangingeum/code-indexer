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
from typer.testing import CliRunner  # noqa: E402

from code_indexer import plans  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
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
    PlanFormatError,
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


def test_rename_symbol_is_reserved_and_refused_as_unsupported(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["operation"] = "rename_symbol"
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "UnsupportedOperation"
    assert report["exit_code"] == EXIT_USAGE
    assert "reserved" in report["error"]
    # No text edit: the refusal happens before any bytes are considered, and
    # validation writes nothing at all.
    with open(os.path.join(str(tmp_path), "mod.py"), "rb") as handle:
        assert handle.read() == TWO_FUNCTIONS.encode("utf-8")


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

def test_an_insertion_plan_validates(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf, operation="insert_symbol",
                            text="def three():\n    return 3",
                            position="before", index=1)
    report = checked(document, str(tmp_path))
    assert report["passed"] is True, report
    assert document["edits"][0]["position"] == "before"
    assert "end_line" not in document["edits"][0]


def test_a_corrupted_insertion_point_is_caught_by_arithmetic(tmp_path):
    tf = sample(str(tmp_path))
    document = document_for(tf, operation="insert_symbol",
                            text="def three():\n    return 3",
                            position="before", index=1)
    document["edits"][0]["start_byte"] += 1
    report = checked(plans.seal(document), str(tmp_path))
    assert report["ErrorType"] == "PlanFormatError"
    assert "inserts at" in report["error"]


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


# ---------------------------------------------------------------------------
# the prepare commands: one plan file, or nothing under --print
# ---------------------------------------------------------------------------

runner = CliRunner()


class PlanRig:
    def __init__(self, root, project, entry, core):
        self.root = root
        self.project = project
        self.entry = entry
        self.core = core
        self.plan_dir = os.path.join(str(project), plans.DEFAULT_PLAN_DIR)


@pytest.fixture()
def plan_rig(tmp_path, monkeypatch):
    """A registered project with sources but no index: prepare never needs one."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "mod.py").write_text(TWO_FUNCTIONS)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="plansample")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield PlanRig(str(project), project, entry, core)
    reg.close()
    cli._core = None
    cli._core_skip = False


def test_plan_replace_body_writes_one_plan_named_by_its_hash(plan_rig):
    result = runner.invoke(app, [
        "plan-replace-body", "one", "--project", "plansample",
        "--file", "mod.py", "--text", "    return 41"])
    assert result.exit_code == 0, result.output
    names = os.listdir(plan_rig.plan_dir)
    assert len(names) == 1
    path = os.path.join(plan_rig.plan_dir, names[0])
    document = plans.load_plan(path)
    assert names[0] == document["plan_id"].split(":", 1)[1] + ".json"
    assert plans.plan_id_of(document) == document["plan_id"]
    assert document["operation"] == "replace_body"
    assert document["target"]["qualified_name"] == "one"
    assert document["edits"][0]["new_text"] == "    return 41"
    assert document["validation"] == {"syntax_required": True,
                                      "semantic_required": False}


def test_print_emits_the_canonical_document_and_writes_nothing(plan_rig):
    result = runner.invoke(app, [
        "plan-replace-body", "one", "--project", "plansample",
        "--file", "mod.py", "--text", "    return 41", "--print"])
    assert result.exit_code == 0, result.output
    assert not os.path.exists(plan_rig.plan_dir)
    assert result.stdout == plans.plan_text(json.loads(result.stdout))
    document = json.loads(result.stdout)
    assert document["plan_id"] == plans.plan_id_of(document)


def test_the_printed_plan_id_is_the_hash_of_the_printed_bytes(plan_rig):
    result = runner.invoke(app, [
        "plan-delete-declaration", "two", "--project", "plansample",
        "--file", "mod.py", "--print"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    without_id = {key: value for key, value in document.items()
                  if key != "plan_id"}
    expected = "sha256:" + hashlib.sha256(
        plans.canonical_bytes(without_id)).hexdigest()
    assert document["plan_id"] == expected


def test_insert_before_moves_above_a_comment_block_and_says_so(tmp_path,
                                                              plan_rig):
    path = plan_rig.project / "commented.py"
    path.write_text("# note a\n# note b\ndef two():\n    return 2\n")
    result = runner.invoke(app, [
        "plan-insert-symbol", "two", "--project", "plansample",
        "--file", "commented.py", "--position", "before",
        "--text", "def one():\n    return 1"])
    assert result.exit_code == 0, result.output
    document = plans.load_plan(
        os.path.join(plan_rig.plan_dir, os.listdir(plan_rig.plan_dir)[0]))
    edit = document["edits"][0]
    assert edit["position"] == "before"
    assert edit["start_line"] == 1
    assert "comment block at lines 1-2" in edit["note"]


def test_insert_after_the_last_declaration_is_position_end(plan_rig):
    result = runner.invoke(app, [
        "plan-insert-symbol", "two", "--project", "plansample",
        "--file", "mod.py", "--position", "after",
        "--text", "def three():\n    return 3"])
    assert result.exit_code == 0, result.output
    document = plans.load_plan(
        os.path.join(plan_rig.plan_dir, os.listdir(plan_rig.plan_dir)[0]))
    assert document["edits"][0]["position"] == "end"


def test_an_ambiguous_name_with_one_file_is_refused_with_both(plan_rig):
    (plan_rig.project / "dup.py").write_text(
        "def dup():\n    return 1\n\n\ndef dup():\n    return 2\n")
    result = runner.invoke(app, [
        "plan-replace-body", "dup", "--project", "plansample",
        "--file", "dup.py", "--text", "    return 3", "--json"])
    assert result.exit_code == EXIT_USAGE
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "ArgumentError"
    assert len(payload["candidates"]) == 2
    assert not os.path.exists(plan_rig.plan_dir)


def test_prepare_needs_exactly_one_replacement_source(plan_rig):
    result = runner.invoke(app, [
        "plan-replace-body", "one", "--project", "plansample",
        "--file", "mod.py"])
    assert result.exit_code == EXIT_USAGE
    assert "--text or --body-file" in result.stderr


def test_a_body_file_is_translated_at_the_input_boundary(tmp_path, plan_rig):
    body = tmp_path / "body.txt"
    body.write_bytes(b"    return 41\r\n")
    result = runner.invoke(app, [
        "plan-replace-body", "one", "--project", "plansample",
        "--file", "mod.py", "--body-file", str(body)])
    assert result.exit_code == 0, result.output
    document = plans.load_plan(
        os.path.join(plan_rig.plan_dir, os.listdir(plan_rig.plan_dir)[0]))
    assert document["edits"][0]["new_text"] == "    return 41"


# ---------------------------------------------------------------------------
# plan-validate: it reports, it does not claim, and it writes nothing
# ---------------------------------------------------------------------------

def _prepare(plan_rig, symbol="one", text="    return 41"):
    result = runner.invoke(app, [
        "plan-replace-body", symbol, "--project", "plansample",
        "--file", "mod.py", "--text", text])
    assert result.exit_code == 0, result.output
    return os.path.join(plan_rig.plan_dir, os.listdir(plan_rig.plan_dir)[0])


def tree_fingerprint(*paths):
    """Every file's hash under the given paths: the write-free witness."""
    seen = {}
    for path in paths:
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                seen[path] = hashlib.sha256(handle.read()).hexdigest()
            continue
        for root, _dirs, names in os.walk(path):
            for name in names:
                full = os.path.join(root, name)
                with open(full, "rb") as handle:
                    seen[full] = hashlib.sha256(handle.read()).hexdigest()
    return seen


def test_validate_reports_every_check_and_writes_nothing(plan_rig):
    path = _prepare(plan_rig)
    before = tree_fingerprint(plan_rig.plan_dir, plan_rig.project / "mod.py")
    result = runner.invoke(app, ["plan-validate", path,
                                 "--project", "plansample"])
    assert result.exit_code == 0, result.output
    after = tree_fingerprint(plan_rig.plan_dir, plan_rig.project / "mod.py")
    assert before == after
    assert "identity structural (file_hash, decl_hash, region_hash)" \
        in result.stdout
    for check in ("plan hash", "schema", "paths", "file hash and newline",
                 "node re-derivation", "region hashes",
                 "line and byte cross-check", "overlap and ordering"):
        assert check in result.stdout
    assert "structural checks only" in result.stdout
    lowered = "\n".join(line for line in result.stdout.splitlines()
                         if "structural checks only" not in line).lower()
    assert '"valid"' not in lowered and "type-checks" not in lowered


def test_validate_accepts_a_sha256_reference(plan_rig):
    path = _prepare(plan_rig)
    document = plans.load_plan(path)
    result = runner.invoke(app, ["plan-validate", document["plan_id"],
                                 "--project", "plansample", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["ref"] == document["plan_id"]


def test_a_stale_file_refuses_validation_with_exit_five(plan_rig):
    path = _prepare(plan_rig)
    (plan_rig.project / "mod.py").write_text(
        TWO_FUNCTIONS.replace("return 1", "return 9"))
    result = runner.invoke(app, ["plan-validate", path,
                                 "--project", "plansample", "--json"])
    assert result.exit_code == EXIT_LOCK
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PlanStale"
    assert payload["passed"] is False
    assert "mod.py" in payload["error"]
    assert "sha256:" in payload["error"]


def test_validate_names_the_refusal_on_stderr_without_json(plan_rig):
    path = _prepare(plan_rig)
    (plan_rig.project / "mod.py").write_text(TWO_FUNCTIONS + "# changed\n")
    result = runner.invoke(app, ["plan-validate", path,
                                 "--project", "plansample"])
    assert result.exit_code == EXIT_LOCK
    assert "PlanStale:" in result.stderr
    assert "structural checks only" in result.stdout


def test_a_reference_whose_hash_disagrees_with_the_file_is_refused(plan_rig):
    path = _prepare(plan_rig)
    document = plans.load_plan(path)
    other = "sha256:" + "3" * 64
    renamed = os.path.join(plan_rig.plan_dir, "3" * 64 + ".json")
    os.rename(path, renamed)
    assert document["plan_id"] != other
    result = runner.invoke(app, ["plan-validate", other,
                                 "--project", "plansample"])
    assert result.exit_code == EXIT_USAGE
    assert "PlanHashMismatch" in result.stderr


def test_validate_refuses_a_path_that_is_not_a_plan(plan_rig):
    result = runner.invoke(app, ["plan-validate", "sha256:" + "4" * 64,
                                 "--project", "plansample"])
    assert result.exit_code == EXIT_FAIL
    assert "NotFoundError" in result.stderr


# ---------------------------------------------------------------------------
# plan-show: the plan as read, and the diff it would make, both write-free
# ---------------------------------------------------------------------------

def test_show_prints_the_plan_and_preserves_unknown_members(plan_rig):
    path = _prepare(plan_rig)
    document = plans.load_plan(path)
    document["notes"] = ["kept verbatim"]
    document["diagnostics"] = [{"code": "note"}]
    plans.write_plan(plans.seal(document), plan_rig.plan_dir)
    for name in os.listdir(plan_rig.plan_dir):
        if os.path.join(plan_rig.plan_dir, name) != path:
            os.rename(os.path.join(plan_rig.plan_dir, name), path)
    resealed = plans.seal(document)
    with open(path, "wb") as handle:
        handle.write(plans.canonical_bytes(resealed))
    before = tree_fingerprint(plan_rig.plan_dir, plan_rig.project / "mod.py")
    result = runner.invoke(app, ["plan-show", path, "--project", "plansample"])
    assert result.exit_code == 0, result.output
    assert tree_fingerprint(plan_rig.plan_dir,
                            plan_rig.project / "mod.py") == before
    payload = json.loads(result.stdout)
    assert payload["notes"] == ["kept verbatim"]
    assert payload["diagnostics"] == [{"code": "note"}]
    assert payload["plan_id"] == resealed["plan_id"]


def test_show_diff_prints_the_change_and_writes_nothing(plan_rig):
    path = _prepare(plan_rig)
    before = tree_fingerprint(plan_rig.plan_dir, plan_rig.project / "mod.py")
    result = runner.invoke(app, ["plan-show", path, "--project", "plansample",
                                 "--diff"])
    assert result.exit_code == 0, result.output
    assert tree_fingerprint(plan_rig.plan_dir,
                            plan_rig.project / "mod.py") == before
    assert "--- a/mod.py" in result.stdout
    assert "+++ b/mod.py" in result.stdout
    assert "-    return 1" in result.stdout
    assert "+    return 41" in result.stdout


def test_show_diff_refuses_a_stale_file(plan_rig):
    path = _prepare(plan_rig)
    (plan_rig.project / "mod.py").write_text(TWO_FUNCTIONS + "# changed\n")
    result = runner.invoke(app, ["plan-show", path, "--project", "plansample",
                                 "--diff"])
    assert result.exit_code == EXIT_LOCK
    assert "PlanStale:" in result.stderr
    assert "+++ b/mod.py" not in result.stdout


def test_show_json_carries_the_document_and_the_diff(plan_rig):
    path = _prepare(plan_rig)
    result = runner.invoke(app, ["plan-show", path, "--project", "plansample",
                                 "--diff", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == 1
    assert payload["plan"]["operation"] == "replace_body"
    assert payload["diff"][0]["path"] == "mod.py"
    assert "+    return 41" in payload["diff"][0]["diff"]


def test_show_diff_refuses_a_document_that_fails_its_own_hash(plan_rig):
    """A tampered document is refused by the preview, not drawn from it.

    The member that changed sits inside the canonical bytes, so the document
    no longer hashes to the plan_id it carries and the file name it sits under
    is a claim the content does not meet. plan-validate already refuses this;
    the preview path must answer the same way, because a diff from a document
    the apply path would refuse is a picture of a different plan.
    """
    path = _prepare(plan_rig)
    document = plans.load_plan(path)
    tampered = dict(document)
    tampered["created_at"] = "2000-01-01T00:00:00Z"
    with open(path, "wb") as handle:
        handle.write(plans.canonical_bytes(tampered))
    result = runner.invoke(app, ["plan-show", document["plan_id"],
                                 "--project", "plansample", "--diff"])
    assert result.exit_code == EXIT_USAGE
    assert "PlanHashMismatch" in result.stderr
    assert "a plan rewritten after sealing is refused" in result.stderr
    assert "+++ b/mod.py" not in result.stdout


def test_show_refuses_a_reference_that_disagrees_with_the_file(plan_rig):
    """A sha256 reference pointing at other content is a hash mismatch.

    ADR-0004 section 1: a path whose content hash differs from the requested
    hash is refused. The reference names a file that exists and holds a plan;
    only its bytes disagree, which is exactly the hand-edited case.
    """
    path = _prepare(plan_rig)
    other = "sha256:" + "3" * 64
    os.rename(path, os.path.join(plan_rig.plan_dir, "3" * 64 + ".json"))
    result = runner.invoke(app, ["plan-show", other,
                                 "--project", "plansample"])
    assert result.exit_code == EXIT_USAGE
    assert "PlanHashMismatch" in result.stderr
    assert "the reference names" in result.stderr


def test_show_refuses_a_structurally_invalid_plan_in_the_format_taxonomy(
        plan_rig):
    """A broken document is a plan-format refusal, never an internal error.

    A plan missing files[] reached the preview as a KeyError before the schema
    was checked, so a user's bad plan file surfaced as an unclassified
    failure. It must surface as PlanFormatError, with the member named.
    """
    path = _prepare(plan_rig)
    document = plans.load_plan(path)
    broken = {key: value for key, value in document.items() if key != "files"}
    with open(path, "wb") as handle:
        handle.write(plans.canonical_bytes(plans.seal(broken)))
    result = runner.invoke(app, ["plan-show", path, "--project", "plansample",
                                 "--diff", "--json"])
    assert result.exit_code == EXIT_USAGE
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PlanFormatError"
    assert "files" in payload["error"]
    assert "InternalError" not in result.stdout


def test_a_duplicated_files_path_is_refused_with_the_path_named(tmp_path):
    """One path, one record: a files[] duplicate is a format refusal.

    A duplicated entry applied the plan correctly and then reported
    `not_attempted` for the second record of a file it had written, and the
    paths check deduped into a set and printed "1 path(s)", so the only check
    that would have mentioned the duplicate hid it. The duplicate is refused
    here instead, in the format's own vocabulary, with the path named.
    """
    tf = sample(str(tmp_path))
    document = document_for(tf)
    document["files"] = document["files"] + [dict(document["files"][0])]
    with pytest.raises(PlanFormatError) as refusal:
        plans.run_preconditions(plans.seal(document), str(tmp_path))
    assert "more than once" in str(refusal.value)
    assert "mod.py" in str(refusal.value)


