"""Every scanned file is accounted for -- no silent drops.

A file the walk sees ends in exactly one bucket: indexed (parsed,
parse_error or parse_unavailable), uncommitted (the pass read it but could
not put it in the index) or skipped for a named reason. The counts have to
reconcile in the payload, because "403 indexed" against "450 on disk" with
no explanation is not an answer a reader can act on.
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
from code_indexer.errors import NotFoundError  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
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
    # The identity the code implements, stated once: `indexed` is what the
    # manifest holds after the pass, `uncommitted` is scanned-but-not-stored,
    # `skipped_total` is what the walk named a reason for. A fixture that
    # loses nothing cannot tell this apart from `walked == indexed + skipped`,
    # which is why the lost-file case below exists.
    assert (acc["walked"]
            == acc["indexed"] + acc["uncommitted"] + acc["skipped_total"]), acc
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
    assert (acc["walked"]
            == acc["indexed"] + acc["uncommitted"] + acc["skipped_total"])
    assert data["parse_error_files"] == ["broken.py"]


def test_outline_names_the_reason_a_file_is_absent(payload):
    core, entry, _, _ = payload
    with pytest.raises(NotFoundError) as excinfo:
        core.outline(entry, "blob.bin")
    message = str(excinfo.value)
    assert "blob.bin" in message
    assert "binary" in message


LOST_MARKER = "the one file whose every chunk fails to embed"


class _DroppingEmbedder:
    """A stub embedder that cannot embed one file, and only that file."""

    def dimension(self) -> int:
        return 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts: list[str]):
        vectors: list[list[float] | None] = []
        errors: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            if LOST_MARKER in text:
                vectors.append(None)
                errors.append((i, "embed failed: backend refused the text"))
            else:
                vectors.append([0.1] * 4)
        return vectors, errors

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
        pass


def test_outline_names_the_class_of_a_skip_past_the_named_examples(
        tmp_path, monkeypatch):
    """The 51st skipped file is still counted AND named by class.

    Only the first few files of each reason are recorded by path, so an
    outline on a path past that sample used to answer a bare "file not
    indexed" -- the one answer the round's promise rules out.
    """
    project = tmp_path / "manybins"
    project.mkdir()
    for i in range(51):
        (project / f"blob{i:02d}.bin").write_bytes(b"\x00\x01payload")
    (project / "app.py").write_text("def app():\n    return 1\n")
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    data = json.loads(runner.invoke(
        app, ["index-status", str(entry.path), "--json"]).output)
    named = set(data["scan_accounting"]["examples"]["binary"])
    unnamed = sorted({f"blob{i:02d}.bin" for i in range(51)} - named)
    assert unnamed, "the example sample is capped below the 51 skipped files"
    with pytest.raises(NotFoundError) as excinfo:
        core.outline(entry, unnamed[0])
    message = str(excinfo.value)
    assert "binary" in message, message
    assert "51" in message, message


def test_index_status_carries_the_key_before_any_pass(tmp_path, monkeypatch):
    """One shape: `index-status` and `overview` both always carry
    `scan_accounting`, null until a pass records one."""
    project = tmp_path / "nopass"
    project.mkdir()
    (project / "a.py").write_text("def a():\n    return 1\n")
    core = stub_core(tmp_path)
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="nopass")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(entry.path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "scan_accounting" in data
    assert data["scan_accounting"] is None


def test_a_file_whose_chunks_all_fail_is_counted_uncommitted(tmp_path,
                                                            monkeypatch):
    """`uncommitted` is a real bucket: the pass read the file and could not
    put it in the index, so `walked == indexed + skipped_total` is FALSE."""
    project = tmp_path / "lostproj"
    project.mkdir()
    (project / "kept.py").write_text("def kept():\n    return 1\n")
    (project / "lost.py").write_text(
        f"def lost():\n    return '{LOST_MARKER}'\n")
    core = stub_core(tmp_path)
    core.embedder = _DroppingEmbedder()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="lostproj")
    core.run_index(entry.slug, str(project), vectors="on")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(entry.path), "--json"])
    assert result.exit_code == 0, result.output
    acc = json.loads(result.output)["scan_accounting"]
    assert acc["uncommitted"] == 1, acc
    assert (acc["walked"]
            == acc["indexed"] + acc["uncommitted"] + acc["skipped_total"]), acc
    m = core.manifest_for(entry.slug)
    try:
        assert m.get_file("lost.py") is None
        assert m.get_file("kept.py") is not None
    finally:
        m.close()


def _text_read_fails_for(victim: str):
    """An open() that fails for one path's TEXT read, after the scan read it."""
    real_open = open

    def guarded_open(file, mode="r", *args, **kwargs):
        # The scan reads bytes (`rb`) and succeeds; the parse reads text later
        # and fails, which is the read failure these tests name.
        if str(file) == victim and "b" not in mode:
            raise OSError("permission denied (simulated after the scan)")
        return real_open(file, mode, *args, **kwargs)

    return guarded_open


def test_a_file_that_cannot_be_read_is_a_named_skip(tmp_path, monkeypatch):
    """A read failure after the scan is a skip with a reason, not a file
    parked in `uncommitted` under a cause that is not true."""
    project = tmp_path / "unreadableproj"
    project.mkdir()
    (project / "kept.py").write_text("def kept():\n    return 1\n")
    (project / "late.py").write_text("def late():\n    return 2\n")
    core = stub_core(tmp_path)
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="unreadableproj")
    monkeypatch.setattr("builtins.open",
                        _text_read_fails_for(str(project / "late.py")))
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(entry.path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    acc = data["scan_accounting"]
    assert acc["skipped"].get("unreadable") == 1, acc
    assert acc["uncommitted"] == 0, acc
    assert (acc["walked"]
            == acc["indexed"] + acc["uncommitted"] + acc["skipped_total"]), acc
    assert "late.py" in acc["examples"]["unreadable"]
    with pytest.raises(NotFoundError) as excinfo:
        core.outline(entry, "late.py")
    assert "unreadable" in str(excinfo.value)


def test_a_read_failure_on_a_later_pass_is_counted_once(tmp_path, monkeypatch):
    """An older row for a now-unreadable file is not this pass's work: the
    file lands in one bucket, not in `indexed` and `unreadable` at once, and
    the row is not thrown away for a condition the next pass may lift."""
    project = tmp_path / "laterproj"
    project.mkdir()
    (project / "kept.py").write_text("def kept():\n    return 1\n")
    (project / "late.py").write_text("def late():\n    return 2\n")
    core = stub_core(tmp_path)
    reg = Registry(os.path.join(core.cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="laterproj")
    core.run_index(entry.slug, str(project))   # a clean first pass
    (project / "late.py").write_text("def late():\n    return 3\n")
    monkeypatch.setattr("builtins.open",
                        _text_read_fails_for(str(project / "late.py")))
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    data = json.loads(runner.invoke(
        app, ["index-status", str(entry.path), "--json"]).output)
    acc = data["scan_accounting"]
    assert acc["skipped"].get("unreadable") == 1, acc
    assert acc["uncommitted"] == 0, acc
    assert (acc["walked"]
            == acc["indexed"] + acc["uncommitted"] + acc["skipped_total"]), acc
    # The older row survives, so the index still answers for the file.
    assert core.outline(entry, "late.py")["file"] == "late.py"
