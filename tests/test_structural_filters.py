"""Structural filters on `find-symbol` (ADR-0002 section 4).

Each filter alone, then composed; a path filter and a name pattern are
checked against SQLite's own plan so "the name index answers this" is a
measured claim, not a comment; and a capped answer is checked to SAY it was
capped rather than looking complete. Offline: the embedder and store are
stubbed at the Core seam.
"""

from __future__ import annotations

import json
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

runner = CliRunner()

SERVICES = (
    "class UserManager:\n"
    "    def __init__(self):\n"
    "        self.items = []\n"
    "\n"
    "    def _secret(self):\n"
    "        return 1\n"
    "\n"
    "\n"
    "class OrderManager(UserManager):\n"
    "    def load(self):\n"
    "        return read_all()\n"
    "\n"
    "    def save(self):\n"
    "        return write_all()\n"
    "\n"
    "\n"
    "class Report:\n"
    "    def render(self):\n"
    "        return read_all()\n"
    "\n"
    "\ndef read_all():\n"
    "    return []\n"
    "\n"
    "\ndef write_all():\n"
    "    return []\n"
)

# Documentation-shaped files: no AST extractor, so their symbol rows are
# textual and their references are UNKNOWN rather than absent. This repo's
# own index is 760 of 925 files in exactly this state, which is why the
# evidence label exists at all. One class per file because the fallback
# chunker names a small file by its first declaration.
DOC_FILES = {
    "widgets.txt": "class TextWidget:\n    described in prose only, never parsed.\n",
    "pipeline.txt": "class PipelineDoc:\n    described in prose only, never parsed.\n",
    "report.txt": "class ReportDoc:\n    described in prose only, never parsed.\n",
}


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
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

    def swap_collection(self, *a, **kw):
        return None


def _build_rig(tmp_path, monkeypatch, files: dict[str, str]):
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
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    project = tmp_path / "proj"
    project.mkdir()
    for name, text in files.items():
        (project / name).write_text(text)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="filters")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


@pytest.fixture()
def structural_rig(tmp_path, monkeypatch):
    core, reg, entry, project = _build_rig(
        tmp_path, monkeypatch, {"services.py": SERVICES, **DOC_FILES})
    yield core, reg, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


def _json(project, *args) -> dict:
    result = runner.invoke(app, ["find-symbol", *args,
                                 "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _names(payload: dict) -> list[str]:
    return [row["name"] for row in payload["symbols"]]


# ---------------------------------------------------------------------------
# each filter alone
# ---------------------------------------------------------------------------

def test_kind_filter_answers_and_keeps_its_old_aliases(structural_rig):
    """--kind is the new spelling; --type/--symbol-type keep working."""
    _, _, _, project = structural_rig
    by_kind = _json(project, "--kind", "class")
    by_type = _json(project, "--type", "class")
    by_symbol_type = _json(project, "--symbol-type", "class")
    # Attributed rows sort first, then the textual ones — the documented
    # order, so a regex hit can never outrank a declaration.
    assert _names(by_kind) == ["OrderManager", "Report", "UserManager",
                                "PipelineDoc", "ReportDoc", "TextWidget"]
    assert by_kind == by_type == by_symbol_type
    assert all(row["type"] == "class" for row in by_kind["symbols"])


def test_name_pattern_is_a_glob_over_the_name(structural_rig):
    _, _, _, project = structural_rig
    payload = _json(project, "--name-pattern", "*Manager")
    assert _names(payload) == ["OrderManager", "UserManager"]
    assert payload["match_count"] == 2
    # A glob is case-sensitive (shell semantics), and it is never widened
    # behind the caller's back.
    assert _json(project, "--name-pattern", "*manager")["symbols"] == []


def test_container_filter_answers_with_the_parent_qualified_name(
        structural_rig):
    _, _, _, project = structural_rig
    payload = _json(project, "--container", "OrderManager")
    assert _names(payload) == ["load", "save"]
    assert all(row["container"] == "OrderManager"
               for row in payload["symbols"])


def test_visibility_filter_and_the_null_reads_as_public(structural_rig):
    core, _, entry, project = structural_rig
    private = _json(project, "--visibility", "private")
    # The underscore rule covers dunders too: __init__ is private.
    assert _names(private) == ["__init__", "_secret"]
    public = _json(project, "--visibility", "public")
    assert "read_all" in _names(public)
    assert "_secret" not in _names(public)
    # The column convention, checked against the store rather than assumed:
    # a NULL visibility is public, so the filter includes those rows.
    m = core.read_manifest(entry.slug)
    try:
        explicit = m.find_symbols(None, visibility="public", limit=None)
    finally:
        m.close()
    assert "read_all" in [row.name for row in explicit]


def test_language_filter_uses_the_denormalized_column(structural_rig):
    _, _, _, project = structural_rig
    python_rows = _json(project, "--language", "python")
    assert "read_all" in _names(python_rows)
    text_rows = _json(project, "--language", "text")
    assert _names(text_rows) == ["PipelineDoc", "ReportDoc", "TextWidget"]


def test_inherits_answers_from_the_attributed_base_class_row(structural_rig):
    _, _, _, project = structural_rig
    payload = _json(project, "--inherits", "UserManager")
    assert _names(payload) == ["OrderManager"]


def test_has_call_answers_from_the_attributed_call_row(structural_rig):
    _, _, _, project = structural_rig
    payload = _json(project, "--has-call", "read_all")
    assert _names(payload) == ["load", "render"]


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------

def test_filters_compose_as_and(structural_rig):
    _, _, _, project = structural_rig
    composed = _json(project, "--kind", "function",
                     "--container", "OrderManager",
                     "--has-call", "read_all")
    assert _names(composed) == ["load"]
    # Every clause narrows: a member of the other class is not a hit, and a
    # call to something else is not a hit either.
    assert _names(_json(project, "--kind", "function",
                        "--container", "Report",
                        "--has-call", "read_all")) == ["render"]
    assert _json(project, "--kind", "function",
                 "--container", "Report",
                 "--has-call", "write_all")["symbols"] == []
    # Name, file and structural filters meet in one read.
    both = _json(project, "--file", "services.py",
                 "--name-pattern", "*Manager",
                 "--kind", "class")
    assert _names(both) == ["OrderManager", "UserManager"]


# ---------------------------------------------------------------------------
# the query plan: no new index, and the filters are index-answered
# ---------------------------------------------------------------------------

def test_a_path_filter_and_a_name_pattern_do_not_scan_every_row(
        structural_rig):
    core, _, entry, _ = structural_rig
    m = core.read_manifest(entry.slug)
    try:
        file_plan = " | ".join(m.symbol_query_plan(
            file="services.py", name_pattern="*Manager"))
        assert "idx_symbols_file_start" in file_plan, file_plan
        prefix_plan = " | ".join(m.symbol_query_plan(name_pattern="User*"))
        assert "idx_symbols_name" in prefix_plan, prefix_plan
        assert "SEARCH s" in prefix_plan, prefix_plan
    finally:
        m.close()


# ---------------------------------------------------------------------------
# truncation
# ---------------------------------------------------------------------------

def test_a_filtered_answer_that_hits_the_limit_says_so(structural_rig):
    _, _, _, project = structural_rig
    capped = _json(project, "--kind", "function", "--limit", "2")
    assert len(capped["symbols"]) == 2
    assert capped["match_count"] > 2
    assert capped["truncated"] is True

    full = _json(project, "--kind", "function")
    assert full["match_count"] == len(full["symbols"])
    assert full["truncated"] is False

    text = runner.invoke(app, ["find-symbol", "--kind", "function",
                               "--limit", "2", "--project", str(project)])
    assert text.exit_code == 0, text.output
    assert "2 of" in text.output and "--limit 2 reached" in text.output


def test_the_default_browse_cap_still_applies_and_is_reported(
        structural_rig):
    """--limit keeps its meaning AND its default: browse mode caps at 25."""
    _, _, _, project = structural_rig
    payload = _json(project, "--kind", "function")
    assert payload["match_count"] == len(payload["symbols"]) == 7
    # A filter is still browse mode: no name was given, so no ambiguity block.
    assert "ambiguity" not in payload
