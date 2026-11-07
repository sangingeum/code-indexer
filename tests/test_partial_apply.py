"""A plan that fails partway: what stays written, and the way back.

The case ADR-0005's Consequences names: file 3 of 5 fails, files 1 and 2 keep
the plan's new bytes, file 3 keeps its old bytes, files 4 and 5 are untouched.
The run is exit 1 with ``PartialApply`` and the report on stdout; recovery is
the ordered operation ids undone in reverse, and a refused undo is reported
rather than retried into place.
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
from code_indexer.errors import EXIT_FAIL, EXIT_LOCK  # noqa: E402

runner = CliRunner()

FILES = {f"f{index}.py": f"def fn{index}():\n    return {index}\n"
         for index in range(1, 6)}
TARGETS = [(f"f{index}.py", f"fn{index}", f"    return 1{index}")
           for index in range(1, 6)]


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    built = apply_rig.build(tmp_path, monkeypatch, dict(FILES))
    yield built
    built.registry.close()
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False


def run(rig, plan_path, *extra):
    return runner.invoke(app, ["plan-apply", plan_path, "--project",
                               apply_rig.PROJECT_NAME, *extra])


class CountingKeeper:
    """Wraps the adapter so a test can count the undos that were asked for."""

    def __init__(self, inner):
        self.inner = inner
        self.undos: list[str] = []

    def undo(self, operation_id):
        self.undos.append(operation_id)
        return self.inner.undo(operation_id)


# ---------------------------------------------------------------------------
# the five-file failure
# ---------------------------------------------------------------------------

def test_file_three_of_five_fails_and_the_later_files_are_untouched(
        rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    original = {name: rig.read(name) for name in FILES}
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_FAIL
    payload = json.loads(result.stdout)
    statuses = {item["path"]: item["status"] for item in payload["files"]}
    assert statuses == {"f1.py": "applied", "f2.py": "applied",
                        "f3.py": "failed", "f4.py": "not_attempted",
                        "f5.py": "not_attempted"}
    assert payload["partial"] is True
    assert payload["ErrorType"] == "PartialApply"
    # The bytes, which are the promise: 1 and 2 new, 3, 4 and 5 as they were.
    assert "return 11" in rig.read("f1.py")
    assert "return 12" in rig.read("f2.py")
    for name in ("f3.py", "f4.py", "f5.py"):
        assert rig.read(name) == original[name]


def test_the_failure_aborts_without_a_compensating_write(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_FAIL
    assert len(rig.mutations()) == 2
    assert "f4.py" not in result.stdout.split("f3.py")[1].replace(
        "f4.py: not_attempted", "")


def test_the_report_names_the_file_the_edit_and_the_keeper_code(rig,
                                                               monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path, "--json")
    payload = json.loads(result.stdout)
    failed = [item for item in payload["files"]
              if item["status"] == "failed"][0]
    edit = failed["edits"][0]
    assert edit["start_line"] == 2 and edit["end_line"] == 2
    assert edit["error_code"] == "INVALID_ARGUMENT"
    assert edit["error_message"]
    assert "f3.py" in payload["error"]
    assert "INVALID_ARGUMENT" in payload["error"]


def test_partial_apply_names_itself_on_stderr_and_exits_one(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_FAIL
    assert "PartialApply:" in result.stderr
    assert "f1.py: applied" in result.stdout
    assert "f5.py: not_attempted" in result.stdout
    assert "partial: true" in result.stdout


def test_a_failure_before_any_write_is_keeper_failure_not_a_partial(rig,
                                                                   monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "1")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_FAIL
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "KeeperFailure"
    assert payload["partial"] is False
    assert payload["operations"] == []
    assert payload["receipt_path"] is None


def test_keeper_five_in_the_loop_maps_to_five(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "2")
    monkeypatch.setenv("STUB_KEEPER_FAIL_EXIT", "5")
    monkeypatch.setenv("STUB_KEEPER_FAIL_CODE", "CONTENT_CONFLICT")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_LOCK
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PlanStale"
    assert "CONTENT_CONFLICT" in payload["error"]
    # The report still says what had already been written, and how to undo it.
    assert payload["operations"]
    assert payload["undo"] == [f"keeper history undo {op}"
                               for op in reversed(payload["operations"])]


# ---------------------------------------------------------------------------
# the recovery reference
# ---------------------------------------------------------------------------

def test_recovery_undoes_every_operation_in_reverse_order(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    original = {name: rig.read(name) for name in FILES}
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_FAIL
    payload = json.loads(result.stdout)
    operations = payload["operations"]
    assert len(operations) == 2
    assert payload["undo"] == [f"keeper history undo {op}"
                               for op in reversed(operations)]
    monkeypatch.delenv("STUB_KEEPER_FAIL_AT")
    adapter = keeper_mod.Keeper(rig.root, plan_id=payload["plan_id"])
    recovery = apply_mod.recover(operations, adapter)
    assert recovery.stopped is False
    assert [step.operation_id for step in recovery.steps] \
        == list(reversed(operations))
    for name in ("f1.py", "f2.py"):
        assert rig.read(name) == original[name]


def test_a_refused_undo_is_reported_and_never_retried(rig, monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path, "--json")
    assert result.exit_code == EXIT_FAIL
    operations = json.loads(result.stdout)["operations"]
    monkeypatch.delenv("STUB_KEEPER_FAIL_AT")
    monkeypatch.setenv("STUB_KEEPER_UNDO_REFUSE", "1")
    adapter = keeper_mod.Keeper(rig.root, plan_id="sha256:" + "0" * 64)
    counting = CountingKeeper(adapter)
    recovery = apply_mod.recover(operations, counting)
    assert recovery.stopped is True
    assert len(recovery.steps) == 1
    assert recovery.steps[0].error_code == "UNDO_CONFLICT"
    # One refusal ends recovery: nothing was tried a second time.
    assert counting.undos == [operations[-1]]
    assert recovery.failure.operation_id == operations[-1]


def test_the_ordered_operation_ids_are_the_recovery_reference(rig,
                                                              monkeypatch):
    plan_path = apply_rig.plan_replacing(rig, TARGETS)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "3")
    result = run(rig, plan_path)
    assert result.exit_code == EXIT_FAIL
    document = plans.load_plan(plan_path)
    receipts = os.path.join(rig.root, ".code-indexer", "receipts")
    assert os.path.isdir(receipts)
    names = os.listdir(receipts)
    assert len(names) == 1
    with open(os.path.join(receipts, names[0]), encoding="utf-8") as handle:
        receipt = json.load(handle)
    assert receipt["plan_id"] == document["plan_id"]
    assert receipt["partial"] is True
    assert [entry["operation_id"] for entry in rig.mutations()] \
        == receipt["operations"]
    for command in receipt["undo"]:
        assert command.startswith("keeper history undo op_")


# ---------------------------------------------------------------------------
# one file, two edits, the second refused: a half-written file is partial
# ---------------------------------------------------------------------------

PAIR = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"


@pytest.fixture()
def pair_rig(tmp_path, monkeypatch):
    """One file carrying two declarations, so a plan can hold two of its edits."""
    built = apply_rig.build(tmp_path, monkeypatch, {"mod.py": PAIR})
    yield built
    built.registry.close()
    import code_indexer.cli as cli

    cli._core = None
    cli._core_skip = False


def two_edit_plan(rig) -> str:
    """Two edits on one file; the format applies the later line first."""
    return apply_rig.plan_replacing(
        rig, [("mod.py", "two", "    return 22"),
              ("mod.py", "one", "    return 11")])


def test_two_edits_on_one_file_second_refused_is_partial_and_named(
        pair_rig, monkeypatch):
    """The report says partial, names the landed edit, and keeps one record.

    The first edit's bytes are on disk and one keeper operation exists when
    the second is refused. `partial` is the documented answer to "did anything
    get written", so it must be true here; a status of `failed` alone cannot
    express a file whose first edit landed, and a per-file record count must
    stay at one for a file named once.
    """
    plan_path = two_edit_plan(pair_rig)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "2")
    result = run(pair_rig, plan_path, "--json")
    assert result.exit_code == EXIT_FAIL
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "PartialApply"
    assert payload["partial"] is True
    assert [item["path"] for item in payload["files"]] == ["mod.py"]
    record = payload["files"][0]
    assert record["status"] == "partially_applied"
    landed = [edit for edit in record["edits"] if edit["operation_id"]]
    refused = [edit for edit in record["edits"] if edit.get("error_code")]
    assert len(landed) == 1 and landed[0]["start_line"] == 6
    assert len(refused) == 1 and refused[0]["start_line"] == 2
    assert payload["operations"] == [landed[0]["operation_id"]]
    assert payload["undo"] == [
        f"keeper history undo {landed[0]['operation_id']}"]
    # The bytes: the landed edit is written, the refused one is untouched.
    assert "return 22" in pair_rig.read("mod.py")
    assert "    return 1\n" in pair_rig.read("mod.py")


def test_the_text_report_states_how_many_edits_of_the_file_landed(
        pair_rig, monkeypatch):
    plan_path = two_edit_plan(pair_rig)
    monkeypatch.setenv("STUB_KEEPER_FAIL_AT", "2")
    result = run(pair_rig, plan_path)
    assert result.exit_code == EXIT_FAIL
    assert "mod.py: partially_applied" in result.stdout
    assert "1 of 2 edit(s) in mod.py landed; the rest were refused" \
        in result.stdout
    assert "partial: true" in result.stdout
    assert "keeper history undo op_" in result.stdout
