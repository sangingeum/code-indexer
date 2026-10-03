"""Doctor health-check tests (robustness work item).

Every failure mode exercised with fakes; no network call besides the
configured URLs (the doctor itself is driven with stub ollama/qdrant clients
here).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.doctor import (Check, _versions_compatible,  # noqa: E402
                                 any_fail, check_fingerprints,
                                 check_index_root, check_locks,
                                 check_ollama, check_qdrant, check_runtime,
                                 check_sqlite, run_doctor)
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------

class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class StubOllama:
    def __init__(self, models=("stub",), error=None):
        self._models = list(models)
        self._error = error

    def list(self):
        if self._error is not None:
            raise self._error
        return {"models": [{"model": m} for m in self._models]}

    def close(self):
        pass


class StubStore:
    """Qdrant stand-in: collections, aliases, dims, version all scriptable."""

    def __init__(self, collections=(), aliases=(), dims=None,
                 version="1.19.1", error=None):
        self._collections = {c: (dims.get(c, 4) if dims else 4)
                             for c in collections}
        self._aliases = dict(aliases)
        self._version = version
        self._error = error
        # The doctor reads store.client.*; expose the scripted surface there.
        self.client = types.SimpleNamespace(
            get_collections=self._get_collections,
            get_aliases=self._get_aliases,
            info=self._info,
            get_collection=self._get_collection)

    def _get_collections(self):
        return types.SimpleNamespace(collections=[
            types.SimpleNamespace(name=n) for n in self._collections])

    def _get_aliases(self):
        return types.SimpleNamespace(aliases=[
            types.SimpleNamespace(alias_name=a, collection_name=t)
            for a, t in self._aliases.items()])

    def _info(self):
        if self._error is not None:
            raise self._error
        return types.SimpleNamespace(version=self._version)

    def _get_collection(self, name):
        if name not in self._collections:
            raise ValueError(f"no collection {name}")
        dim = self._collections[name]
        return types.SimpleNamespace(config=types.SimpleNamespace(
            params=types.SimpleNamespace(
                vectors=types.SimpleNamespace(size=dim))))


def _cfg(tmp: str) -> Config:
    return Config(ollama_url="http://stub", qdrant_url="http://stub",
                  embed_model="stub", index_root=str(tmp), stale_ttl=60,
                  embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                  watch_debounce=3, watch_sweep_interval=300)


@pytest.fixture()
def env(tmp_path):
    cfg = _cfg(str(tmp_path / "root"))
    os.makedirs(cfg.index_root, exist_ok=True)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    yield cfg, reg
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------

def test_runtime_ok_and_uv_missing(env):
    checks = check_runtime("3.12.1", uv_on_path=True)
    assert all(c.status == "ok" for c in checks)
    checks = check_runtime("3.12.1", uv_on_path=False)
    names = _by_name(checks)
    assert names["uv"].status == "warn"  # warn does not fail
    assert not any_fail(checks)


def test_runtime_old_python_fails():
    checks = check_runtime("3.10.4", uv_on_path=True)
    names = _by_name(checks)
    assert names["python"].status == "fail"


# ---------------------------------------------------------------------------
# ollama
# ---------------------------------------------------------------------------

def test_ollama_happy_path(env):
    cfg, reg = env
    checks, dim = check_ollama(cfg, StubEmbedder(), StubOllama(models=("stub",)))
    names = _by_name(checks)
    assert names["ollama-reachable"].status == "ok"
    assert names["ollama-model"].status == "ok"
    assert names["ollama-dim"].status == "ok"
    assert dim == 4


def test_ollama_unreachable_fails(env):
    cfg, reg = env
    checks, dim = check_ollama(cfg, StubEmbedder(),
                               StubOllama(error=ConnectionError("refused")))
    assert _by_name(checks)["ollama-reachable"].status == "fail"
    assert dim is None


def test_ollama_model_missing_fails(env):
    cfg, reg = env
    checks, _ = check_ollama(cfg, StubEmbedder(), StubOllama(models=("other",)))
    missing = _by_name(checks)["ollama-model"]
    assert missing.status == "fail"
    assert "ollama pull stub" in missing.detail


# ---------------------------------------------------------------------------
# qdrant
# ---------------------------------------------------------------------------

def test_qdrant_happy_path(env):
    cfg, reg = env
    entry = reg.add(str(tempfile.mkdtemp()), name="proj")
    checks = check_qdrant(cfg, StubStore(collections=[f"idx_{entry.slug}"]),
                          reg, probe_dim=4)
    names = _by_name(checks)
    assert names["qdrant-reachable"].status == "ok"
    assert names["qdrant-missing"].status == "ok"
    assert names["qdrant-orphans"].status == "ok"
    assert names["qdrant-dims"].status == "ok"


def test_qdrant_unreachable_fails(env):
    cfg, reg = env
    checks = check_qdrant(cfg, StubStore(error=ConnectionError("down")), reg)
    assert _by_name(checks)["qdrant-reachable"].status == "fail"


def test_qdrant_missing_collection_fails(env):
    cfg, reg = env
    entry = reg.add(str(tempfile.mkdtemp()), name="proj")
    checks = check_qdrant(cfg, StubStore(collections=[]), reg, probe_dim=4)
    missing = _by_name(checks)["qdrant-missing"]
    assert missing.status == "fail"
    assert f"idx_{entry.slug}" in missing.detail
    assert "reindex-project" in missing.detail


def test_qdrant_orphans_warn(env):
    cfg, reg = env
    checks = check_qdrant(cfg, StubStore(collections=["idx_leftover"]), reg)
    assert _by_name(checks)["qdrant-orphans"].status == "warn"
    assert not any_fail(checks)  # warn does not fail


def test_qdrant_orphan_alias_target_protected(env):
    """Post-swap rebuild: idx_<slug> is an alias to idx_<slug>__new; the
    physical __new collection is the alias target and must not count as an
    orphan."""
    cfg, reg = env
    entry = reg.add(str(tempfile.mkdtemp()), name="proj")
    slug = entry.slug
    checks = check_qdrant(
        cfg,
        StubStore(collections=[f"idx_{slug}__new"],
                  aliases={f"idx_{slug}": f"idx_{slug}__new"}),
        reg, probe_dim=4)
    names = _by_name(checks)
    assert names["qdrant-missing"].status == "ok"  # alias counts as present
    assert names["qdrant-orphans"].status == "ok"


def test_qdrant_dim_mismatch_fails(env):
    cfg, reg = env
    entry = reg.add(str(tempfile.mkdtemp()), name="proj")
    checks = check_qdrant(
        cfg, StubStore(collections=[f"idx_{entry.slug}"],
                       dims={f"idx_{entry.slug}": 4096}),
        reg, probe_dim=4)
    assert _by_name(checks)["qdrant-dims"].status == "fail"


def test_qdrant_client_server_compat_warn():
    # The audited mismatch: client 1.14.3 vs server 1.19.1.
    assert _versions_compatible("1.14.3", "1.19.1") is False
    assert _versions_compatible("1.14.3", "1.15.0") is True   # delta 1
    assert _versions_compatible("1.15.0", "1.14.3") is True   # either side
    assert _versions_compatible("2.0.0", "1.19.1") is False   # major
    assert _versions_compatible("garbage", "1.19.1") is True  # unverifiable


# ---------------------------------------------------------------------------
# index root / sqlite
# ---------------------------------------------------------------------------

def test_index_root_writable_and_disk(env):
    cfg, reg = env
    checks = check_index_root(cfg)
    names = _by_name(checks)
    assert names["index-root"].status == "ok"
    assert names["disk-free"].status in ("ok", "warn")


def test_index_root_unwritable_fails(env):
    cfg, reg = env
    cfg.index_root = "/proc/definitely-not-writable/here"
    checks = check_index_root(cfg)
    assert _by_name(checks)["index-root"].status == "fail"


def test_sqlite_integrity_happy_and_corrupt(env):
    cfg, reg = env
    import sqlite3
    reg_path = os.path.join(cfg.index_root, "registry.db")
    checks = check_sqlite(cfg)
    assert all(c.status == "ok" for c in checks)

    corrupt = os.path.join(cfg.index_root, "corrupt", "manifest.db")
    os.makedirs(os.path.dirname(corrupt), exist_ok=True)
    with open(corrupt, "wb") as f:
        f.write(b"not a database at all")
    checks = check_sqlite(cfg)
    bad = [c for c in checks if c.status == "fail"]
    assert bad and "corrupt" in bad[0].name


# ---------------------------------------------------------------------------
# locks / watch pid
# ---------------------------------------------------------------------------

def test_watch_pidfile_liveness(env):
    cfg, reg = env
    pidfile = os.path.join(cfg.index_root, "watch.pid")
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))  # our own pid is definitely alive
    assert _by_name(check_locks(cfg))["watch"].status == "ok"

    with open(pidfile, "w") as f:
        f.write("999999999")  # not a live pid
    stale = _by_name(check_locks(cfg))["watch"]
    assert stale.status == "warn"
    assert "unwatch" in stale.detail

    os.remove(pidfile)
    assert _by_name(check_locks(cfg))["watch"].status == "ok"


def test_lock_held_reported_ok(env):
    cfg, reg = env
    import fcntl
    lock_path = os.path.join(cfg.index_root, "someproj.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # hold it
        checks = check_locks(cfg)
        locks = _by_name(checks)["locks"]
        assert locks.status == "ok"
        assert "someproj.lock" in locks.detail
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# fingerprints (CI-02 integration)
# ---------------------------------------------------------------------------

def test_fingerprint_mismatch_warns(env):
    cfg, reg = env
    entry = reg.add(str(tempfile.mkdtemp()), name="proj")
    import sqlite3
    mdir = os.path.join(cfg.index_root, entry.slug, "manifest.db")
    os.makedirs(os.path.dirname(mdir), exist_ok=True)
    conn = sqlite3.connect(mdir)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO meta VALUES ('schema_version','4');"
        "INSERT INTO meta VALUES ('embed_model','old-model');"
        "INSERT INTO meta VALUES ('embed_dim','4');"
        "INSERT INTO meta VALUES ('embed_text_version','contextual-header-v1');"
        "INSERT INTO meta VALUES ('chunker_version','1');")
    conn.commit()
    conn.close()
    checks = check_fingerprints(cfg, reg, StubEmbedder())
    fp = _by_name(checks)["fingerprint"]
    assert fp.status == "warn"
    assert "old-model" in fp.detail and "reindex-project" in fp.detail


def test_fingerprint_match_ok(env):
    cfg, reg = env
    reg.add(str(tempfile.mkdtemp()), name="proj")  # never-indexed: skipped
    checks = check_fingerprints(cfg, reg, StubEmbedder())
    assert _by_name(checks)["fingerprint"].status == "ok"


# ---------------------------------------------------------------------------
# aggregation + CLI
# ---------------------------------------------------------------------------

def test_run_doctor_aggregates_all_sections(env):
    cfg, reg = env
    checks = run_doctor(cfg, reg, StubEmbedder(), StubStore(),
                        StubOllama(models=("stub",)))
    names = {c.name for c in checks}
    assert {"python", "uv", "ollama-reachable", "ollama-model", "ollama-dim",
            "qdrant-reachable", "qdrant-compat", "qdrant-missing",
            "qdrant-orphans", "qdrant-dims", "index-root", "disk-free",
            "watch", "locks", "fingerprint"} <= names


def _cli_core(cfg, reg, store=None, ollama=None):
    core = Core(cfg)
    embedder = StubEmbedder()
    embedder.client = ollama if ollama is not None else StubOllama(
        models=(cfg.embed_model,))
    core.embedder = embedder  # type: ignore[assignment]
    core.store = store if store is not None else StubStore()  # type: ignore[assignment]
    core.registry = reg
    return core


def test_cli_doctor_healthy_exits_zero(env, monkeypatch):
    cfg, reg = env
    core = _cli_core(cfg, reg)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "ok " in result.output


def test_cli_doctor_failing_backend_exits_one(env, monkeypatch):
    cfg, reg = env
    core = _cli_core(cfg, reg,
                     store=StubStore(error=ConnectionError("down")))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "fail qdrant-reachable" in result.output


def test_cli_doctor_json(env, monkeypatch):
    cfg, reg = env
    core = _cli_core(cfg, reg)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    import json as _json
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert payload["healthy"] is True
    assert all(set(c) == {"status", "name", "detail"}
               for c in payload["checks"])