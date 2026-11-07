"""The apply leg: preconditions first, one keeper mutation per edit, a report.

ADR-0005 is the authority for every test here. What is checked is the four
observables it states and nothing stronger: nothing is written before every
precondition passes, each file's bytes move from old to new through keeper's
hash guard, the operations that ran are undoable in reverse order, and there is
no cross-file guarantee. Every test runs in a throwaway project with its own
plan dir and a stub keeper on PATH, so the real checkout is never a target.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import apply_rig  # noqa: E402
from code_indexer import apply as apply_mod  # noqa: E402
from code_indexer import keeper as keeper_mod  # noqa: E402
from code_indexer import plans  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    EXIT_BACKEND,
    EXIT_LOCK,
    EXIT_USAGE,
)

TWO_FUNCTIONS = "def one():\n    return 1\n\ndef two():\n    return 2\n"
THREE_FUNCTIONS = ("def alpha():\n    return 1\n\ndef beta():\n    return 2\n\n"
                    "def gamma():\n    return 3\n")

runner = CliRunner()


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    built = apply_rig.build(tmp_path, monkeypatch,
                            {"mod.py": TWO_FUNCTIONS})
    yield built
    built.registry.close()
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False


@pytest.fixture()
def wide_rig(tmp_path, monkeypatch):
    built = apply_rig.build(tmp_path, monkeypatch, {
        "a.py": THREE_FUNCTIONS, "b.py": THREE_FUNCTIONS})
    yield built
    built.registry.close()
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False


def run(rig, plan_path, *extra):
    return runner.invoke(app, ["plan-apply", plan_path, "--project",
                               apply_rig.PROJECT_NAME, *extra])


# ---------------------------------------------------------------------------
# the happy path: one mutation per edit, and the report of it
# ---------------------------------------------------------------------------

def test_a_plan_applies_one_keeper_mutation_per_edit(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path)
    assert result.exit_code == 0, result.output
    assert "    return 41" in rig.read("mod.py")
    assert "    return 2" in rig.read("mod.py")
    assert len(rig.mutations()) == 1
    assert "mod.py: applied" in result.stdout
    assert "partial: false" in result.stdout


def test_the_report_names_the_plan_the_identity_and_every_file(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    document = plans.load_plan(plan_path)
    assert payload["plan_id"] == document["plan_id"]
    assert payload["identity"] == {"verified": "structural",
                                   "basis": ["file_hash", "decl_hash",
                                             "region_hash"]}
    assert payload["partial"] is False
    assert payload["files"][0]["path"] == "mod.py"
    assert payload["files"][0]["status"] == "applied"
    edit = payload["files"][0]["edits"][0]
    assert edit["start_line"] == 2 and edit["end_line"] == 2
    assert edit["operation_id"] == payload["operations"][0]


def test_every_keeper_call_carries_the_plan_id_and_the_actor(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path)
    assert result.exit_code == 0, result.output
    document = plans.load_plan(plan_path)
    entries = rig.mutations()
    assert entries, "keeper recorded nothing"
    assert {entry["task_id"] for entry in entries} == {document["plan_id"]}
    assert {entry["actor"] for entry in entries} == {"code-indexer"}


def test_the_next_edit_carries_the_hash_keeper_returned(rig):
    plan_path = apply_rig.plan_replacing(
        rig, [("mod.py", "one", "    return 41"),
              ("mod.py", "two", "    return 42")])
    result = run(rig, plan_path)
    assert result.exit_code == 0, result.output
    entries = rig.mutations()
    assert len(entries) == 2
    assert entries[1]["before_hash"] == entries[0]["after_hash"]


def test_files_apply_in_ascending_path_order_and_lines_in_descending_order(
        wide_rig):
    plan_path = apply_rig.plan_replacing(
        wide_rig, [("b.py", "alpha", "    return 11"),
                   ("a.py", "gamma", "    return 33"),
                   ("a.py", "alpha", "    return 11")])
    result = run(wide_rig, plan_path)
    assert result.exit_code == 0, result.output
    order = [(entry["path"]) for entry in wide_rig.mutations()]
    assert order == ["a.py", "a.py", "b.py"]
    # a.py: gamma's body is line 8, alpha's is line 2, so gamma goes first.
    assert "    return 33" in wide_rig.read("a.py")
    assert "    return 11" in wide_rig.read("a.py")


# ---------------------------------------------------------------------------
# the preconditions: one refusal, zero mutations
# ---------------------------------------------------------------------------

def test_a_changed_file_refuses_the_whole_plan_with_zero_mutations(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    before = rig.bytes_of("mod.py")
    rig.write("mod.py", TWO_FUNCTIONS.replace("return 1", "return 9"))
    changed = rig.bytes_of("mod.py")
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_LOCK
    assert rig.bytes_of("mod.py") == changed
    assert changed != before
    assert rig.mutations() == []
    assert not os.path.isdir(os.path.join(rig.root, ".code-indexer",
                                          "receipts"))


def test_a_stale_refusal_is_not_a_partial_apply(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    rig.write("mod.py", TWO_FUNCTIONS.replace("return 1", "return 9"))
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_LOCK
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PlanStale"
    assert "mod.py" in payload["error"]
    assert "sha256:" in payload["error"]


def test_reapplying_a_stale_plan_is_refused_the_same_way(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    first = run(rig, plan_path)
    assert first.exit_code == 0, first.output
    applied = rig.bytes_of("mod.py")
    second = run(rig, plan_path)
    assert second.exit_code == EXIT_LOCK
    assert rig.bytes_of("mod.py") == applied
    assert len(rig.mutations()) == 1


def test_the_pre_pass_and_plan_validate_share_one_code_path(rig, monkeypatch):
    """The two commands call the same function, so they cannot disagree."""
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    rig.write("mod.py", TWO_FUNCTIONS.replace("return 1", "return 9"))
    seen = []
    original = plans.run_preconditions

    def recorder(document, root, requested=None, checks=None):
        seen.append(document.get("plan_id"))
        return original(document, root, requested, checks)

    monkeypatch.setattr(plans, "run_preconditions", recorder)
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_LOCK
    assert seen == [plans.load_plan(plan_path)["plan_id"]]
    validated = runner.invoke(app, ["plan-validate", plan_path,
                                    "--project", apply_rig.PROJECT_NAME])
    assert validated.exit_code == EXIT_LOCK
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------

def test_a_dry_run_reports_every_file_as_would_apply_and_writes_nothing(rig):
    plan_path = apply_rig.plan_replacing(
        rig, [("mod.py", "one", "    return 41"),
              ("mod.py", "two", "    return 42")])
    before = rig.bytes_of("mod.py")
    result = run(rig, plan_path, "--dry-run", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["files"][0]["status"] == "would_apply"
    assert [edit["operation_id"] for edit in payload["files"][0]["edits"]] \
        == [None, None]
    assert rig.bytes_of("mod.py") == before
    assert rig.mutations() == []
    assert payload["receipt_path"] is None


def test_a_dry_run_asks_keeper_to_preview_every_edit(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "no file was written" in result.stdout
    assert "would_apply" in result.stdout


def test_a_dry_run_still_refuses_a_stale_plan(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    rig.write("mod.py", TWO_FUNCTIONS.replace("return 1", "return 9"))
    result = run(rig, plan_path, "--dry-run")
    assert result.exit_code == EXIT_LOCK


# ---------------------------------------------------------------------------
# keeper absent, or short a guard
# ---------------------------------------------------------------------------

def test_keeper_absent_is_exit_three_with_the_install_line(rig, tmp_path,
                                                           monkeypatch):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    before = rig.bytes_of("mod.py")
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_BACKEND
    assert "KeeperUnavailable" in result.stderr
    assert keeper_mod.MINIMUM_VERSION in result.stderr
    assert "uv tool install filekeeper-cli" in result.stderr
    # no fallback path: the file is exactly as it was
    assert rig.bytes_of("mod.py") == before
    assert rig.mutations() == []


def test_a_keeper_without_the_hash_guard_is_capability_missing(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    monkeypatch.setenv("STUB_KEEPER_DROP_FLAG", "--expected-hash")
    before = rig.bytes_of("mod.py")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_BACKEND
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "KeeperCapabilityMissing"
    assert "--expected-hash" in payload["error"]
    assert rig.bytes_of("mod.py") == before


def test_the_probe_asks_for_every_flag_it_will_pass(rig):
    adapter = keeper_mod.Keeper(rig.root, plan_id="sha256:" + "0" * 64)
    adapter.probe()  # the stub advertises all three; nothing is raised
    assert set(keeper_mod.REQUIRED_FLAGS) == {
        "--expected-hash", "--expected-content", "--dry-run"}


# ---------------------------------------------------------------------------
# post-apply parse verdicts, and the receipt
# ---------------------------------------------------------------------------

def test_the_post_apply_verdict_is_the_parsers_and_names_the_bad_lines(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return (((")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    verdict = payload["post_apply"][0]
    assert verdict["path"] == "mod.py"
    assert verdict["verdict"] == "parse_error"
    assert verdict["lines"]
    assert "not a file that compiles" in payload["caveat"]


def test_a_modified_file_that_parses_reports_parsed(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path, "--json")
    payload = json.loads(result.stdout)
    assert payload["post_apply"] == [{"path": "mod.py", "verdict": "parsed",
                                      "lines": []}]


def test_a_file_with_no_grammar_reports_parse_unavailable(rig):
    rig.write("notes.txt", "just text\n")
    verdicts = apply_mod.post_apply_verdict(rig.root, ["notes.txt"])
    assert verdicts[0]["verdict"] == "parse_unavailable"


def test_the_receipt_is_json_named_by_the_plan_hash_and_printed(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path)
    assert result.exit_code == 0, result.output
    document = plans.load_plan(plan_path)
    receipts = os.path.join(rig.root, ".code-indexer", "receipts")
    names = os.listdir(receipts)
    assert len(names) == 1
    digest = document["plan_id"].split(":", 1)[1]
    assert names[0].startswith(digest + "-")
    assert names[0].endswith(".json")
    with open(os.path.join(receipts, names[0]), encoding="utf-8") as handle:
        receipt = json.load(handle)
    assert receipt["plan_id"] == document["plan_id"]
    assert receipt["operations"] == [entry["operation_id"]
                                     for entry in rig.mutations()]
    assert os.path.join(receipts, names[0]) in result.stdout


def test_refresh_runs_one_incremental_pass_after_the_apply(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    calls = []
    monkeypatch.setattr(rig.core, "refresh", lambda entry: calls.append(entry))
    result = run(rig, plan_path, "--refresh", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["refresh"] == "ran"
    assert len(calls) == 1


def test_without_refresh_no_index_pass_runs(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path, "--json")
    payload = json.loads(result.stdout)
    assert payload["refresh"] == "skipped"


# ---------------------------------------------------------------------------
# the vocabulary fence
# ---------------------------------------------------------------------------

def test_no_surface_claims_a_cross_file_guarantee(rig):
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    result = run(rig, plan_path)
    assert result.exit_code == 0, result.output
    # The receipt line carries the tmp path the test itself is named after.
    output = "\n".join(line for line in result.stdout.splitlines()
                       if not line.startswith("receipt ")).lower()
    usage = runner.invoke(app, ["plan-apply", "--help"]).stdout.lower()
    for banned in ("atomic", "transaction", "all-or-nothing"):
        assert banned not in output, banned
        assert banned not in usage, banned
    sources = [apply_mod.__doc__ or "", keeper_mod.__doc__ or ""]
    sources.append(apply_mod.apply_plan.__doc__ or "")
    for text in sources:
        for banned in ("atomic", "transaction", "all-or-nothing"):
            assert banned not in text.lower(), banned


# ---------------------------------------------------------------------------
# one path, one record: a duplicated files[] entry
# ---------------------------------------------------------------------------

def test_a_duplicated_files_path_is_refused_before_any_mutation(rig):
    """The duplicate is refused, not collapsed silently into one report row.

    The same document applied correctly at the base commit and then reported
    `not_attempted` for the second record of a file it had written. Refusing
    the document is the honest answer: a plan that names one path twice has
    two edits' worth of intent and one file's worth of report, and the paths
    check that would say so deduped the duplicate away.
    """
    plan_path = apply_rig.plan_replacing_one(rig, "mod.py", "one",
                                             "    return 41")
    document = plans.load_plan(plan_path)
    document["files"] = document["files"] + [dict(document["files"][0])]
    duplicated = plans.write_plan(plans.seal(document), rig.plan_dir)
    result = run(rig, duplicated, "--json")
    assert result.exit_code == EXIT_USAGE
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PlanFormatError"
    assert "more than once" in payload["error"]
    assert "mod.py" in payload["error"]
    assert rig.mutations() == []
    assert "    return 41" not in rig.read("mod.py")


# ---------------------------------------------------------------------------
# the shapes the suite lacked: nesting, non-ASCII bytes, attribute blocks
# ---------------------------------------------------------------------------

NESTED = ("class Outer:\n"
          "    def inner(self):\n"
          "        return 1\n"
          "\n"
          "    def other(self):\n"
          "        return 2\n")

UNICODE = ("# héllo — ünïcode\n"
           "def alpha():\n"
           "    return 'héllo'\n"
           "\n"
           "\n"
           "def beta():\n"
           "    return '日本語'\n")

DECORATED = ("import functools\n"
             "\n"
             "\n"
             "@functools.cache\n"
             "def decorated():\n"
             "    return 1\n"
             "\n"
             "\n"
             "class Thing:\n"
             "    attr = 1\n"
             "\n"
             "    @staticmethod\n"
             "    def helper():\n"
             "        return attr\n")


@pytest.fixture()
def make_rig(tmp_path, monkeypatch):
    """Build one throwaway project per call and close what it opened."""
    built_rigs: list = []

    def build(files):
        built = apply_rig.build(tmp_path, monkeypatch, files)
        built_rigs.append(built)
        return built

    yield build
    for built in built_rigs:
        built.registry.close()
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False


def test_a_nested_declaration_is_located_through_its_container(make_rig):
    """Container recursion: the method's identity carries the class name.

    Two methods in one class share a kind and a file; only the qualified name
    and the declaration hash tell them apart, and only the container recursion
    in the walk builds that name. The body path is the method's own block, so
    the class line and the sibling method are outside the edit.
    """
    built = make_rig({"nest.py": NESTED})
    tf = plans.read_text_file(built.root, "nest.py")
    derived, _verdict = plans.declarations("nest.py", tf.text)
    assert [decl.qualified_name for decl in derived] == [
        "Outer", "Outer.inner", "Outer.other"]
    inner = [decl for decl in derived if decl.qualified_name == "Outer.inner"][0]
    assert (inner.start_line, inner.body_start_line) == (2, 3)
    plan_path = apply_rig.plan_replacing_one(
        built, "nest.py", "inner", "        return 41")
    document = plans.load_plan(plan_path)
    assert document["target"]["qualified_name"] == "Outer.inner"
    assert document["target"]["range"]["start_line"] == 2
    assert document["edits"][0]["start_line"] == 3
    result = run(built, plan_path)
    assert result.exit_code == 0, result.output
    assert built.read("nest.py") == (
        "class Outer:\n"
        "    def inner(self):\n"
        "        return 41\n"
        "\n"
        "    def other(self):\n"
        "        return 2\n")
    assert len(built.mutations()) == 1


def test_a_non_ascii_line_keeps_line_and_byte_arithmetic_in_agreement(
        make_rig):
    """Byte geometry where lines and bytes diverge and ASCII stays green.

    The line is sixteen characters and more than sixteen bytes, so an offset
    computed as a character index and one computed as a byte index disagree
    here and agree everywhere the rest of the suite looks. The locator records
    bytes, the pre-pass cross-checks them against the line, and the applied
    file is byte-for-byte what the replacement says.
    """
    built = make_rig({"uni.py": UNICODE})
    plan_path = apply_rig.plan_replacing_one(
        built, "uni.py", "beta", "    return 'さようなら'")
    document = plans.load_plan(plan_path)
    prefix = "".join(UNICODE.splitlines(keepends=True)[:5])
    target_range = document["target"]["range"]
    assert target_range["start_line"] == 6
    assert target_range["start_byte"] == len(prefix.encode("utf-8"))
    # The two arithmetic answers differ on this line; that is the point.
    assert target_range["start_byte"] > len(prefix)
    assert document["edits"][0]["start_line"] == 7
    checked = runner.invoke(app, ["plan-validate", plan_path, "--project",
                                  apply_rig.PROJECT_NAME])
    assert checked.exit_code == 0, checked.output
    assert "line and byte cross-check: ok" in checked.stdout
    result = run(built, plan_path)
    assert result.exit_code == 0, result.output
    assert built.bytes_of("uni.py") == UNICODE.replace(
        "    return '日本語'", "    return 'さようなら'").encode("utf-8")


def test_an_attribute_block_stays_with_its_declaration(make_rig):
    """Range and locator behaviour around a decorator.

    A decorated definition's range begins at its decorator, its body path is
    the inner function's block, so a body replacement leaves the decorator
    untouched; an insertion before it lands above the decorator rather than
    between the decorator and the declaration it belongs to.
    """
    built = make_rig({"deco.py": DECORATED})
    tf = plans.read_text_file(built.root, "deco.py")
    derived, _verdict = plans.declarations("deco.py", tf.text)
    by_name = {decl.qualified_name: decl for decl in derived}
    assert by_name["decorated"].start_line == 4
    assert by_name["decorated"].body_start_line == 6
    assert by_name["Thing.helper"].body_start_line == 14
    replace_path = apply_rig.plan_replacing_one(
        built, "deco.py", "decorated", "    return 41")
    replace_document = plans.load_plan(replace_path)
    assert replace_document["target"]["range"]["start_line"] == 4
    assert replace_document["edits"][0]["start_line"] == 6
    insertion = plans.insert_symbol_edit(tf, by_name["decorated"], "before",
                                         "SETTING = 1")
    assert insertion["start_line"] == 4
    assert insertion["start_byte"] == tf.line_starts[3]
    result = run(built, replace_path)
    assert result.exit_code == 0, result.output
    assert built.read("deco.py").startswith(
        "import functools\n\n\n@functools.cache\ndef decorated():\n"
        "    return 41\n")
    assert "    @staticmethod\n    def helper():\n        return attr\n" \
        in built.read("deco.py")
