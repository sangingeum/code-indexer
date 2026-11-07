"""The exit-code taxonomy (ADR-0003 section 5), asserted as behaviour.

A total backend failure and an empty result are different answers: the first
exits 3 with `ErrorType: ...` on stderr and NOTHING on stdout; the second
exits 0 with its answer on stdout. Codes 4 (rebuild) and 5 (lock) name the
caller's next move, and 2 stays the usage class.
"""

from __future__ import annotations

import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.errors import (EXIT_BACKEND, EXIT_FAIL, EXIT_LOCK,  # noqa: E402
                                 EXIT_OK, EXIT_REINDEX, EXIT_USAGE,
                                 ArgumentError, BackendError, ConfigError,
                                 InternalError, LockedError, NotFoundError,
                                 RebuildRequiredError, classify)
from code_indexer.indexer import Indexer  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


class StubEmbedder:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing

    def dimension(self) -> int:
        return 4

    def dimension_once(self) -> int:
        return 4

    def embed(self, texts):
        if self.failing:
            raise RuntimeError("connection refused (ollama)")
        return [[0.1] * 4 for _ in texts]

    def embed_query_once(self, text):
        if self.failing:
            raise RuntimeError("connection refused (ollama)")
        return [0.1] * 4

    def query_instruction_text(self, query: str) -> str:
        return query

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def close(self) -> None:
        pass


class StubStore:
    def __init__(self, hits=None) -> None:
        self.hits = hits or []

    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        return len(points)

    def purge_file_points(self, *a, **kw):
        return 0

    def count_points(self, name):
        return 0

    def search(self, *a, **kw):
        return list(self.hits)

    def list_collections(self):
        return set()


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def main():\n    return 0\n")
    os.makedirs(cfg.index_root, exist_ok=True)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="exitproj")
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer = Indexer(cfg, core.embedder, core.store)  # type: ignore[assignment]
    core.indexer.index_project(str(project), entry.slug,
                               core.manifest_for(entry.slug))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project, reg
    reg.close()


def test_the_taxonomy_codes():
    assert (EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_BACKEND, EXIT_REINDEX,
            EXIT_LOCK) == (0, 1, 2, 3, 4, 5)
    assert ArgumentError("x").exit_code == 2
    assert BackendError("x").exit_code == 3
    assert RebuildRequiredError("x").exit_code == 4
    assert LockedError("x").exit_code == 5
    assert ConfigError("x").exit_code == 1
    assert NotFoundError("x").exit_code == 1
    assert InternalError("x").exit_code == 1


def test_classification_of_new_and_old_shapes():
    assert classify(ValueError("error: connection refused (ollama)")) \
        .error_type == "BackendError"
    assert classify(ValueError("error: project not registered: /p")) \
        .error_type == "NotFoundError"
    from code_indexer.fingerprint import ConfigError as LegacyConfigError
    assert classify(LegacyConfigError(
        "index for /p does not match the current configuration; run: "
        "code-indexer reindex-project /p")).exit_code == 4
    assert classify(LegacyConfigError(
        "model 'm' not found on Ollama (x); run: ollama pull m")).exit_code == 3


def test_total_backend_failure_is_code_3_stderr_only(rig, monkeypatch):
    core, entry, project, reg = rig
    core.embedder = StubEmbedder(failing=True)  # type: ignore[assignment]
    result = runner.invoke(app, ["semantic-search", "anything", "--project",
                                 str(project)])
    assert result.exit_code == 3, result.output
    assert result.stdout == "", result.stdout
    assert result.stderr.count("BackendError:") == 1, result.stderr
    assert "Traceback" not in result.stderr


def test_empty_result_is_code_0_on_stdout(rig):
    core, entry, project, reg = rig
    result = runner.invoke(app, ["semantic-search", "anything", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    # The empty answer still exits 0 and still answers on stdout; it now names
    # the cause, so the bare "no results" is the lead of the sentence.
    assert result.stdout.splitlines()[0].startswith("no results:"), \
        result.stdout
    assert "BackendError" not in result.stderr


def test_needs_reindex_is_code_4(rig):
    core, entry, project, reg = rig
    from code_indexer.embed_text import EMBED_FORMAT
    from code_indexer.graph_extract import EXTRACTION_VERSION
    from code_indexer.ts_chunker import CHUNKER_VERSION
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("embed_model", "older-model")
        m.set_meta("embed_dim", "4")
        m.set_meta("embed_text_version", EMBED_FORMAT)
        m.set_meta("chunker_version", str(CHUNKER_VERSION))
        m.set_meta("extraction_version", str(EXTRACTION_VERSION))
    finally:
        m.close()
    result = runner.invoke(app, ["semantic-search", "anything", "--project",
                                 str(project)])
    assert result.exit_code == 4, result.output
    assert result.stdout == "", result.stdout
    assert result.stderr.count("RebuildRequiredError:") == 1, result.stderr


def test_held_flock_is_code_5_with_no_answer(rig):
    core, entry, project, reg = rig
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
        result = runner.invoke(app, ["semantic-search", "q", "--project",
                                     str(project), "--refresh"])
    finally:
        os.close(read_fd)
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    assert result.exit_code == 5, result.output
    assert result.stdout == ""
    assert "LockedError: indexing in progress" in result.stderr


def test_usage_error_is_code_2(rig):
    core, entry, project, reg = rig
    result = runner.invoke(app, ["semantic-search", "q", "--project",
                                 str(project), "--all-projects"])
    assert result.exit_code == 2, result.output
    assert "ArgumentError:" in result.stderr, result.stderr


def test_json_failure_carries_the_error_type(rig):
    core, entry, project, reg = rig
    result = runner.invoke(app, ["find-definition", "Core", "--project",
                                 "/nonexistent/path", "--json"])
    assert result.exit_code == 1, result.output
    import json
    payload = json.loads(result.stdout)
    assert payload["ErrorType"] == "NotFoundError"
    assert payload["schema"] == 1
