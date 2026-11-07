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
import pathlib
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
from code_indexer.manifest import Manifest  # noqa: E402
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

    def swap_collection(self, tmp, target):
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
                 index_root=str(tmp_path / "state"),
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
    # Close the writer: the fixture must leave a SETTLED database, or the
    # byte-identity assertion below would measure this connection's deferred
    # checkpoint rather than the read's cost.
    seeding = core.manifest_for(entry.slug)
    try:
        core.indexer.index_project(str(project), entry.slug, seeding)
    finally:
        seeding.close()
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
def test_a_read_leaves_the_manifest_byte_identical(rig, args):
    """The file BYTES are unchanged — the contract round 1 never tested.

    A read opens the manifest read-only: no journal-mode write, no DDL, no
    migration. (The logical snapshot above is the writer's contract; this is
    the reader's: the answer costs the file nothing.) The fixture leaves a
    SETTLED database, so what this compares is the read's cost and not a
    writer's deferred checkpoint.
    """
    core, entry, project, embedder, store = rig
    db = pathlib.Path(core.cfg.index_root) / entry.slug / "manifest.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = runner.invoke(app, [*args, "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("args", READ_INVOCATIONS)
def test_a_read_opens_the_manifest_read_only(rig, args, monkeypatch):
    """The mechanism behind the byte assertion, asserted directly.

    A byte comparison can only notice a writable open that *costs* the file
    something (a checkpoint of a pending WAL, a migration). This pins the
    rule itself: every manifest a read command opens is opened
    ``writable=False``, so there is no write-capable handle on a read path at
    all. It is the check that would have caught the manifest-only staleness
    warning opening the file read-write.
    """
    core, entry, project, embedder, store = rig
    opened: list[tuple[str, bool]] = []
    original = Manifest.__init__

    def spy(self, db_path, **kwargs):
        opened.append((str(db_path), kwargs.get("writable", True)))
        original(self, db_path, **kwargs)

    monkeypatch.setattr(Manifest, "__init__", spy)
    result = runner.invoke(app, [*args, "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert opened, f"{args[0]} opened no manifest"
    writable_opens = [path for path, writable in opened if writable]
    assert not writable_opens, (
        f"{args[0]} opened the manifest write-capable: {writable_opens}")


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
    # The roster count is part of the contract: a new command must be added
    # here deliberately, not slip in with the next feature.
    assert len(roster) == 33, sorted(roster)


# The plan reads answer from a plan file and the live sources, not from an
# index answer, so there is nothing for --refresh to refresh. They accept the
# inert --skip-stale-check like every command and no --refresh.
NO_REFRESH_READS = frozenset({"plan-show", "plan-validate"})


def test_every_read_command_accepts_refresh_and_the_inert_skip_flag():
    group = typer_get_command(cli.app)
    missing_refresh, missing_skip = [], []
    for name in sorted(command_classes.READ_COMMANDS):
        flags = {o for p in group.commands[name].params
                 for o in getattr(p, "opts", [])}
        if "--refresh" not in flags and name not in NO_REFRESH_READS:
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


def test_stale_answer_prints_one_count_line_on_every_read(rig):
    """The banner is a stateless count: same single line, read after read.

    Five reads of one genuinely stale project are five answers -- a CLI
    cannot know they came from one reader -- so every one of them states the
    verdict. Nothing is remembered, and nothing is suppressed: the line is
    short enough to stay visible rather than becoming wallpaper.
    """
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 99\n")
    (project / "c.py").write_text("def added():\n    return 3\n")
    lines: list[str] = []
    for _ in range(5):
        result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                     str(project)])
        assert result.exit_code == 0, result.output
        count_lines = [ln for ln in result.stderr.splitlines()
                       if ln.startswith("Warning: index is stale")]
        assert len(count_lines) == 1, result.stderr
        lines.append(count_lines[0])
        assert "Warning" not in result.stdout
    assert len(set(lines)) == 1, lines
    line = lines[0]
    assert line.startswith("Warning: index is stale: "), line
    assert "1 modified file, 1 added file" in line, line
    assert "since " not in line, line        # no pass timestamp in the banner
    assert "b.py" not in line and "c.py" not in line, line
    assert f"reindex-project {project}" in line, line


def test_the_stale_banner_keeps_no_state_file(rig, tmp_path):
    """Silence is never stored: no marker appears anywhere a later read reads.

    A marker or a recorded timestamp would let a later, unrelated process
    believe it had already been told something it never saw. The ONLY state
    a read leaves behind is the answer itself.
    """
    core, entry, project, embedder, store = rig
    (project / "c.py").write_text("def added():\n    return 3\n")
    index_root = pathlib.Path(core.cfg.index_root)

    def tracked() -> list[str]:
        return sorted(str(p.relative_to(index_root)) for p in index_root.rglob("*")
                      if not p.name.endswith((".db", "-wal", "-shm")))

    before = tracked()
    for _ in range(3):
        result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                     str(project)])
        assert result.exit_code == 0, result.output
    assert tracked() == before, [p for p in tracked() if p not in before]
    assert not [p for p in project.rglob("*")
                if "warn" in p.name or "stale" in p.name], "a marker was written"


def test_json_carries_every_reason_on_every_read(rig):
    """The banner's brevity never reaches the machine-readable answer."""
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 99\n")
    (project / "c.py").write_text("def added():\n    return 3\n")
    reasons = [{"kind": "modified", "file": "b.py"},
               {"kind": "added", "file": "c.py"}]
    seen = []
    for _ in range(5):
        result = runner.invoke(app, ["find-symbol", "Core", "--project",
                                     str(project), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["stale"] is True
        assert payload["stale_known"] is True
        assert payload["stale_reasons"] == reasons, payload["stale_reasons"]
        assert payload["stale_reason_count"] == 2
        assert payload["index_revision"] == "1"
        assert payload["last_indexed"].endswith("Z")
        seen.append(payload["stale_reasons"])
    assert seen == [reasons] * 5, seen


def test_a_stale_and_skipped_project_reports_both_states(rig):
    """A skip is not a stale reason, and a stale read does not hide it."""
    core, entry, project, embedder, store = rig
    (project / "notes.md").write_text(
        "token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "abcdefghijklmnopqrstu\n")
    (project / "c.py").write_text("def added():\n    return 3\n")
    result = runner.invoke(app, ["overview", "--project", str(project)])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stderr.splitlines()
             if ln.startswith("Warning: index is stale")]
    assert len(lines) == 1, result.stderr
    assert "1 added file" in lines[0], lines[0]
    assert "1 file skipped (sensitive content)" in lines[0], lines[0]
    payload = json.loads(runner.invoke(
        app, ["overview", "--project", str(project), "--json"]).stdout)
    assert payload["stale_reasons"] == [{"kind": "added", "file": "c.py"}]
    assert payload["withheld_files"] == [
        {"file": "notes.md", "reason": "sensitive content"}]
    assert payload["withheld_count"] == 1
    # index-status states the skip on its own line, so its warning omits it:
    # the fact appears exactly once in a command's output, never twice.
    status = runner.invoke(app, ["index-status", "--project", str(project)])
    assert status.exit_code == 0, status.output
    assert (status.stdout + status.stderr).count(
        "skipped (sensitive content)") == 1, status.stdout + status.stderr


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


def test_a_query_read_computes_one_staleness_verdict_per_project(rig):
    """A stale answer is built from the ONE walk that produced its block.

    The cost contract: a read may not walk a project tree twice to say the
    same thing. The verdict count is the assertion; timing is not.
    """
    core, entry, project, embedder, store = rig
    (project / "b.py").write_text("def other():\n    return 99\n")
    seen: list[str] = []
    real = core.staleness

    def counting(target):
        seen.append(target.path)
        return real(target)

    core.staleness = counting  # type: ignore[method-assign]

    scoped = runner.invoke(app, ["semantic-search", "Core",
                                 "--project", str(project)])
    assert scoped.exit_code == 0, scoped.output
    assert seen == [entry.path], f"scoped read walked {seen}"
    assert "Warning: index is stale" in scoped.stderr, scoped.stderr

    seen.clear()
    fanned = runner.invoke(app, ["semantic-search", "Core", "--all-projects"])
    assert fanned.exit_code == 0, fanned.output
    assert seen == [entry.path], f"fan-out read walked {seen}"
    assert "Warning: index is stale" in fanned.stderr, fanned.stderr
