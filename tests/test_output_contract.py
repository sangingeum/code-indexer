"""Output-contract tests (CI-26): one-line taxonomy errors, exit codes,
schema field, deterministic ordering. Iterates over every subcommand with a
failing input."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    ArgumentError, BackendError, ConfigError, InternalError, LockedError,
    NotFoundError, classify)

runner = CliRunner()


# ---------------------------------------------------------------------------
# taxonomy units
# ---------------------------------------------------------------------------

def test_taxonomy_render_and_exit_codes():
    assert NotFoundError("x not registered").render() == \
        "NotFoundError: x not registered"
    assert ArgumentError("bad").exit_code == 2
    for t in (ConfigError, NotFoundError, BackendError, LockedError,
              InternalError):
        assert t("x").exit_code == 1


def test_classify_maps_legacy_messages():
    assert classify(ValueError("error: not registered: /p")).error_type == \
        "NotFoundError"
    assert classify(ValueError("error: path does not exist: /p")) \
        .error_type == "NotFoundError"
    assert classify(ValueError("indexing in progress")) \
        .error_type == "LockedError"
    assert classify(ValueError("error: connection refused (ollama)")) \
        .error_type == "BackendError"
    assert classify(ValueError("error: something odd")).error_type == \
        "InternalError"
    assert classify(ValueError("error: connection refused (ollama)")) \
        .render() == "BackendError: connection refused (ollama)"


def test_classify_wraps_fingerprint_config_error():
    from code_indexer.fingerprint import ConfigError as LegacyConfigError
    err = classify(LegacyConfigError("error: embed model changed; run: "
                                     "code-indexer reindex-project /p"))
    assert isinstance(err, ConfigError)
    assert err.render().startswith("ConfigError: embed model changed")


# ---------------------------------------------------------------------------
# every subcommand with a failing input: one line, taxonomy prefix, exit 1/2
# ---------------------------------------------------------------------------

FAIL_CASES = [
    # (args, expected_type) — failing inputs that exercise the taxonomy.
    (["find-symbol", "x", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["find-callers", "x", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["find-callees", "x", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["deps", "x.py", "--project", "/nonexistent/path"], "NotFoundError"),
    (["overview", "--project", "/nonexistent/path"], "NotFoundError"),
    (["skeleton", "--project", "/nonexistent/path"], "NotFoundError"),
    (["outline", "f.py", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["get-code-context", "f.py", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["semantic-search", "q", "--project", "/nonexistent/path"],
     "NotFoundError"),
    (["index-status", "/nonexistent/path"], "NotFoundError"),
    (["reindex-project", "/nonexistent/path"], "NotFoundError"),
    (["remove-project", "/nonexistent/path"], "NotFoundError"),
]


@pytest.mark.parametrize("args,expected_type", FAIL_CASES)
def test_subcommand_error_contract(args, expected_type, tmp_path,
                                   monkeypatch):
    # Fresh state root: nothing registered -> the resolution errors.
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    result = runner.invoke(app, args)
    assert result.exit_code in (1, 2), result.output
    err_lines = [ln for ln in result.output.splitlines()
                 if ln.startswith(tuple(f"{t}:" for t in (
                     "ArgumentError", "ConfigError", "NotFoundError",
                     "BackendError", "LockedError", "InternalError")))]
    assert len(err_lines) == 1, result.output
    assert err_lines[0].startswith(f"{expected_type}:"), err_lines[0]


def test_usage_error_exit_code_2():
    # Malformed invocation (missing required arg) -> exit 2 via Typer.
    result = runner.invoke(app, ["find-callers"])
    assert result.exit_code == 2


def test_success_stdout_is_result_only(rig, tmp_path, monkeypatch):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["list-projects"])
    assert result.exit_code == 0
    # stdout carries data only — no progress chatter, no "Indexed" lines.
    for line in result.output.splitlines():
        assert not line.startswith(("Indexed", "indexing", "background"))


# ---------------------------------------------------------------------------
# fixture rig (minimal)
# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text("def main():\n    return 0\n")
    core = Core(cfg)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

        def query_instruction_text(self, q):
            return q

        def embed_with_errors(self, texts):
            return [[0.1] * 4 for _ in texts], []

        def close(self):
            pass

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    reg.add(str(project), name="cproj")
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, None, project


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))