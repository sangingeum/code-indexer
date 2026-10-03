"""Changed-symbols / diff-impact tests (navigation work item).

Temporary git repo fixtures covering add/modify/delete/rename/untracked/
binary, syntax-error fallback, and impact. All offline (git + tree-sitter).
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer import changed  # noqa: E402

runner = CliRunner()

PY_V1 = "def keep():\n    return 1\n\n\ndef old_fn():\n    return 2\n"
PY_V2 = "def keep():\n    return 1\n\n\ndef new_fn():\n    return 3\n"


def _git(path, *args, check=True):
    r = subprocess.run(["git", "-C", str(path), *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text(PY_V1)
    (repo / "helper.py").write_text("def used():\n    return 1\n")
    (repo / "old.py").write_text("def moved():\n    return 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text("import app\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_add_modify_delete_rename(git_repo, monkeypatch):
    repo = git_repo
    # modify app.py (old_fn -> new_fn), delete helper.py, add extra.py,
    # and a REAL rename (old.py -> moved.py, identical content so git -M
    # pairs them as R100).
    (repo / "app.py").write_text(PY_V2)
    (repo / "helper.py").unlink()
    (repo / "extra.py").write_text("def extra():\n    return 0\n")
    (repo / "old.py").unlink()
    (repo / "moved.py").write_text("def moved():\n    return 1\n")
    _git(repo, "add", "-A")
    result = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                     staged=True)
    kinds = {f: e["kind"] for f, e in result.items()}
    assert kinds["extra.py"] == "added"
    assert kinds["app.py"] == "modified"
    assert kinds["helper.py"] == "removed"
    assert kinds["moved.py"] == "renamed"
    # app.py symbols: new_fn mapped; old_fn detected as removed.
    app_entry = result["app.py"]
    names = {s["name"] for s in app_entry["symbols"] if s.get("name")}
    assert "new_fn" in names
    removed = {r["name"] for r in app_entry.get("removed_symbols", [])}
    assert "old_fn" in removed
    assert "keep" not in removed  # unchanged symbol not reported


def test_untracked_and_binary(git_repo, monkeypatch):
    repo = git_repo
    (repo / "untracked.py").write_text("def fresh():\n    pass\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    result = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                     staged=True, include_untracked=True)
    # Untracked file mapped as added with its symbol.
    assert result["untracked.py"]["kind"] == "added"
    assert any(s.get("name") == "fresh"
               for s in result["untracked.py"]["symbols"])
    # Binary content under 'other files'.
    other = result.get("(other files)", {}).get("files", [])
    assert "blob.bin" in other


def test_non_code_files_grouped(git_repo):
    repo = git_repo
    (repo / "README.md").write_text("# changed docs\n")
    _git(repo, "add", "-A")
    result = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                     staged=True)
    other = result.get("(other files)", {}).get("files", [])
    assert "README.md" in other


def test_syntax_error_falls_back_to_lines(git_repo):
    repo = git_repo
    (repo / "broken.py").write_text("def broken(:\n  this is not valid\n")
    _git(repo, "add", "-A")
    result = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                     staged=True)
    entry = result["broken.py"]
    assert entry["symbols"], "line-range fallback present"
    # Error-tolerant tree-sitter may still name the symbol; either a named
    # symbol or an explicit fallback line-range entry is acceptable.
    assert (any(s.get("name") for s in entry["symbols"])
            or all(s.get("fallback") for s in entry["symbols"]))


def test_not_a_git_repo(tmp_path):
    with pytest.raises(changed.NotAGitRepo):
        changed.changed_symbols(str(tmp_path))


def test_unindexed_project_works(git_repo, tmp_path, monkeypatch):
    """Pure live parsing: no index at all for the symbol view."""
    repo = git_repo
    (repo / "app.py").write_text(PY_V2)
    _git(repo, "add", "app.py")
    result = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                     staged=True)
    assert any(s.get("name") == "new_fn"
               for s in result["app.py"]["symbols"])


def test_cli_changed_symbols(git_repo, tmp_path, monkeypatch):
    repo = git_repo
    (repo / "app.py").write_text(PY_V2)
    _git(repo, "add", "-A")
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    os.makedirs(str(tmp_path / "state"), exist_ok=True)
    monkeypatch.setattr(cli, "_core", None)  # fresh Core for the env root
    monkeypatch.setattr(cli, "_core_skip", False)
    # Register the repo (unindexed is fine — changed-symbols parses live).
    from code_indexer.registry import Registry
    reg = Registry(str(tmp_path / "state" / "registry.db"))
    reg.add(str(repo), name="clirepo")
    reg.close()
    result = runner.invoke(app, ["changed-symbols", "--project", str(repo),
                                 "--staged"])
    assert result.exit_code == 0, result.output
    assert "modified app.py" in result.output
    assert "new_fn" in result.output
    result_json = runner.invoke(app, ["changed-symbols", "--project",
                                      str(repo), "--staged", "--json"])
    assert result_json.exit_code == 0
    assert '"schema": 1' in result_json.output


def test_render_format(git_repo):
    repo = git_repo
    (repo / "app.py").write_text(PY_V2)
    (repo / "helper.py").unlink()
    (repo / "extra.py").write_text("def extra():\n    return 0\n")
    _git(repo, "add", "-A")
    changed_data = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                           staged=True)
    out = changed.render(changed_data)
    assert "modified app.py" in out
    assert any(ln.startswith("removed helper.py") for ln in out.splitlines())
    assert any(ln.startswith("added extra.py") for ln in out.splitlines())


def test_impact_lists_callers(git_repo, tmp_path, monkeypatch):
    """Impact enriches modified symbols with graph-table callers + tests."""
    from code_indexer.config import Config
    from code_indexer.core import Core
    from code_indexer.registry import Registry
    repo = git_repo
    # Caller references old_fn (still in HEAD content it calls old_fn).
    (repo / "caller.py").write_text("import app\n\napp.old_fn()\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "with caller")
    # Now change app.py (old_fn -> new_fn) and stage.
    (repo / "app.py").write_text(PY_V2)
    _git(repo, "add", "-A")

    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"), stale_ttl=60,
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
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
    entry = reg.add(str(repo), name="diffproj")
    core.run_index(entry.slug, str(repo))

    changed_data = changed.changed_symbols(str(repo), base="HEAD", head=None,
                                           staged=True)
    imp = changed.impact(core, entry.slug, changed_data)
    app_imp = imp.get("app.py", {})
    caller_files = {c["file"] for c in app_imp.get("callers", [])}
    assert "caller.py" in caller_files
    tests = app_imp.get("candidate_tests", [])
    assert "tests/test_app.py" in tests


def test_mcp_changed_symbols_tool_exists():
    import code_indexer.server as server
    assert hasattr(server, "changed_symbols")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))