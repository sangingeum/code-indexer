"""Every row-reading surface refuses a needs-reindex project (exit 4).

Round 1's review found only the search path gated, so `find-symbol` and
`overview` answered "no symbols" with exit 0 from an index whose stored rows
this build would not produce. With the extraction-version bump every project
becomes needs-reindex, so the defect would have become universal. These tests
walk one surface list: each must exit 4, leave stdout EMPTY, and print exactly
one `RebuildRequiredError:` line naming the remedy. The commands that answer
without rows stay answerable.
"""

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
from code_indexer.graph_extract import EXTRACTION_VERSION  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

PROJECT_FILE = (
    "def main():\n"
    "    return 0\n"
    "\n"
    "\n"
    "class Core:\n"
    "    def search(self):\n"
    "        return main()\n"
)


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def embed_query_once(self, text):
        return [0.1] * 4

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
        pass


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def swap_collection(self, tmp, target):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *a, **kw):
        return 0

    def count_points(self, name):
        return 0

    def search(self, *a, **kw):
        return []


@pytest.fixture()
def needs_reindex_rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    (project / "a.py").write_text(PROJECT_FILE)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="stale")
    core.run_index(entry.slug, str(project))
    # The project exactly as this leg leaves every live project: the stored
    # fingerprint carries the previous extraction version.
    m = core.manifest_for(entry.slug)
    try:
        m.set_meta("extraction_version", str(EXTRACTION_VERSION - 1))
    finally:
        m.close()
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


# surface -> argv (without --project)
SURFACES = {
    "find-symbol": ["find-symbol", "Core"],
    "find-definition": ["find-definition", "Core"],
    "outline": ["outline", "a.py"],
    "skeleton": ["skeleton"],
    "get-code-context": ["get-code-context", "a.py", "--start-line", "1",
                         "--end-line", "2"],
    "find-references": ["find-references", "Core"],
    "find-callers": ["find-callers", "main"],
    "find-callees": ["find-callees", "main"],
    "deps": ["deps", "a.py"],
    "changed-symbols": ["changed-symbols"],
    "overview": ["overview"],
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_the_surface_refuses_with_exit_4(needs_reindex_rig, surface):
    core, entry, project = needs_reindex_rig
    result = runner.invoke(app, SURFACES[surface] + ["--project", str(project)])
    assert result.exit_code == 4, (surface, result.output)
    assert result.stdout == "", (surface, result.stdout)
    assert result.stderr.count("RebuildRequiredError:") == 1, (
        surface, result.stderr)
    assert "reindex-project" in result.stderr, (surface, result.stderr)


# Each command's OWN machine-readable spelling, read from its --help. `deps`
# is the exception the surface contract makes: ADR-0003 section 7 grants
# --json to five read commands and deps is not one of them — it spells the
# same mode `--format json`. Inventing --json for it here would test a flag
# the tool does not have (and typer would exit 2 before any refusal).
MACHINE_MODE = {name: ["--json"] for name in SURFACES}
MACHINE_MODE["deps"] = ["--format", "json"]


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_the_surface_refuses_in_its_machine_readable_mode(
        needs_reindex_rig, surface):
    """A machine-readable mode does not turn a refusal into a payload.

    stdout stays empty, the one refusal line goes to stderr, exit is 4.
    """
    core, entry, project = needs_reindex_rig
    result = runner.invoke(
        app, SURFACES[surface] + ["--project", str(project)]
        + MACHINE_MODE[surface])
    assert result.exit_code == 4, (surface, result.output)
    assert result.stdout == "", (surface, result.stdout)
    assert result.stderr.count("RebuildRequiredError:") == 1, (
        surface, result.stderr)


# ---------------------------------------------------------------------------
# the commands that answer without rows stay answerable
# ---------------------------------------------------------------------------

def test_list_projects_stays_answerable(needs_reindex_rig):
    result = runner.invoke(app, ["list-projects", "--json"])
    assert result.exit_code == 0, result.output
    assert "needs-reindex" in result.stdout


def test_lookup_project_stays_answerable(needs_reindex_rig):
    core, entry, project = needs_reindex_rig
    result = runner.invoke(app, ["lookup-project", str(project)])
    assert result.exit_code == 0, result.output
    assert "RebuildRequiredError" not in result.stderr


def test_index_status_stays_answerable(needs_reindex_rig):
    core, entry, project = needs_reindex_rig
    result = runner.invoke(app, ["index-status", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert "needs-reindex" in result.stdout


def test_doctor_is_not_refused(needs_reindex_rig):
    """Doctor may report failing checks; it must never be refused."""
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code != 4, result.output
    assert "RebuildRequiredError" not in result.stderr


# ---------------------------------------------------------------------------
# --refresh still repairs: the pass runs first, then the answer is allowed
# ---------------------------------------------------------------------------

def test_refresh_repairs_the_project_and_the_surface_then_answers(
        needs_reindex_rig):
    core, entry, project = needs_reindex_rig
    result = runner.invoke(
        app, ["outline", "a.py", "--project", str(project), "--refresh"])
    assert result.exit_code == 0, result.output
    m = core.manifest_for(entry.slug)
    try:
        assert m.get_meta("extraction_version") == str(EXTRACTION_VERSION)
    finally:
        m.close()
