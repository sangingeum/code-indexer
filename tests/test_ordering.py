"""The ordering policy: one classifier, one key order, no ties by accident.

The CLI-level proofs (a fixture with MORE references in the test file, a
determinism re-run) live beside the surfaces they exercise; this module pins the
classifier and the key shape those surfaces share.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer import ordering  # noqa: E402


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
