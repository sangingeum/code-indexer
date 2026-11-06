"""The read contract (ADR-0003 sections 1-4, 6-7), asserted as behaviour.

A read command answers from the index as of ``meta.last_indexed``, reports a
``fresh`` / ``stale`` / ``unknown`` verdict, and writes NOTHING: the manifest
bytes and ``last_indexed`` are unchanged, and the staleness path contacts no
service. ``--refresh`` is the only flag that makes a read write, and it fails
hard (exit 5, no answer) when another process holds the per-project flock.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.main import get_command as typer_get_command  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import command_classes  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.indexer import Indexer  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


class RecordingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def dimension(self) -> int:
        self.calls += 1
        return 4

    def dimension_once(self) -> int:
        self.calls += 1
        return 4

    def embed(self, texts):
        self.calls += 1
        return [[0.1] * 4 for _ in texts]

    def embed_query_once(self, text):
        self.calls += 1
        return [0.1] * 4

    def query_instruction_text(self, query: str) -> str:
        return query

    def embed_with_errors(self, texts):
        self.calls += 1
        return [[0.1] * 4 for _ in texts], []

    def close(self) -> None:
        pass


class RecordingStore:
    def __init__(self, hits=None, collections=None) -> None:
        self.calls = 0
        self.hits = hits or []
        self.collections = collections or set()

    def collection_exists(self, name):
        self.calls += 1
        return True

    def create_collection(self, name, dim):
        self.calls += 1

    def upsert_points(self, name, points):
        self.calls += 1
        return len(points)

    def purge_file_points(self, *a, **kw):
        self.calls += 1
        return 0

    def count_points(self, name):
        self.calls += 1
        return 0

    def search(self, *a, **kw):
        self.calls += 1
        return list(self.hits)

    def list_collections(self):
        self.calls += 1
        return set(self.collections)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(
        "def Core():\n    return 1\n\n\ndef caller():\n    return Core()\n")
    (project / "b.py").write_text("def other():\n    return 2\n")
    os.makedirs(cfg.index_root, exist_ok=True)
    entry = Registry(os.path.join(cfg.index_root, "registry.db")).add(
        str(project), name="readproj")
    embedder, store = RecordingEmbedder(), RecordingStore()
    core = Core(cfg)
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer = Indexer(cfg, embedder, store)  # type: ignore[assignment]
    core.indexer.index_project(str(project), entry.slug,
                               core.manifest_for(entry.slug))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    embedder.calls = 0
    store.calls = 0
    yield core, entry, project, embedder, store


def _manifest_snapshot(core: Core, slug: str) -> tuple:
    """A LOGICAL manifest snapshot: what a writer would have to change.

    The raw file bytes are not the contract -- SQLite checkpoints its WAL on
    open, which changes bytes without a logical write. The logical hash
    covers the pass stamp, the revision and every stored row a read could
    have repaired.
    """
    m = core.manifest_for(slug)
    try:
        files = sorted((path, row.content_hash, row.size, row.chunk_count)
                       for path, row in m.all_files().items())
        last = m.get_meta("last_indexed")
        rev = m.get_meta("index_revision")
        symbols = sorted((r.file, r.name, r.start_line)
                         for r in m.all_symbols())
        refs = m.import_rows()
    finally:
        m.close()
    digest = hashlib.sha256(
        repr((files, last, rev, symbols, refs)).encode()).hexdigest()
    return digest, last, rev, files, len(symbols)


READ_INVOCATIONS = [
    ["find-symbol", "Core"],
    ["find-definition", "Core"],
    ["find-references", "Core"],
    ["overview"],
    ["skeleton"],
    ["outline", "a.py"],
]


@pytest.mark.parametrize("args", READ_INVOCATIONS)
def test_a_read_answers_without_writing_or_dialling(rig, args):
    core, entry, project, embedder, store = rig
    before = _manifest_snapshot(core, entry.slug)
    embedder.calls = 0
    store.calls = 0
    result = runner.invoke(app, [*args, "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert _manifest_snapshot(core, entry.slug) == before
    assert embedder.calls == 0, f"{args[0]} dialled the embedder"
    assert store.calls == 0, f"{args[0]} dialled the store"


@pytest.mark.parametrize("args", READ_INVOCATIONS)
def test_a_read_does_not_run_an_index_pass(rig, args):
    core, entry, project, embedder, store = rig
    calls: list = []
    core.run_index = lambda *a, **kw: calls.append(a) or {
        "state": "idle", "result": {}}
    result = runner.invoke(app, [*args, "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert calls == [], f"{args[0]} ran an index pass"


def test_the_staleness_verdict_contacts_no_service(rig):
    core, entry, project, embedder, store = rig
    embedder.calls = 0
    store.calls = 0
    verdict = core.staleness(entry)
    assert verdict.state == "fresh"
    assert embedder.calls == 0 and store.calls == 0


def test_roster_is_closed_and_every_command_classified():
    group = typer_get_command(cli.app)
    names = set(group.commands)
    roster = command_classes.all_commands()
    assert names - roster == set(), f"commands outside the roster: {names - roster}"
    assert roster - names == set(), f"roster names that do not exist: {roster - names}"
    assert len(roster) == 27, sorted(roster)


def test_every_read_command_accepts_refresh_and_the_inert_skip_flag():
    group = typer_get_command(cli.app)
    missing_refresh, missing_skip = [], []
    for name in sorted(command_classes.READ_COMMANDS):
        flags = {o for p in group.commands[name].params
                 for o in getattr(p, "opts", [])}
        if "--refresh" not in flags:
            missing_refresh.append(name)
        if "--skip-stale-check" not in flags:
            missing_skip.append(name)
    assert not missing_refresh, f"reads without --refresh: {missing_refresh}"
    assert not missing_skip, f"reads without --skip-stale-check: {missing_skip}"


def test_fresh_answer_prints_nothing_extra(rig):
    core, entry, project, embedder, store = rig
    result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "Warning: index" not in result.stderr, result.stderr


def test_stale_answer_prints_the_record_line_once(rig):
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 99\n")
    (project / "c.py").write_text("def added():\n    return 3\n")
    result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stderr.splitlines()
             if ln.startswith("Warning: index is stale")]
    assert len(lines) == 1, result.stderr
    line = lines[0]
    assert "2 files changed" in line, line
    assert "1 modified, 1 added" in line, line
    assert "answers describe the index, not the tree" in line
    assert f"Refresh: code-indexer reindex-project {project}" in line
    assert "Warning" not in result.stdout


def test_unknown_answer_says_the_check_did_not_run(rig):
    core, entry, project, embedder, store = rig
    import shutil
    shutil.rmtree(project)
    result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                 str(project)])
    lines = [ln for ln in result.stderr.splitlines()
             if "staleness check did not run" in ln]
    assert len(lines) == 1, result.stderr
    assert "Warning:" in lines[0]


def test_json_answer_carries_the_staleness_block(rig):
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 7\n")
    result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                 str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload["stale_known"], bool)
    assert payload["stale"] is True
    assert payload["stale_reason_count"] == 1
    assert payload["stale_reasons"][0]["kind"] == "modified"
    assert payload["index_revision"] == "1"
    assert payload["last_indexed"].endswith("Z")


def test_refresh_writes_once_and_answers_from_the_result(rig):
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 8\n")
    before_rev = _manifest_snapshot(core, entry.slug)[2]
    result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                 str(project), "--refresh"])
    assert result.exit_code == 0, result.output
    after = _manifest_snapshot(core, entry.slug)
    assert after[2] != before_rev, "--refresh did not write a new revision"
    assert "Warning: index is stale" not in result.stderr


def test_refresh_on_a_held_flock_is_exit_5_with_no_answer(rig):
    core, entry, project, embedder, store = rig
    lock_path = core._lock_path(entry.slug)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover — child process
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(write_fd, b"1")
            time.sleep(10)
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        os.read(read_fd, 1)
        result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                     str(project), "--refresh"])
    finally:
        os.close(read_fd)
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    assert result.exit_code == 5, result.output
    assert result.stdout == "", result.stdout
    assert "LockedError: indexing in progress" in result.stderr
