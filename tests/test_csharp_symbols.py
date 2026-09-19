"""Fixture project for the C# method-recall regression test (written per-test)."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.indexer import Indexer  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


CS_SOURCE = """\
using System;

namespace Estate.Sim
{
    public class CrimeSurface
    {
        public bool EvaluateFastPathHostileAct(int pawnId)
        {
            return pawnId > 0;
        }

        private double ScoreThreat(int level)
        {
            return level * 1.5;
        }

        public CrimeSurface()
        {
        }
    }

    public enum ThreatKind { Low, High }

    public struct ThreatProfile { public int Level; }

    public interface IThreatSource { double Score(); }

    public record ThreatRecord(int Level);
}
"""


@pytest.fixture()
def cs_project():
    """A registered project whose only file is a C# source."""
    root = tempfile.mkdtemp(prefix="ci-cs-")
    src = os.path.join(root, "src")
    os.makedirs(src)
    with open(os.path.join(src, "CrimeSurface.cs"), "w") as f:
        f.write(CS_SOURCE)
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=tempfile.mkdtemp(prefix="ci-cs-idx-"),
                 stale_ttl=60, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    manifest = Manifest(os.path.join(cfg.index_root, "m.db"))
    Indexer(cfg, StubEmbedder(), StubStore()).index_project(root, "cs", manifest)
    yield manifest
    manifest.close()
    shutil.rmtree(cfg.index_root, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)


def test_csharp_method_symbols_found(cs_project):
    """Regression (solomon review, C# recall gap): find-symbol missed
    method-level symbols in C# — classes resolved, methods did not.
    Root cause: the csharp grammar's method_declaration / constructor
    nodes were not in the AST unit set, and class bodies were not walked
    for nested symbols, so methods never reached the manifest."""
    rows = cs_project.find_symbols("EvaluateFastPathHostileAct")
    assert rows, "method symbol missing from the manifest"
    row = rows[0]
    assert row.symbol_type == "method"
    assert row.source == "ast"
    assert row.file == "src/CrimeSurface.cs"
    assert row.start_line == 7


def test_csharp_constructor_and_private_method(cs_project):
    ctor = cs_project.find_symbols("CrimeSurface", symbol_type="method")
    assert ctor, "constructor missing"
    assert ctor[0].symbol_type == "method"
    private = cs_project.find_symbols("ScoreThreat")
    assert private and private[0].symbol_type == "method"


def test_csharp_type_kinds(cs_project):
    assert cs_project.find_symbols("CrimeSurface", symbol_type="class")
    assert cs_project.find_symbols("ThreatKind", symbol_type="enum")
    assert cs_project.find_symbols("ThreatProfile", symbol_type="struct")
    assert cs_project.find_symbols("IThreatSource", symbol_type="interface")
    assert cs_project.find_symbols("ThreatRecord")
    ns = cs_project.find_symbols("Estate.Sim", symbol_type="namespace")
    assert ns or cs_project.find_symbols("Sim", symbol_type="namespace")


def test_csharp_methods_not_swallowed_by_class(cs_project):
    """Methods must survive alongside their class (no swallow)."""
    rows = cs_project.find_symbols(None, file="src/CrimeSurface.cs", limit=50)
    names = {r.name for r in rows}
    assert "CrimeSurface" in names
    assert "EvaluateFastPathHostileAct" in names
    assert "ScoreThreat" in names
