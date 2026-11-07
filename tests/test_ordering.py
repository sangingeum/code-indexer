"""The ordering policy: one classifier, one key order, no ties by accident.

Two layers. The classifier and key shapes are pinned here directly; the
surfaces are exercised through the CLI on a fixture built so the ordering
CANNOT be satisfied by accident -- the test and documentation files carry MORE
declarations and MORE references than the source file, so a list ordered by
count, by path or by insertion order disagrees with the answer asserted here.
A determinism re-run pins the tie-break layer.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer import ordering  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


@dataclass
class Row:
    """The subset of ``SymbolRow`` a key reads."""

    file: str
    name: str
    symbol_type: str | None = "function"
    start_line: int = 1
    source: str = "ast"
    qualified_name: str | None = None
    decl_hash: str | None = None


@pytest.mark.parametrize("path", [
    "src/app.py",
    "pkg/core.py",
    "src/tackboard/static/app.css",
    "Vivarium.Sim/DemoSystem.cs",
    "vendor/libfoo.c",
    "README.weird",
])
def test_source_files_are_source(path):
    assert ordering.file_class(path) == ordering.SOURCE, path


@pytest.mark.parametrize("path", [
    "tests/test_app.py",
    "tests/conftest.py",
    "packages/tackboard-mcp/tests/conftest.py",
    "Vivarium.Sim.Tests/ZeroAllocTickTests.cs",
    "Vivarium.Sim.Tests/CapabilityHostTests.cs",
    "src/test/java/com/example/FooTest.java",
    "ui/app.spec.ts",
    "crates/core/tests/roundtrip.rs",
    "some_test.go",
])
def test_test_paths_are_tests(path):
    assert ordering.file_class(path) == ordering.TESTS, path


@pytest.mark.parametrize("path", [
    "docs/overhaul-baseline-audit.md",
    "README.md",
    "CHANGELOG.md",
    "skill/references/query.md",
    "doc/architecture.rst",
    "notes.txt",
    "tests/README.md",  # a document wherever it sits; the tests rule reads code
])
def test_documents_are_docs(path):
    assert ordering.file_class(path) == ordering.DOCS, path


@pytest.mark.parametrize("path", [
    "dist/bundle.min.js",
    "build/generated.c",
    "target/debug/main.rs",
    "node_modules/left-pad/index.js",
    "Cargo.lock",
    "uv.lock",
    "gen/api.pb.go",
    "a/model_generated.py",
    "static/app.min.css",
])
def test_build_output_and_artifacts_are_generated(path):
    assert ordering.file_class(path) == ordering.GENERATED, path


def test_a_generated_document_under_build_is_still_an_artifact():
    """Class rules apply in order: artifact, then doc, then test."""
    assert ordering.file_class("build/report.md") == ordering.GENERATED


def test_an_unrecognised_shape_stays_source():
    """An unknown file is never demoted behind a fixture by guesswork."""
    assert ordering.file_class("mystery.qqq") == ordering.SOURCE


def test_the_class_ladder_is_source_test_doc_artifact():
    assert ordering.CLASS_ORDER == (ordering.SOURCE, ordering.TESTS,
                                    ordering.DOCS, ordering.GENERATED)
    weights = [ordering.class_weight(p) for p in (
        "src/app.py", "tests/test_app.py", "docs/design.md",
        "dist/app.min.js")]
    assert weights == [0, 1, 2, 3], weights


def test_symbol_key_puts_the_class_first_and_the_exact_name_before_a_prefix():
    source = Row(file="src/app.py", name="Tick")
    tests = Row(file="tests/test_app.py", name="Tick")
    assert ordering.symbol_key(source, "Tick") < ordering.symbol_key(
        tests, "Tick")
    prefix = Row(file="src/prefix.py", name="Ticker")
    assert ordering.symbol_key(source, "Tick") < ordering.symbol_key(
        prefix, "Tick"), "a row that merely shares a prefix is not an exact hit"


def test_a_row_that_cannot_name_its_match_sorts_last_in_its_class():
    named = Row(file="src/a.py", name="Tick", symbol_type="method")
    unnamed = Row(file="src/a.py", name="Tick", symbol_type=None)
    assert ordering.symbol_key(named, "Tick") < ordering.symbol_key(
        unnamed, "Tick")


def test_edge_key_promotes_the_caller_that_names_its_symbol():
    named = ordering.edge_key("src/a.py", "Runner.Tick", 10, "call", "ast")
    unnamed = ordering.edge_key("src/a.py", None, 10, "call", "ast")
    assert named < unnamed


def test_edge_key_still_puts_the_file_class_first():
    source = ordering.edge_key("src/a.py", None, 10, "call", "ast")
    tests = ordering.edge_key("tests/test_a.py", "Runner.Tick", 10, "call",
                              "ast")
    assert source < tests


def test_hotspot_key_ranks_class_before_the_count():
    source = ordering.hotspot_key("src/core.py", 100)
    tests = ordering.hotspot_key("tests/conftest.py", 1460)
    assert source < tests
    # ...and within one class the count still decides.
    assert ordering.hotspot_key("src/core.py", 100) < ordering.hotspot_key(
        "src/other.py", 99)


def test_the_keys_are_total_so_two_runs_cannot_disagree():
    same_line = [Row(file="src/a.py", name="Tick", start_line=5,
                     qualified_name="Runner.Tick", decl_hash="sha256:a"),
                 Row(file="src/a.py", name="Tick", start_line=5,
                     qualified_name="Runner.Tick", decl_hash="sha256:b")]
    keys = [ordering.symbol_key(row, "Tick") for row in same_line]
    assert len(set(keys)) == 2, "rows that differ only by declaration hash tie"


# ---------------------------------------------------------------------------
# the CLI surfaces on a fixture that CANNOT pass by accident
# ---------------------------------------------------------------------------
#
# The test file declares the shared name three times and the generated bundle
# mentions it as often as the source does; the test file also owns a helper
# with far more references than the source file's own name. A list ordered by
# count, by path or by insertion order therefore disagrees with every answer
# asserted below -- only the class weight can produce it.

SOURCE_FILE = "pkg/sim.py"
SOURCE_TEXT = "def Tick():\n    return 1\n"

TEST_FILE = "tests/test_sim.py"
TEST_TEXT = (
    "def Tick(first=1):\n"
    "    return first\n"
    "\n"
    "\n"
    "def Tick(second=2):\n"
    "    return second\n"
    "\n"
    "\n"
    "def Tick(third=3):\n"
    "    return third\n"
    "\n"
    "\n"
    "def exercise():\n"
    "    Tick()\n"
    "    Tick()\n"
    "    Tick()\n"
    + "".join("    shared_fixture()\n" for _ in range(20))
)

CONFTEST_FILE = "tests/conftest.py"
CONFTEST_TEXT = "def shared_fixture():\n    return 1\n"

APP_FILE = "app.py"
APP_TEXT = "def drive():\n    Tick()\n"

DOC_FILE = "docs/design.md"
DOC_TEXT = ("# Design\n\n"
            "Tick is the simulation step. Tick drives every system, and\n"
            "Tick is measured per frame. A Tick is never skipped.\n")

BUNDLE_FILE = "dist/bundle.min.js"
BUNDLE_TEXT = "function Tick(){\nTick();\nTick();\nTick();\n}\n"


class _StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query):
        return query

    def close(self):
        pass


class _StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, *args, **kwargs):
        return 0

    def count_points(self, name):
        return 0

    def search(self, *args, **kwargs):
        return []

    def swap_collection(self, *args, **kwargs):
        return None


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=1, watch_sweep_interval=3600)
    project = tmp_path / "proj"
    files = {
        SOURCE_FILE: SOURCE_TEXT, TEST_FILE: TEST_TEXT,
        CONFTEST_FILE: CONFTEST_TEXT, APP_FILE: APP_TEXT,
        DOC_FILE: DOC_TEXT, BUNDLE_FILE: BUNDLE_TEXT,
    }
    for name, text in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    core = Core(cfg)
    core.embedder = _StubEmbedder()  # type: ignore[assignment]
    core.store = _StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="ordering")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, reg, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


def _json(project, *args) -> dict:
    result = runner.invoke(app, [*args, "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_the_fixture_really_puts_more_hits_in_the_demoted_classes(rig):
    """Otherwise every assertion below could pass for the wrong reason."""
    core, _reg, entry, _project = rig
    m = core.manifest_for(entry.slug)
    try:
        per_file: dict[str, int] = {}
        for row in m.refs_to("Tick", limit=1000):
            per_file[row["file"]] = per_file.get(row["file"], 0) + 1
        assert per_file.get(TEST_FILE, 0) > per_file.get(APP_FILE, 0), per_file
        declarations = [r.file for r in m.definitions_of("Tick")]
        assert declarations.count(TEST_FILE) > declarations.count(
            SOURCE_FILE), declarations
    finally:
        m.close()


def test_find_definition_puts_the_source_declaration_first(rig):
    """Three test declarations and one generated one lose to one source row."""
    _core, _reg, _entry, project = rig
    data = _json(project, "find-definition", "Tick")
    files = [d["file"] for d in data["definitions"]]
    assert files.count(TEST_FILE) >= 3, files
    assert files[0] == SOURCE_FILE, files
    weights = [ordering.class_weight(f) for f in files]
    assert weights == sorted(weights), files


def test_find_definition_is_deterministic(rig):
    _core, _reg, _entry, project = rig
    first = _json(project, "find-definition", "Tick")
    second = _json(project, "find-definition", "Tick")
    assert first == second


def test_find_references_puts_source_rows_before_more_numerous_test_rows(rig):
    """The source has ONE reference; the test file has several. It still leads."""
    _core, _reg, _entry, project = rig
    data = _json(project, "find-references", "Tick", "--limit", "100")
    files = [row["file"] for row in data["references"]]
    assert files.count(TEST_FILE) > files.count(APP_FILE), files
    assert files[0] == APP_FILE, files
    weights = [ordering.class_weight(name) for name in files]
    assert weights == sorted(weights), files
    # An ordering, not a filter: every row is returned.
    assert data["total"] == len(data["references"])


def test_find_references_is_deterministic(rig):
    _core, _reg, _entry, project = rig
    first = _json(project, "find-references", "Tick", "--limit", "100")
    second = _json(project, "find-references", "Tick", "--limit", "100")
    assert first == second


def test_find_callers_puts_source_call_sites_ahead_of_the_test_harness(rig):
    _core, _reg, _entry, project = rig
    data = _json(project, "find-callers", "Tick")
    roots = data["roots"]
    assert roots and roots[0]["file"] == SOURCE_FILE, roots
    files = [node["file"] for node in roots[0]["children"]]
    assert files[0] == APP_FILE, files
    assert TEST_FILE in files, files
    weights = [ordering.class_weight(name) for name in files]
    assert weights == sorted(weights), files

