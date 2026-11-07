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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
import code_indexer.graph as graph  # noqa: E402
from code_indexer import ordering  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


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
    # The documents every repository has, spelled without an extension: no
    # suffix rule can see them, so a basename set has to.
    "LICENSE",
    "LICENSE.md",
    "LICENCE",
    "COPYING",
    "NOTICE",
    "AUTHORS",
    "README",
    "CHANGELOG",
    "CONTRIBUTING",
    "pkgs/core/LICENSE",
])
def test_documents_are_docs(path):
    assert ordering.file_class(path) == ordering.DOCS, path


def test_an_unrecognised_extension_on_a_document_name_stays_source():
    """The basename set is exact: it classifies a document, not a guess."""
    assert ordering.file_class("README.weird") == ordering.SOURCE
    assert ordering.file_class("license.py") == ordering.SOURCE


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


def test_the_class_ladder_is_source_example_test_doc_artifact():
    assert ordering.CLASS_ORDER == (ordering.SOURCE, ordering.EXAMPLES,
                                    ordering.TESTS, ordering.DOCS,
                                    ordering.GENERATED)
    weights = [ordering.class_weight(p) for p in (
        "src/app.py", "examples/demo.py", "tests/test_app.py",
        "docs/design.md", "dist/app.min.js")]
    assert weights == [0, 1, 2, 3, 4], weights


@pytest.mark.parametrize("path", [
    "examples/planner.py",
    "examples/supervisor/main.py",
    "sample/quickstart.py",
    "packages/api/demo/server.py",
])
def test_sample_code_is_examples(path):
    assert ordering.file_class(path) == ordering.EXAMPLES, path


def test_a_test_inside_a_sample_tree_is_still_a_test():
    """Rules apply in order, so the more specific rung wins."""
    assert ordering.file_class("examples/test_planner.py") == ordering.TESTS


def test_a_project_may_add_directory_names_to_a_rung(tmp_path):
    """The ladder is the default, not the whole policy: a project extends it."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.code-indexer.ordering]\n"
        'docs = ["manuals"]\n'
        'examples = ["recipes"]\n'
        'tests = ["harness"]\n')
    names = ordering.load_class_names(str(tmp_path))
    assert ordering.file_class("manuals/chapter.py", names) == ordering.DOCS
    assert ordering.file_class("recipes/tour.py", names) == ordering.EXAMPLES
    assert ordering.file_class("harness/util.py", names) == ordering.TESTS
    # Additive, never subtractive: a rung the project did not name survives.
    assert ordering.file_class("tests/test_app.py", names) == ordering.TESTS


def test_a_missing_or_malformed_ordering_table_yields_the_builtin_ladder(
        tmp_path):
    assert ordering.load_class_names(str(tmp_path)) == \
        ordering.DEFAULT_CLASS_NAMES
    (tmp_path / "pyproject.toml").write_text("this is not toml [")
    assert ordering.load_class_names(str(tmp_path)) == \
        ordering.DEFAULT_CLASS_NAMES


def test_the_sql_function_reads_the_same_ladder_as_the_python_keys(tmp_path):
    """One classifier, two layers: a capped read cannot disagree with a key."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.code-indexer.ordering]\n" 'docs = ["manuals"]\n')
    names = ordering.load_class_names(str(tmp_path))
    m = Manifest(":memory:", class_names=names)
    try:
        weights = m._conn.execute(
            "SELECT file_class_weight('manuals/chapter.py'),"
            " file_class_weight('src/app.py')").fetchone()
    finally:
        m.close()
    assert list(weights) == [ordering.class_weight("manuals/chapter.py", names),
                             ordering.class_weight("src/app.py", names)]
    assert weights[0] == ordering.CLASS_WEIGHT[ordering.DOCS]


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


def test_the_edge_key_is_total_so_two_runs_cannot_disagree():
    first = ordering.edge_key("src/a.py", "Runner.Tick", 5, "call", "ast",
                              "runner", ordering.DEFAULT_CLASS_NAMES)
    second = ordering.edge_key("src/a.py", "Runner.Tick", 5, "call", "ast",
                               "runner", ordering.DEFAULT_CLASS_NAMES)
    assert first == second
    other = ordering.edge_key("src/a.py", "Runner.Tick", 5, "call", "ast",
                              "other")
    assert first != other, "different receivers must not tie"


# ---------------------------------------------------------------------------
# the CLI surfaces on a fixture that CANNOT pass by accident
# ---------------------------------------------------------------------------
#
# The test file declares the shared name three times and the generated bundle
# mentions it as often as the source does; the test file also owns a helper
# with far more references than the source file's own name. A list ordered by
# count, by path or by insertion order therefore disagrees with every answer
# asserted below -- only the class weight can produce it.

SOURCE_FILE = "src/sim.py"
SOURCE_TEXT = "def Tick():\n    return 1\n\n\ndef reset():\n    return 0\n"

TEST_FILE = "spec/test_sim.py"
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
    + "".join("    shared_fixture()\n" for _ in range(30))
)

CONFTEST_FILE = "spec/conftest.py"
CONFTEST_TEXT = "def shared_fixture():\n    return 1\n"

APP_FILE = "web/app.py"
APP_TEXT = ("def drive():\n"
            "    Tick()\n"
            "    reset()\n"
            "    reset()\n"
            "    reset()\n")

DOC_FILE = "docs/design.md"
DOC_TEXT = ("# Design\n\n"
            "Tick is the simulation step. Tick drives every system, and\n"
            "Tick is measured per frame. A Tick is never skipped.\n")

BUNDLE_FILE = "dist/bundle.min.js"
BUNDLE_TEXT = "function Tick(){\nTick();\nTick();\nTick();\n}\n"

# The document every repository has, spelled without an extension, and wordy
# enough to win a size list and to accumulate identifier mentions: class, not
# size, is what keeps it out of the orientation answer.
LICENCE_FILE = "LICENSE"
LICENCE_TEXT = ("Copyright (c) the authors.\n\n"
                + "Tick is a simulation step, and this licence mentions Tick "
                  "only because it speaks about the software.\n" * 8)

# A named call site and a module-level mention of the same name, both in
# source: the identity key is the only thing that orders one above the other.
LOOSE_FILE = "src/loose.py"
LOOSE_TEXT = ('"""Tick references with no enclosing declaration."""\n'
              "\n"
              "Tick()\n")

# Importers of the same module, one source and one test: both are level 1 of
# `deps`, and only the class key keeps the production importer first. The
# paths are chosen so that PATH order would give the reverse (examples, spec,
# web): `web/uses_sim.py` is spelled after `spec/uses_sim.py`, so an
# alphabetically sorted level puts the test importer first.
USES_FILE = "web/uses_sim.py"
USES_TEXT = "from src.sim import Tick\n\n\ndef use():\n    Tick()\n"
TESTS_USES_FILE = "spec/uses_sim.py"
TESTS_USES_TEXT = "from src.sim import Tick\n\n\ndef check():\n    Tick()\n"

# Code shipped to be read: after source, before tests.
EXAMPLE_FILE = "examples/sim_demo.py"
EXAMPLE_TEXT = "from src.sim import Tick\n\n\ndef demo():\n    Tick()\n"


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
        LICENCE_FILE: LICENCE_TEXT, LOOSE_FILE: LOOSE_TEXT,
        USES_FILE: USES_TEXT, TESTS_USES_FILE: TESTS_USES_TEXT,
        EXAMPLE_FILE: EXAMPLE_TEXT,
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


def test_overview_hotspots_promote_source_over_a_larger_test_fixture(rig):
    """The test fixture has MORE fan-in than a source file, and still loses.

    `spec/conftest.py` owns `shared_fixture`, referenced 30 times from the
    test file, and `dist/bundle.min.js` owns another `Tick`: both are counted
    at the file that defines the name, and both rank below the source that
    owns `Tick`. The fixture's raw count is therefore the largest in the
    store, so a list sorted by count would lead with it; only the class
    ladder puts the source first. Before per-definition attribution every
    `Tick` reference was credited to the lexicographically first definer
    (`dist/bundle.min.js`) and the fixture out-counted the source; the counts
    moved with the fix, the ordering rule did not.
    """
    core, _reg, entry, project = rig
    from code_indexer.overview import build_overview

    m = core.manifest_for(entry.slug)
    try:
        data = build_overview(m, str(project))
    finally:
        m.close()
    fan = {row["file"]: row["fan_in"] for row in data["hotspots"]["fan_in"]}
    order = [row["file"] for row in data["hotspots"]["fan_in"]]
    assert fan[CONFTEST_FILE] == 30, fan
    # The discriminator: by raw count the fixture leads, by class it does not.
    assert fan[CONFTEST_FILE] > fan[SOURCE_FILE], fan
    count_order = [row["file"] for row in
                   sorted(data["hotspots"]["fan_in"],
                          key=lambda d: (-d["fan_in"], d["file"]))]
    assert count_order[0] == CONFTEST_FILE, count_order
    assert data["hotspots"]["fan_in"][0]["file"] == SOURCE_FILE, fan
    assert order != count_order, order
    assert order.index(SOURCE_FILE) < order.index(CONFTEST_FILE), order
    # The class ladder holds across the whole list: no test, document or
    # artifact ranks above a source file, whatever its count.
    weights = [ordering.class_weight(path) for path in order]
    assert weights == sorted(weights), order
    size = {row["file"]: row["loc"] for row in data["hotspots"]["size"]}
    assert size[TEST_FILE] > size[SOURCE_FILE], size
    assert data["hotspots"]["size"][0]["file"] == SOURCE_FILE, size


def test_overview_hotspots_are_deterministic(rig):
    _core, _reg, _entry, project = rig
    first = _json(project, "overview")
    second = _json(project, "overview")
    assert first["hotspots"] == second["hotspots"]


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


def test_find_references_puts_a_named_call_site_before_a_module_level_mention(
        rig):
    """The identity key is the ONLY thing separating these two rows.

    Both sit in source, and `src/loose.py` sorts first by path: without the
    enclosing-symbol key the module-level mention would lead the answer.
    """
    _core, _reg, _entry, project = rig
    rows = _json(project, "find-references", "Tick", "--limit", "100")[
        "references"]
    files = [row["file"] for row in rows]
    assert LOOSE_FILE in files, files
    assert files[0] != LOOSE_FILE, files
    named = [i for i, row in enumerate(rows)
             if row["file"] in (APP_FILE, USES_FILE)]
    loose = [i for i, row in enumerate(rows) if row["file"] == LOOSE_FILE]
    assert named, files
    assert max(named) < min(loose), files
    assert all(row["from_symbol"] is None for row in rows
               if row["file"] == LOOSE_FILE)


def test_find_references_ranks_shipped_samples_between_source_and_tests(rig):
    _core, _reg, _entry, project = rig
    files = [row["file"] for row in _json(
        project, "find-references", "Tick", "--limit", "100")["references"]]
    assert EXAMPLE_FILE in files, files
    sample = files.index(EXAMPLE_FILE)
    assert all(sample > i for i, name in enumerate(files)
               if name in (APP_FILE, USES_FILE)), files
    assert all(sample < i for i, name in enumerate(files)
               if name in (TEST_FILE, DOC_FILE)), files


def test_the_licence_document_does_not_win_orientation(rig):
    """Larger than the source file and full of identifier mentions, still
    outside both hotspot lists: the class, not the count, decides."""
    core, _reg, entry, project = rig
    from code_indexer.overview import build_overview

    m = core.manifest_for(entry.slug)
    try:
        data = build_overview(m, str(project))
        sizes = {path: f.loc or 0 for path, f in m.all_files().items()}
    finally:
        m.close()
    assert sizes[LICENCE_FILE] > sizes[SOURCE_FILE], sizes
    assert ordering.file_class(LICENCE_FILE) == ordering.DOCS
    fan = [row["file"] for row in data["hotspots"]["fan_in"]]
    size = [row["file"] for row in data["hotspots"]["size"]]
    assert fan[0] == SOURCE_FILE, fan
    assert size[0] == SOURCE_FILE, size
    # Fan-in attributes a name to the file that DEFINES it, and the licence
    # defines nothing, so it cannot appear there at all.
    assert LICENCE_FILE not in fan, fan
    # Size would promote it -- it is the largest file in the tree -- and the
    # class still ranks it below every source row, including the smallest.
    assert size[0] == SOURCE_FILE, size
    assert size.index(LICENCE_FILE) > size.index(SOURCE_FILE), size


def test_deps_orders_each_level_by_class_before_the_node_cap(rig):
    """One source importer, one sample, one test importer: path order puts
    `spec/` first, so only the class key produces this answer -- and with one
    slot left it decides WHICH importer survives the cap."""
    core, _reg, entry, _project = rig
    walk = graph.deps_for_file(core, entry.slug, SOURCE_FILE, direction="in")
    level = walk["in"][1]
    assert set(level) == {USES_FILE, EXAMPLE_FILE, TESTS_USES_FILE}, level
    assert level == [USES_FILE, EXAMPLE_FILE, TESTS_USES_FILE], level

    capped = graph.deps_for_file(core, entry.slug, SOURCE_FILE,
                                 direction="in", max_nodes=2)
    assert capped["in"][1] == [USES_FILE], capped
    assert capped["omitted"] == 2, capped
    assert capped["truncated"] is True, capped

