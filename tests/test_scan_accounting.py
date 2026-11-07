"""Every scanned file is accounted for -- no silent drops.

A file the walk sees ends in exactly one bucket: indexed (parsed,
parse_error or parse_unavailable) or skipped for a named reason. The counts
have to reconcile in the payload, because "403 indexed" against "450 on disk"
with no explanation is not an answer a reader can act on.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

runner = CliRunner()


def _tree(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("*.log\nvendor/\n")
    (project / "app.py").write_text("def greet(name):\n    return name\n")
    (project / "broken.py").write_text("def broken(:\n    pass\n")
    (project / "README.md").write_text("# Title\n\nsome prose here\n")
    (project / "blob.bin").write_bytes(b"\x00\x01\x02 binary payload\n")
    (project / "notes.txt").write_bytes(b"caf\xe9 au lait\n")
    (project / "big.py").write_text("# " + "y" * 400 + "\n")
    (project / "debug.log").write_text("a log line\n")
    vendor = project / "vendor"
    vendor.mkdir()
    (vendor / "lib.cs").write_text("public class Lib {}\n")
    (vendor / "extra.cs").write_text("public class Extra {}\n")
    return project


@pytest.fixture()
def payload(tmp_path, monkeypatch):
    project = _tree(tmp_path)
    core = stub_core(tmp_path, max_file_bytes=200)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(entry.path), "--json"])
    assert result.exit_code == 0, result.output
    return core, entry, project, json.loads(result.output)


def test_the_counts_reconcile(payload):
    core, entry, project, data = payload
    acc = data["scan_accounting"]
    m = core.manifest_for(entry.slug)
    try:
        indexed = len(m.all_files())
    finally:
        m.close()
    assert acc["indexed"] == indexed
    assert acc["walked"] == acc["indexed"] + acc["skipped_total"], acc
    assert acc["skipped_total"] == sum(acc["skipped"].values())


def test_every_skip_has_a_named_reason(payload):
    _, _, _, data = payload
    skipped = data["scan_accounting"]["skipped"]
    assert skipped["binary"] == 1
    assert skipped["non-utf8"] == 1
    assert skipped["oversized"] == 1
    assert skipped["ignored"] == 1
    assert skipped["ignored-directory"] == 2
    assert set(skipped) <= {
        "ignored", "ignored-directory", "oversized", "binary", "non-utf8",
        "unreadable", "out-of-scope", "sensitive"}


def test_the_parse_error_files_are_named(payload):
    _, _, _, data = payload
    assert data["parse_error_files"] == ["broken.py"]
    assert data["parsed"] >= 1
    assert data["parse_error"] == 1


def test_overview_carries_the_same_reconciliation(tmp_path, monkeypatch):
    project = _tree(tmp_path)
    core = stub_core(tmp_path, max_file_bytes=200)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    data = json.loads(runner.invoke(
        app, ["overview", "--project", str(entry.path), "--json"]).output)
    acc = data["scan_accounting"]
    assert acc["walked"] == acc["indexed"] + acc["skipped_total"]
    assert data["parse_error_files"] == ["broken.py"]


def test_outline_names_the_reason_a_file_is_absent(payload):
    core, entry, _, _ = payload
    with pytest.raises(ValueError) as excinfo:
        core.outline(entry, "blob.bin")
    message = str(excinfo.value)
    assert "blob.bin" in message
    assert "binary" in message
