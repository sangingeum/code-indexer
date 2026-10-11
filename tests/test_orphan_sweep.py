"""Issue #11: the orphan sweep.

Residue is a row in ``refs`` / ``imports`` / ``symbols`` whose ``file`` has no
``files`` row AND whose file is gone from the working tree. Two mechanisms:

1. ``sweep-orphans`` — a new CLI subcommand, dry-run by default: it prints the
   per-table counts and the file list it would clear and changes nothing;
   ``--apply`` removes them in one transaction. It must not trigger an index
   pass.
2. the forward leak — ``Manifest.delete_files`` purged ``files``/``symbols``/
   ``refs`` but not ``imports``, so every deletion left import rows behind. New
   deletions no longer accrue that residue.

Safety properties pinned here, all revert-sensitive:

- the dry run changes nothing (row counts before == after) and prints counts +
  the file list;
- ``--apply`` removes exactly the expected rows and no others;
- a path whose rows have no ``files`` row but which IS present on disk is NOT
  listed for removal and survives both modes (the tree check);
- ``imports`` is purged on a real deletion.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.manifest import SymbolRow  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

TABLES = ("symbols", "refs", "imports")


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.aliases: dict[str, str] = {}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections or name in self.aliases

    def create_collection(self, name: str, dim: int) -> None:
        self.collections.add(name)

    def drop_collection(self, name: str) -> None:
        self.collections.discard(name)
        self.aliases.pop(name, None)

    def upsert_points(self, name: str, points: list) -> int:
        return len(points)

    def purge_file_points(self, *a, **kw) -> int:
        return 0

    def count_points(self, name: str) -> int:
        return 0

    def search(self, *a, **kw):
        return []


def _wire(cfg: Config, reg: Registry) -> Core:
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    core.registry = reg
    return core


@pytest.fixture()
def rig(tmp_path):
    """An indexed sandbox project.

    ``keep.py`` stays; ``ondisk.py`` is created AFTER indexing, so it has rows
    inserted at the manifest seam but never a ``files`` row while being present
    on disk — the "different predicates" case the tree check guards.
    """
    root = tmp_path / "state"
    root.mkdir(parents=True, exist_ok=True)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "keep.py").write_text("def keep():\n    return 1\n")
    (project / "gone.py").write_text(
        "import os\n\n\ndef gone():\n    return os.getcwd()\n")
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(root), stale_ttl=60, embed_batch=48,
                 upsert_batch=256, max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    reg = Registry(str(root / "registry.db"))
    entry = reg.add(str(project), name="orphproj")
    core = _wire(cfg, reg)
    assert core.run_index(entry.slug, str(project))["state"] == "idle"
    # A file present on disk but never indexed: rows with no files row.
    (project / "ondisk.py").write_text("def ondisk():\n    return 2\n")
    yield core, reg, entry, project
    reg.close()
    shutil.rmtree(str(root), ignore_errors=True)
    shutil.rmtree(str(project), ignore_errors=True)


def _delete_file_and_its_files_row(core: Core, slug: str, project, path: str):
    """Turn ``path`` into an orphan: gone from the tree, files row removed,
    but its symbols/refs/imports rows left behind (the residue shape)."""
    os.remove(os.path.join(str(project), path))
    m = core.manifest_for(slug)
    try:
        m._conn.execute("DELETE FROM files WHERE path = ?", (path,))
        m._conn.commit()
    finally:
        m.close()


def _insert_rows_with_no_files_row(core: Core, slug: str, path: str) -> None:
    m = core.manifest_for(slug)
    try:
        m.replace_file_symbols(path, [SymbolRow(
            file=path, name="ondisk", symbol_type="function", start_line=1,
            end_line=2, source="ast", signature="def ondisk():",
            visibility="public")])
    finally:
        m.close()


def _row_counts(core: Core, slug: str) -> dict[str, int]:
    m = core.manifest_for(slug)
    try:
        return {t: m._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("files",) + TABLES}
    finally:
        m.close()


def _file_rows(core: Core, slug: str, path: str) -> dict[str, int]:
    m = core.manifest_for(slug)
    try:
        return {t: m._conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE file = ?", (path,)).fetchone()[0]
            for t in TABLES}
    finally:
        m.close()


def _text(result) -> str:
    """The combined stream, deduped (see tests/test_staleness_reporting.py)."""
    out = result.output
    err = getattr(result, "stderr", "") or ""
    if err and err not in out:
        out += err
    return out


def _removals(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("- ")]


# ---------------------------------------------------------------------------
# the forward leak: delete_files purges imports too
# ---------------------------------------------------------------------------

def test_delete_files_purges_imports_rows(rig):
    core, reg, entry, project = rig
    # gone.py imports os, so the index holds an imports row for it.
    assert _file_rows(core, entry.slug, "gone.py")["imports"] >= 1
    m = core.manifest_for(entry.slug)
    try:
        m.delete_files(["gone.py"])
    finally:
        m.close()
    after = _file_rows(core, entry.slug, "gone.py")
    assert after == {"symbols": 0, "refs": 0, "imports": 0}, after


# ---------------------------------------------------------------------------
# dry run: reports, changes nothing
# ---------------------------------------------------------------------------

def test_dry_run_reports_and_changes_nothing(rig, monkeypatch):
    core, reg, entry, project = rig
    _delete_file_and_its_files_row(core, entry.slug, project, "gone.py")
    _insert_rows_with_no_files_row(core, entry.slug, "ondisk.py")
    before = _row_counts(core, entry.slug)

    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["sweep-orphans", str(project)])
    assert result.exit_code == 0, result.output
    text = _text(result)
    assert "dry-run" in text
    assert "refs=" in text and "imports=" in text and "symbols=" in text
    # gone.py is listed for removal …
    assert "- gone.py" in _removals(text)
    # … ondisk.py is present on disk, so it must NOT be in the removal list.
    assert not any("ondisk.py" in ln for ln in _removals(text))
    # the guard is reported, not acted on
    assert "ondisk.py" in text
    # nothing changed
    assert _row_counts(core, entry.slug) == before


def test_dry_run_counts_are_accurate(rig, monkeypatch):
    core, reg, entry, project = rig
    _delete_file_and_its_files_row(core, entry.slug, project, "gone.py")
    expected = _file_rows(core, entry.slug, "gone.py")
    assert expected["symbols"] >= 1
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["sweep-orphans", str(project), "--json"])
    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["files"] == ["gone.py"]
    assert payload["per_table"] == expected
    assert payload["total"] == sum(expected.values())
    assert payload["removed"] == 0


# ---------------------------------------------------------------------------
# apply: removes exactly the expected rows
# ---------------------------------------------------------------------------

def test_apply_removes_exactly_the_expected_rows(rig, monkeypatch):
    core, reg, entry, project = rig
    _delete_file_and_its_files_row(core, entry.slug, project, "gone.py")
    _insert_rows_with_no_files_row(core, entry.slug, "ondisk.py")
    keep_before = _file_rows(core, entry.slug, "keep.py")

    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["sweep-orphans", str(project), "--apply"])
    assert result.exit_code == 0, result.output
    text = _text(result)
    assert "applied" in text
    assert "removed=" in text
    # gone.py's residue is gone …
    assert _file_rows(core, entry.slug, "gone.py") == {
        "symbols": 0, "refs": 0, "imports": 0}
    # … the live file is untouched …
    assert _file_rows(core, entry.slug, "keep.py") == keep_before
    # … and the present-on-disk path survives apply.
    assert _file_rows(core, entry.slug, "ondisk.py")["symbols"] == 1
    # a second apply finds nothing left to clear
    second = runner.invoke(app, ["sweep-orphans", str(project), "--apply"])
    assert second.exit_code == 0, second.output
    assert _removals(_text(second)) == []
    assert _file_rows(core, entry.slug, "ondisk.py")["symbols"] == 1


def test_sweep_does_not_trigger_an_index_pass(rig):
    core, reg, entry, project = rig
    _delete_file_and_its_files_row(core, entry.slug, project, "gone.py")

    def _boom(*a, **kw):
        raise AssertionError("sweep-orphans must not run an index pass")

    core.maybe_refresh = _boom  # type: ignore[assignment]
    core.run_index = _boom  # type: ignore[assignment]
    result = core.sweep_orphans(entry, str(project))
    assert result["files"] == ["gone.py"]
    assert result["removed"] == 0  # dry run by default at the core level too


def test_core_sweep_reports_present_on_disk_as_kept(rig):
    core, reg, entry, project = rig
    _delete_file_and_its_files_row(core, entry.slug, project, "gone.py")
    _insert_rows_with_no_files_row(core, entry.slug, "ondisk.py")
    result = core.sweep_orphans(entry, str(project))
    assert result["files"] == ["gone.py"]
    assert result["present_on_disk_kept"] == ["ondisk.py"]
    assert result["apply"] is False