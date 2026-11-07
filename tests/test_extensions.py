"""The optional extras: absence is one actionable line, never a traceback.

A core install has no Ollama client, no Qdrant client and no MCP SDK. The
package imports with none of them; a command that needs the semantic
extension stops with ``ConfigError: the semantic extension is not installed;
install code-indexer[semantic]`` and exit 3.
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hide the two client libraries from this interpreter before anything else is
# imported: find_spec answers None and an attempted import raises, which is
# exactly what a core-only install looks like.
_BLOCKED = '''
import builtins, importlib.util, sys

_real_find = importlib.util.find_spec

def _find(name, *args, **kwargs):
    if name in ("ollama", "qdrant_client"):
        return None
    return _real_find(name, *args, **kwargs)

importlib.util.find_spec = _find

_real_import = builtins.__import__

def _import(name, *args, **kwargs):
    if name == "ollama" or name.startswith("qdrant_client"):
        raise ImportError("No module named " + name)
    return _real_import(name, *args, **kwargs)

builtins.__import__ = _import
'''


def _run(script: str, tmp_path) -> subprocess.CompletedProcess:
    env = {**os.environ,
           "INDEX_ROOT": str(tmp_path / "state"),
           "PYTHONPATH": os.path.join(REPO, "src")}
    return subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, check=False,
                          cwd=str(tmp_path), env=env)


def test_semantic_search_without_the_extra_is_one_line_and_exit_three(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    script = _BLOCKED + f'''
import os
os.makedirs(os.environ["INDEX_ROOT"], exist_ok=True)
from code_indexer.registry import Registry

reg = Registry(os.path.join(os.environ["INDEX_ROOT"], "registry.db"))
reg.add({str(proj)!r}, name="p")
reg.close()

from code_indexer.cli import main
sys.argv = ["code-indexer", "semantic-search", "anything", "--project", {str(proj)!r}]
main()
'''
    proc = _run(script, tmp_path)
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr.strip().splitlines() == [
        "ConfigError: the semantic extension is not installed; "
        "install code-indexer[semantic]"]


def test_an_index_pass_without_the_extra_builds_a_rows_only_manifest(tmp_path):
    """A vectors-off pass needs no extension: rows, chunks and FTS land."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    script = _BLOCKED + f'''
from code_indexer.cli import main
sys.argv = ["code-indexer", "add-project", {str(proj)!r}]
main()
'''
    proc = _run(script, tmp_path)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "vectors=off" in proc.stdout
    assert proc.stderr == ""


def test_an_embedding_pass_without_the_extra_is_the_same_line(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    script = _BLOCKED + f'''
from code_indexer.cli import main
sys.argv = ["code-indexer", "add-project", {str(proj)!r}, "--vectors", "on"]
main()
'''
    proc = _run(script, tmp_path)
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr.strip().splitlines() == [
        "ConfigError: the semantic extension is not installed; "
        "install code-indexer[semantic]"]


def test_package_import_brings_no_submodule(tmp_path):
    """`import code_indexer` costs no client library and no submodule."""
    script = "import sys, code_indexer\n" \
             "print([m for m in sys.modules if m.startswith('code_indexer.')])\n"
    proc = _run(script, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


def test_the_extension_error_renders_as_config_error_with_exit_three():
    from code_indexer.errors import ExtensionUnavailableError
    from code_indexer.extensions import SEMANTIC_EXTENSION_MESSAGE

    exc = ExtensionUnavailableError(SEMANTIC_EXTENSION_MESSAGE)
    assert exc.render() == ("ConfigError: " + SEMANTIC_EXTENSION_MESSAGE)
    assert exc.exit_code == 3


def test_doctor_completes_with_the_extra_absent(tmp_path, monkeypatch):
    """A core-only install still gets a health check: unverified, not a crash."""
    from typer.testing import CliRunner

    import code_indexer.cli as cli
    from code_indexer.cli import app
    from code_indexer.config import Config
    from code_indexer.core import Core

    monkeypatch.setattr(cli, "semantic_available", lambda: False)
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cfg = Config(ollama_url="http://127.0.0.1:1",
                 qdrant_url="http://127.0.0.1:1", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    core = Core(cfg)
    assert core._embedder is None and core._store is None
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "unverified" in result.output
    assert "Traceback" not in result.output
