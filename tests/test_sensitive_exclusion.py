"""Sensitive-content exclusion tests (privacy work item).

Fixture repo carries fake secrets (clearly bogus values matching the
high-confidence patterns). Acceptance: the secret files are not indexed —
nothing in the manifest, hence nothing in Qdrant payloads or search output —
and --allow-sensitive overrides per project.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.sensitive import (content_is_sensitive,  # noqa: E402
                                    file_is_sensitive,
                                    filename_is_sensitive)

runner = CliRunner()

SECRET_CONTENT = (
    "# prod config\n"
    # Assembled at runtime so push protection never sees a literal matching
    # a real secret-scanner pattern; the value still matches OUR regexes.
    "aws_access_key_id = AKIA" + "IOSFODNN7" + "EXAMPLE\n"
    "github_token = gh" + "p_" + "abcdefghij" * 4 + "\n"
    "slack_token = xo" + "xb-" + "123456789012" + "-abcdefghij\n"
)
JWT = ("token = ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
       ".ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0"
       ".Sf" + "lKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
PEM = ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n"
       "-----END RSA PRIVATE KEY-----")
GITHUB_TOKEN_LINE = "gh" + "p_" + "a" * 40

FILES = {
    "src/app.py": "def handler():\n    return 42\n",
    "src/ok.json": '{"greeting": "hello"}\n',
    ".env": SECRET_CONTENT,                      # filename skip (.env*)
    "deploy/server.pem": PEM,                    # filename skip (*.pem)
    "deploy/aws.key": "raw key material",        # filename skip (*.key)
    "config/credentials.yml": "user: admin",     # filename skip (credentials*)
    "terraform/prod.tfstate": '{"version": 4}',  # filename skip (*.tfstate)
    "src/tokens.py": JWT,                        # content skip (JWT)
    "deploy/github_token.txt": GITHUB_TOKEN_LINE,  # content skip (GitHub)
}


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


class StubStore:
    def __init__(self):
        self.payloads: list[dict] = []

    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        for p in points:
            self.payloads.append(dict(p.payload))

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        self.payloads = [p for p in self.payloads if p.get("file") != path
                         or (min_chunk_index
                             and p.get("chunk_index", 0) < min_chunk_index)]
        return 0

    def count_points(self, name):
        return len(self.payloads)

    def search(self, *a, **kw):
        return []


def _cfg(tmp: str) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub",
                  index_root=tmp, stale_ttl=60, embed_batch=48,
                  upsert_batch=256, max_file_bytes=1048576,
                  watch_debounce=3, watch_sweep_interval=300)


@pytest.fixture()
def rig(tmp_path):
    cfg = _cfg(str(tmp_path / "state"))
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / "deploy").mkdir()
    (project / "config").mkdir()
    (project / "terraform").mkdir()
    for rel, text in FILES.items():
        p = project / rel
        p.write_text(text)
    core = Core(cfg)
    store = StubStore()
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = store  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="secretproj")
    yield core, reg, entry, store
    reg.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# pattern units
# ---------------------------------------------------------------------------

def test_filename_globs():
    for path in (".env", ".env.production", "server.pem", "id_rsa",
                 "aws.key", "credentials.yml", "secrets.json",
                 "terraform.tfstate"):
        assert filename_is_sensitive(path), path
    for path in ("src/app.py", "environment.d/settings.toml", "keys.md",
                 "envdir/README", "terraform/main.tf"):
        assert not filename_is_sensitive(path), path


def test_content_patterns():
    assert content_is_sensitive(SECRET_CONTENT)
    assert content_is_sensitive(JWT)
    assert content_is_sensitive(PEM)
    assert content_is_sensitive("token = AKIAREDACTEDKEY0000X")  # AKIA+16
    # Near-misses must NOT trigger (high-confidence only).
    assert not content_is_sensitive("aws_access_key_id = AKIA")   # too short
    assert not content_is_sensitive("def handler(): return 42")
    assert not content_is_sensitive("jwt decoding discussion, no token")


def test_file_is_sensitive_precedence():
    assert file_is_sensitive(".env", SECRET_CONTENT) == "filename"
    assert file_is_sensitive("src/plain.py", JWT) == "content"
    assert file_is_sensitive("src/plain.py", "clean") is None


# ---------------------------------------------------------------------------
# end-to-end: secrets never reach the index
# ---------------------------------------------------------------------------

def test_secrets_excluded_from_index(rig):
    core, reg, entry, store = rig
    result = core.run_index(entry.slug, entry.path)
    assert result["result"]["sensitive_skipped"] == 7  # 5 by name, 2 by content
    indexed_files = {p["file"] for p in store.payloads}
    assert "src/app.py" in indexed_files
    for secret_file in (".env", "deploy/server.pem", "deploy/aws.key",
                        "config/credentials.yml", "terraform/prod.tfstate",
                        "src/tokens.py", "deploy/github_token.txt"):
        assert secret_file not in indexed_files, secret_file


def test_manifest_has_no_secret_rows(rig):
    core, reg, entry, store = rig
    core.run_index(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        rows = set(m.all_files())
    finally:
        m.close()
    assert "src/app.py" in rows
    assert ".env" not in rows
    assert "src/tokens.py" not in rows


def test_allow_sensitive_override_indexes_everything(rig):
    core, reg, entry, store = rig
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("allow_sensitive", "1")
    finally:
        m.close()
    result = core.run_index(entry.slug, entry.path)
    assert result["result"]["sensitive_skipped"] == 0
    indexed_files = {p["file"] for p in store.payloads}
    assert ".env" in indexed_files
    assert "src/tokens.py" in indexed_files


def test_revoked_override_purges_previous_secret_chunks(rig):
    core, reg, entry, store = rig
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("allow_sensitive", "1")
    finally:
        m.close()
    core.run_index(entry.slug, entry.path)
    assert ".env" in {p["file"] for p in store.payloads}
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("allow_sensitive", "0")
    finally:
        m.close()
    core.run_index(entry.slug, entry.path)
    assert ".env" not in {p["file"] for p in store.payloads}


def test_cli_add_project_allow_sensitive(tmp_path, monkeypatch):
    import code_indexer.cli as cli
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("A=1\n")
    (project / "a.py").write_text("def a():\n    return 1\n")
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None

    class _Emb:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

    class _Store:
        def collection_exists(self, n):
            return True

        def create_collection(self, n, d):
            pass

        def upsert_points(self, n, pts):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

    core = Core(_cfg(str(tmp_path / "state")))
    core.embedder = _Emb()  # type: ignore[assignment]
    core.store = _Store()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(
        app, ["add-project", str(project), "--allow-sensitive"])
    assert result.exit_code == 0, result.output
    assert "--allow-sensitive set" in result.output
    m = core.manifest_for(core.registry.get_by_path(str(project)).slug)
    try:
        assert m.get_meta("allow_sensitive") == "1"
    finally:
        m.close()
    cli._core = None


def test_index_status_reports_sensitive_count(rig, monkeypatch):
    import code_indexer.cli as cli
    core, reg, entry, store = rig
    core.run_index(entry.slug, entry.path)
    cli._core = core
    cli._core_skip = False
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", entry.path])
    assert result.exit_code == 0
    assert "sensitive=" in result.output and "files skipped" in result.output
    cli._core = None


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))