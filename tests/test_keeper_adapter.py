"""The filekeeper boundary itself: an argv list, a probe, an envelope.

Nothing here applies a plan. These tests pin the three things the adapter
promises: it starts a subprocess and never a shell, it refuses a keeper whose
parser does not advertise the guards it is about to pass, and it reads the
JSON envelope by name -- never human text, never stdout by position.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

import keeper_stub  # noqa: E402
from code_indexer import keeper as keeper_mod  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    EXIT_BACKEND,
    KeeperCapabilityMissingError,
    KeeperUnavailableError,
)

PLAN_ID = "sha256:" + "a" * 64


@pytest.fixture()
def env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    keeper_stub.install(str(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep
                       + os.environ.get("PATH", ""))
    monkeypatch.setenv("STUB_KEEPER_HOME", str(tmp_path / "keeper-home"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "mod.py").write_text("one\ntwo\nthree\n")
    return project


# ---------------------------------------------------------------------------
# the capability probe, by flag
# ---------------------------------------------------------------------------

def test_the_probe_accepts_a_keeper_that_advertises_every_guard(env):
    keeper_mod.Keeper(str(env), plan_id=PLAN_ID).probe()


@pytest.mark.parametrize("flag", keeper_mod.REQUIRED_FLAGS)
def test_a_missing_flag_is_capability_missing_and_names_it(env, monkeypatch,
                                                           flag):
    monkeypatch.setenv("STUB_KEEPER_DROP_FLAG", flag)
    with pytest.raises(KeeperCapabilityMissingError) as caught:
        keeper_mod.Keeper(str(env), plan_id=PLAN_ID).probe()
    assert flag in str(caught.value)
    assert caught.value.exit_code == EXIT_BACKEND
    assert "uv tool install filekeeper-cli" in str(caught.value)


def test_an_absent_keeper_is_unavailable_with_the_install_line(env, tmp_path,
                                                              monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(KeeperUnavailableError) as caught:
        keeper_mod.Keeper(str(env), plan_id=PLAN_ID).probe()
    assert keeper_mod.MINIMUM_VERSION in str(caught.value)
    assert caught.value.exit_code == EXIT_BACKEND


# ---------------------------------------------------------------------------
# the envelope, read by name
# ---------------------------------------------------------------------------

def test_a_mutation_envelope_is_read_by_name(env):
    adapter = keeper_mod.Keeper(str(env), plan_id=PLAN_ID)
    adapter.probe()
    before = _hash(env / "mod.py")
    envelope = adapter.replace_lines(
        "mod.py", start=2, end=2, replacement="TWO", expected_hash=before)
    assert envelope.ok is True
    assert envelope.operation_id and envelope.operation_id.startswith("op_")
    assert envelope.undo_available is True
    assert envelope.before_hash == before
    assert envelope.after_hash == _hash(env / "mod.py")
    assert envelope.after_hash != before
    assert (env / "mod.py").read_text() == "one\nTWO\nthree\n"


def test_a_refusal_carries_the_code_the_message_and_the_path(env):
    adapter = keeper_mod.Keeper(str(env), plan_id=PLAN_ID)
    envelope = adapter.replace_lines(
        "mod.py", start=1, end=1, replacement="X",
        expected_hash="sha256:" + "0" * 64)
    assert envelope.ok is False
    assert envelope.error_code == "CONTENT_CONFLICT"
    assert envelope.error_path == "mod.py"
    assert envelope.exit_code == 5
    assert (env / "mod.py").read_text() == "one\ntwo\nthree\n"


def test_a_dry_run_returns_no_operation_and_no_after_hash(env):
    adapter = keeper_mod.Keeper(str(env), plan_id=PLAN_ID)
    before = _hash(env / "mod.py")
    envelope = adapter.replace_lines(
        "mod.py", start=1, end=1, replacement="X", expected_hash=before,
        dry_run=True)
    assert envelope.ok is True
    assert envelope.operation_id is None
    assert envelope.after_hash is None
    assert envelope.predicted_after_hash
    assert (env / "mod.py").read_text() == "one\ntwo\nthree\n"


def test_a_path_that_begins_with_a_dash_still_reaches_keeper(env):
    (env / "-dash.py").write_text("one\n")
    adapter = keeper_mod.Keeper(str(env), plan_id=PLAN_ID)
    envelope = adapter.file_hash("-dash.py")
    assert envelope.ok is True


def test_output_that_is_not_one_json_object_is_a_refusal(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "keeper"
    script.write_text("#!/bin/sh\necho 'replaced line 2 of a.txt'\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    adapter = keeper_mod.Keeper(str(tmp_path), plan_id=PLAN_ID)
    envelope = adapter.replace_lines(
        "mod.py", start=1, end=1, replacement="X",
        expected_hash="sha256:" + "0" * 64)
    assert envelope.ok is False
    assert envelope.error_code == "UNREADABLE_JSON_ENVELOPE"


def _hash(path):
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
