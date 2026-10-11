"""Output-contract tests (CI-26): one-line taxonomy errors, exit codes,
schema field, deterministic ordering. Iterates over every subcommand with a
failing input."""

from __future__ import annotations

import logging
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


def _record_stale_fingerprint(core, entry, model="older-model"):
    """Write a recorded fingerprint the running config does not match.

    The manifest seam: no index pass, no embedder round trip — just the
    meta rows a previous build would have left behind.
    """
    from code_indexer.embed_text import EMBED_FORMAT
    from code_indexer.graph_extract import EXTRACTION_VERSION
    from code_indexer.ts_chunker import CHUNKER_VERSION

    manifest = core.manifest_for(entry.slug)
    try:
        manifest.set_meta("embed_model", model)
        manifest.set_meta("embed_dim", "4")
        manifest.set_meta("embed_text_version", EMBED_FORMAT)
        manifest.set_meta("chunker_version", str(CHUNKER_VERSION))
        manifest.set_meta("extraction_version", str(EXTRACTION_VERSION))
    finally:
        manifest.close()


def test_fingerprint_refusal_renders_the_type_prefix_exactly_once(rig):
    """The mismatch refusal carries the error type ONCE.

    The message sources no longer bake ``ConfigError: `` in, so the
    taxonomy's ``<ErrorType>: <message>`` render is the only place a prefix
    comes from (a baked-in one doubled it: ``ConfigError: ConfigError:``).
    """
    from code_indexer.fingerprint import ConfigError as FingerprintConfigError

    core, reg, _entry, project = rig
    entry = core.registry.get_by_name("cproj")
    _record_stale_fingerprint(core, entry)

    with pytest.raises(FingerprintConfigError) as excinfo:
        core.assert_fingerprint_ok(entry)
    rendered = classify(excinfo.value).render()
    assert rendered.startswith("ConfigError: index for "), rendered
    assert rendered.count("ConfigError:") == 1, rendered
    assert "ConfigError: ConfigError" not in rendered
    assert "run: code-indexer reindex-project" in rendered


def test_semantic_search_refusal_is_one_taxonomy_line(rig):
    """The search refusal no longer escapes as a rich traceback: one
    taxonomy line on stderr, the taxonomy's exit code, nothing else."""
    core, reg, _entry, project = rig
    entry = core.registry.get_by_name("cproj")
    core.run_index(entry.slug, str(project))   # a real recorded fingerprint
    _record_stale_fingerprint(core, entry)

    result = runner.invoke(app, ["semantic-search", "q", "--project",
                                 str(project), "--skip-stale-check"])
    assert result.exit_code == 1, result.output
    lines = [ln for ln in result.output.splitlines()
             if ln.startswith("ConfigError:")]
    assert len(lines) == 1, result.output
    assert lines[0].startswith("ConfigError: index for "), lines[0]
    assert "ConfigError: ConfigError" not in result.output
    assert "Traceback" not in result.output


def test_verbose_refusal_logs_the_traceback_but_keeps_stdout_clean(rig):
    """The refusal's diagnostic record carries the exception under --verbose.

    The seam logs at INFO with ``exc_info=True``: ``configure_logging``
    raises our package to INFO under ``--verbose`` (WARNING otherwise), so
    the traceback itself is the log record, never a rich traceback on the
    result stream. At the default level the record is filtered out, so the
    one-line output contract is unchanged.
    """
    core, reg, _entry, project = rig
    entry = core.registry.get_by_name("cproj")
    core.run_index(entry.slug, str(project))   # a real recorded fingerprint
    _record_stale_fingerprint(core, entry)

    seen: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    handler = _Capture(level=logging.DEBUG)
    app_logger = logging.getLogger("code-indexer.cli")
    args = ["semantic-search", "q", "--project", str(project),
            "--skip-stale-check"]
    app_logger.addHandler(handler)
    try:
        verbose = runner.invoke(app, ["--verbose", *args])
        verbose_records = list(seen)
        seen.clear()
        default = runner.invoke(app, list(args))
    finally:
        app_logger.removeHandler(handler)

    # (a) --verbose: one diagnostic record, carrying the exception.
    assert verbose.exit_code == 1, verbose.output
    seam = [r for r in verbose_records
            if r.name == "code-indexer.cli"
            and r.getMessage() == "semantic-search failed"]
    assert len(seam) == 1, [r.getMessage() for r in verbose_records]
    assert seam[0].levelno == logging.INFO, seam[0].levelno
    assert seam[0].exc_info is not None, "record carries no exc_info"
    rendered = logging.Formatter().format(seam[0])
    assert "Traceback" in rendered and "ConfigError" in rendered, rendered
    # The result (stdout) stream stays result-only: no traceback, no log.
    assert verbose.stdout == "", verbose.stdout
    assert verbose.stderr.count("ConfigError:") == 1, verbose.stderr

    # (b) default level: the INFO record is filtered out entirely.
    assert default.exit_code == 1, default.output
    assert default.stdout == ""
    assert default.stderr.count("ConfigError:") == 1, default.stderr
    assert seen == [], [r.getMessage() for r in seen]


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