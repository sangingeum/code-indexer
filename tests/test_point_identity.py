"""Point identity: the point at a row's id carries that row, and only it.

Point ids are positional -- ``point_id(project, file, chunk_index)`` -- while
reuse used to be recorded as a bare ``(file, chunk_hash)`` flag meaning "this
text has a vector somewhere". An edit that shifts a file's chunk sequence
leaves every unchanged chunk's ``chunk_index`` different, so the unchanged
chunks were not written and the point at the NEW index kept the vector and
payload of whatever chunk used to sit there. Measured on the live index:
13.3% of rows disagreed with their point (`core.py`: 3 of 265 rows agreed).

The property asserted here is the one that was missing: after a pass, for
every chunk row, the point at that row's id carries the row's file,
chunk_index, chunk_hash and range -- and the vector is the one this row's
own embedding input produces, so the assertion cannot be satisfied by a
carried vector belonging to another chunk.

The repair is the same property over a collection that is already wrong: the
stored vector is found by content and written at the id the row names, with
no embedding, and a second run changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli_mod  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.embed_text import embed_text  # noqa: E402
from code_indexer.embedder import Embedder  # noqa: E402
from code_indexer.indexer import Indexer, _chunk_from_row  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.store import point_id  # noqa: E402

PROJECT = "/projects/identity"
# A port nothing listens on: an embedding service that is down, not slow.
DEAD_EMBEDDING_HOST = "http://127.0.0.1:9"
RUNNER = CliRunner()


class _Embedder:
    """Deterministic vectors: distinct per embedding input, captured for count."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def dimension(self) -> int:
        return 8

    @staticmethod
    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[:8]]

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [self.vector(t) for t in texts]

    def embed_with_errors(self, texts: list[str]):
        return self.embed(texts), []

    def close(self) -> None:
        pass


class _Store:
    """An in-memory collection with the two reads/writes a pass needs."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.writes = 0

    def collection_exists(self, name: str) -> bool:
        return True

    def create_collection(self, name: str, dim: int) -> None:
        pass

    def upsert_points(self, name: str, points: list) -> int:
        for p in points:
            self.points[p["id"]] = {
                "id": p["id"],
                "vector": list(p["vector"]),
                "payload": dict(p["payload"]),
            }
        self.writes += len(points)
        return len(points)

    def points_for_file(self, name: str, project: str, file: str) -> list[dict]:
        return [dict(p) for p in self.points.values()
                if p["payload"].get("file") == file]

    def purge_file_points(self, name: str, project: str, file: str,
                          min_chunk_index: int | None = None) -> int:
        doomed = [
            p["id"] for p in self.points.values()
            if p["payload"].get("file") == file
            and (min_chunk_index is None
                 or p["payload"].get("chunk_index", -1) >= min_chunk_index)
        ]
        for pid in doomed:
            del self.points[pid]
        return len(doomed)

    def count_points(self, name: str) -> int:
        return len(self.points)

    def swap_collection(self, collection: str, alias: str) -> None:
        pass

    def search(self, *args, **kwargs) -> list:
        return []


ORIGINAL = (
    "def alpha():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def beta():\n"
    "    return 2\n"
)

INSERTED = (
    "def added():\n"
    "    return 0\n"
    "\n"
    "\n"
) + ORIGINAL


def _cfg(tmp_path) -> Config:
    return Config(
        ollama_url="", qdrant_url="", embed_model="stub",
        index_root=str(tmp_path / "root"), embed_batch=48,
        upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
        watch_sweep_interval=300,
    )


def _indexer(tmp_path) -> tuple[Indexer, _Embedder, _Store]:
    embedder = _Embedder()
    store = _Store()
    indexer = Indexer(_cfg(tmp_path), embedder, store)  # type: ignore[arg-type]
    return indexer, embedder, store


def _assert_rows_own_their_points(manifest: Manifest, store: _Store,
                                project: str, vector_of=None) -> None:
    """Every row's point carries that row, and no other point stands in.

    ``vector_of`` is the embedder's own vector for an embedding input; the
    stub embedder's is the default, and a test that indexed through a real
    (loopback) service passes that service's vector.
    """
    vector_of = vector_of or _Embedder.vector
    rows = manifest.all_chunks()
    assert rows, "the manifest must hold chunk rows for this property"
    per_file: dict[str, int] = {}
    for row in rows:
        per_file[row.file] = per_file.get(row.file, 0) + 1
        pid = point_id(project, row.file, row.chunk_index)
        point = store.points.get(pid)
        assert point is not None, (
            f"no point at {row.file} chunk {row.chunk_index}")
        payload = point["payload"]
        assert payload["file"] == row.file
        assert payload["chunk_index"] == row.chunk_index, (
            f"{row.file} chunk {row.chunk_index} is served by payload of chunk "
            f"{payload['chunk_index']}")
        assert payload["chunk_hash"] == row.chunk_hash
        assert payload["start_line"] == row.start_line
        assert payload["end_line"] == row.end_line
        expected = vector_of(
            embed_text(row.file, _chunk_from_row(row)))
        assert point["vector"] == expected, (
            f"{row.file} chunk {row.chunk_index} holds another chunk's vector")
    for file, count in per_file.items():
        held = [p for p in store.points.values()
                if p["payload"].get("file") == file]
        assert len(held) == count, (
            f"{file} holds {len(held)} points for {count} rows")


def test_an_incremental_pass_places_every_row_at_its_own_point(tmp_path):
    """A shifted chunk sequence keeps each unchanged chunk's own point."""
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    indexer, embedder, store = _indexer(tmp_path)
    manifest = Manifest(str(tmp_path / "manifest.db"))
    try:
        first = indexer.index_project(str(tmp_path), "slugx", manifest)
        assert first.chunks_embedded > 0
        _assert_rows_own_their_points(manifest, store, str(tmp_path))

        # A declaration above alpha: every later chunk's index and start line
        # move, while its text (and so its chunk_hash) is unchanged.
        src.write_text(INSERTED)
        embedder.texts.clear()
        store.writes = 0
        second = indexer.index_project(str(tmp_path), "slugx", manifest)
        assert second.chunks_reused > 0, "the moved chunks must be reused"
        assert second.chunks_embedded == len(embedder.texts) > 0
        _assert_rows_own_their_points(manifest, store, str(tmp_path))

        # A file that shrinks: its old tail must not outlive its rows.

        src.write_text(INSERTED[: INSERTED.index("def beta")])
        indexer.index_project(str(tmp_path), "slugx", manifest)
        _assert_rows_own_their_points(manifest, store, str(tmp_path))
    finally:
        manifest.close()


def _core(tmp_path) -> tuple[Core, object]:
    cfg = _cfg(tmp_path)
    core = Core(cfg)
    embedder, store = _Embedder(), _Store()
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    registry = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = registry.add(str(tmp_path), name="identityproj")
    core.run_index(entry.slug, str(tmp_path))
    return core, entry


def _permute(store: _Store, file: str) -> None:
    """Make each point hold the next point's payload and vector, for ``file``.

    That is the shape the defect produced: the payload (and the vector with
    it) belongs to another row of the same file, so the point at index i
    describes some other chunk -- while every row's vector still exists
    somewhere in the collection, which is what makes it repairable.
    """
    held = sorted((p for p in store.points.values()
                   if p["payload"].get("file") == file),
                  key=lambda p: p["payload"]["chunk_index"])
    for i, point in enumerate(held):
        donor = held[(i + 1) % len(held)]
        store.points[point["id"]] = {
            "id": point["id"],
            "vector": list(donor["vector"]),
            "payload": dict(donor["payload"]),
        }


def test_the_repair_readdresses_a_permuted_collection_and_is_idempotent(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    core, entry = _core(tmp_path)
    store = core.store
    manifest = core.manifest_for(entry.slug)
    try:
        _assert_rows_own_their_points(manifest, store, str(tmp_path))
        _permute(store, "mod.py")
        with pytest.raises(AssertionError):
            _assert_rows_own_their_points(manifest, store, str(tmp_path))

        embedded_before = len(core.embedder.texts)
        first = core.repair_points(entry.slug, str(tmp_path))
        assert first["state"] == "idle", first
        assert first["result"]["chunks_embedded"] == 0
        assert first["result"]["chunks_unrepairable"] == 0
        assert len(core.embedder.texts) == embedded_before, (
            "the repair must not embed anything")
        _assert_rows_own_their_points(manifest, store, str(tmp_path))

        before = {pid: (list(p["vector"]), dict(p["payload"]))
                  for pid, p in store.points.items()}
        store.writes = 0
        second = core.repair_points(entry.slug, str(tmp_path))
        assert second["state"] == "idle", second
        assert store.writes == 0, "a second repair must change nothing"
        assert {pid: (list(p["vector"]), dict(p["payload"]))
                for pid, p in store.points.items()} == before
    finally:
        manifest.close()


# A file whose two declarations are byte-identical: the same chunk_hash AND
# the same symbol, at two different chunk_index values. The live index holds
# 112 such duplicate groups (237 rows), and the repair used to re-address one
# row of every pair on every run -- most of the 33 files it reported as
# committed on a fully agreeing index.
DUPLICATE = (
    "def repeated():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def repeated():\n"
    "    return 1\n"
)


def test_duplicate_chunk_rows_each_hold_their_own_point(tmp_path):
    """Two rows with one embedding input still own two points.

    Content addressing gives both rows the same vector; identity does not.
    The point lookup therefore consumes a point per row and prefers the one
    already at the row's own id, so a repeated declaration is not re-written
    on every repair.
    """
    src = tmp_path / "dup.py"
    src.write_text(DUPLICATE)
    core, entry = _core(tmp_path)
    store = core.store
    manifest = core.manifest_for(entry.slug)
    try:
        rows = manifest.all_chunks()
        keys = [(r.chunk_hash, r.symbol) for r in rows]
        assert len(rows) == 2, [r.chunk_index for r in rows]
        assert keys[0] == keys[1], keys
        _assert_rows_own_their_points(manifest, store, str(tmp_path))

        store.writes = 0
        revision = manifest.get_meta("index_revision")
        first = core.repair_points(entry.slug, str(tmp_path))
        assert first["state"] == "idle", first
        assert first["result"]["chunks_reused"] == 2, first["result"]
        assert store.writes == 0, (
            "a repeated declaration was re-addressed by an agreeing repair")
        assert first["result"]["files_committed"] == 0, first["result"]
        assert manifest.get_meta("index_revision") == revision
        _assert_rows_own_their_points(manifest, store, str(tmp_path))

        second = core.repair_points(entry.slug, str(tmp_path))
        assert second["state"] == "idle", second
        assert store.writes == 0, "the second repair must write nothing either"
        assert manifest.get_meta("index_revision") == revision
    finally:
        manifest.close()


def test_the_repair_refuses_a_row_whose_vector_is_gone(tmp_path):
    """No vector anywhere means no point written -- counted, never guessed."""
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    core, entry = _core(tmp_path)
    store = core.store
    manifest = core.manifest_for(entry.slug)
    try:
        rows = manifest.all_chunks()
        victim = rows[0]
        del store.points[point_id(str(tmp_path), victim.file,
                                 victim.chunk_index)]
        embedded_before = len(core.embedder.texts)
        result = core.repair_points(entry.slug, str(tmp_path))
        assert result["state"] == "idle", result
        assert result["result"]["chunks_unrepairable"] == 1
        assert len(core.embedder.texts) == embedded_before
        assert point_id(str(tmp_path), victim.file,
                        victim.chunk_index) not in store.points
    finally:
        manifest.close()


# ---------------------------------------------------------------------------
# the repair needs no embedding service
# ---------------------------------------------------------------------------

class _EmbeddingLedger:
    """A loopback embedding service that records every request it answers.

    The repair carries vectors that are already stored, so it has no reason to
    dial an embedder at all. This ledger makes \"dialled nothing\" observable:
    every POST lands in ``requests``, the dimension probe included.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.requests: list[dict] = []
        ledger = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw)
                except ValueError:
                    payload = {}
                ledger.requests.append({"path": self.path,
                                        "payload": payload})
                texts = payload.get("input") or []
                if isinstance(texts, str):
                    texts = [texts]
                body = json.dumps(
                    {"embeddings": [[0.25] * ledger.dim for _ in texts]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    def __enter__(self) -> _EmbeddingLedger:
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def reset(self) -> None:
        self.requests.clear()


def _service_core(tmp_path, host: str) -> tuple[Core, object]:
    """A core whose embedder is the REAL one, pointed at ``host``."""
    cfg = Config(ollama_url=host, qdrant_url="", embed_model="ledgermodel",
                 index_root=str(tmp_path / "root"), embed_batch=48,
                 upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    core = Core(cfg)
    embedder = Embedder(host=host, model="ledgermodel")
    store = _Store()
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    registry = Registry(os.path.join(cfg.index_root, "registry.db"))
    return core, registry.add(str(tmp_path), name="ledgerproj")


def test_the_repair_dials_no_embedding_service(tmp_path):
    """A repair issues no request at all -- not even the dimension probe.

    The dimension it needs is a fact the index already records, so a repair
    that re-addresses stored vectors never asks the service for one. The
    second half proves it the other way round: with the embedding host
    pointed at a dead endpoint the SAME repair still succeeds and reports the
    same counts.
    """
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    with _EmbeddingLedger() as ledger:
        core, entry = _service_core(tmp_path, ledger.url)
        core.run_index(entry.slug, str(tmp_path), vectors="on")
        assert ledger.requests, "the indexing pass must use the service"
        store = core.store
        manifest = core.manifest_for(entry.slug)
        try:
            # A fresh client is what a fresh CLI process has: no
            # remembered dimension, so a repair that wanted a probe would
            # have to ask for one now.
            core.embedder = Embedder(  # type: ignore[assignment]
                host=ledger.url, model="ledgermodel")
            core.indexer.embedder = core.embedder  # type: ignore[assignment]
            _permute(store, "mod.py")
            ledger.reset()
            repaired = core.repair_points(entry.slug, str(tmp_path))
            assert ledger.requests == [], (
                "the repair must not dial the embedding service: "
                f"{ledger.requests}")
            assert repaired["state"] == "idle", repaired
            assert repaired["result"]["chunks_unrepairable"] == 0
            counts = {k: v for k, v in repaired["result"].items()
                      if k != "duration_s"}
            _assert_rows_own_their_points(manifest, store, str(tmp_path),
                                   vector_of=lambda _text: [0.25] * 8)

            # Same work, embedding host dead: the repair still succeeds.
            _permute(store, "mod.py")
            core.embedder = Embedder(  # type: ignore[assignment]
                host=DEAD_EMBEDDING_HOST, model="ledgermodel")
            core.indexer.embedder = core.embedder  # type: ignore[assignment]
            offline = core.repair_points(entry.slug, str(tmp_path))
            assert offline["state"] == "idle", offline
            assert offline["result"]["chunks_unrepairable"] == 0
            assert {k: v for k, v in offline["result"].items()
                    if k != "duration_s"} == counts
            _assert_rows_own_their_points(manifest, store, str(tmp_path),
                                   vector_of=lambda _text: [0.25] * 8)
        finally:
            manifest.close()


# ---------------------------------------------------------------------------
# a refusal keeps the class that names the caller's next move
# ---------------------------------------------------------------------------

def _cli_rig(tmp_path, monkeypatch, *, collection_present: bool = True):
    """A core behind the CLI, with a real manifest and a stubbed collection."""
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    cfg = _cfg(tmp_path)
    core = Core(cfg)
    embedder = _Embedder()
    store = _Store()
    if not collection_present:
        store.collection_exists = lambda name: False  # type: ignore[method-assign]
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    registry = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = registry.add(str(tmp_path), name="refusalproj")
    core.run_index(entry.slug, str(tmp_path), vectors="on")
    monkeypatch.setattr(cli_mod, "_get_core", lambda skip: core)
    return core, entry, registry


def test_rows_not_current_is_a_rebuild_refusal_exit_four(tmp_path, monkeypatch):
    """The rebuild the repair demands is exit 4, not a generic failure (1)."""
    core, entry, registry = _cli_rig(tmp_path, monkeypatch)
    manifest = core.manifest_for(entry.slug)
    try:
        manifest.set_meta("extraction_version", "0")
    finally:
        manifest.close()
    result = RUNNER.invoke(app, ["reindex-project", str(tmp_path),
                                 "--repair-points"])
    registry.close()
    assert result.exit_code == 4, (result.exit_code, result.output)
    assert result.stdout == "", result.stdout
    assert result.stderr.count("RebuildRequiredError:") == 1, result.stderr
    assert "not current" in result.stderr, result.stderr


def test_a_missing_collection_is_a_rebuild_refusal_exit_four(tmp_path,
                                                             monkeypatch):
    """A collection that is gone is a rebuild too: exit 4, one stderr line."""
    core, entry, registry = _cli_rig(tmp_path, monkeypatch,
                                     collection_present=False)
    result = RUNNER.invoke(app, ["reindex-project", str(tmp_path),
                                 "--repair-points"])
    registry.close()
    assert result.exit_code == 4, (result.exit_code, result.output)
    assert result.stdout == "", result.stdout
    assert result.stderr.count("RebuildRequiredError:") == 1, result.stderr
    assert "collection is gone" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# a repair that writes no point is not a change
# ---------------------------------------------------------------------------

def test_a_repair_that_writes_no_point_does_not_advance_the_revision(tmp_path):
    """Nothing written means nothing recorded: no revision, no as-of stamp.

    A revision that moves on every pass makes a command that changed nothing
    look like a change to every later audit, so a no-op repair writes no
    manifest at all.
    """
    src = tmp_path / "mod.py"
    src.write_text(ORIGINAL)
    core, entry = _core(tmp_path)
    store = core.store
    manifest = core.manifest_for(entry.slug)
    try:
        before = manifest.get_meta("index_revision")
        stamped = manifest.get_meta("last_indexed")
        store.writes = 0
        result = core.repair_points(entry.slug, str(tmp_path))
        assert result["state"] == "idle", result
        assert store.writes == 0, "this repair has nothing to write"
        assert manifest.get_meta("index_revision") == before, (
            "a no-op repair advanced index_revision")
        assert manifest.get_meta("last_indexed") == stamped
    finally:
        manifest.close()
