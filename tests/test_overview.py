"""Overview command tests (navigation work item).

Fixture repo with known languages/entry points/tests/config; the snapshot
test pins the section layout, the cap test pins --max-lines, and the
ordering tests pin stability. All offline.
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
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()


def _write(project, rel: str, text: str) -> None:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


PY_APP = ("from pkg.helpers import refresh_token\n"
          "def main():\n"
          "    refresh_token()\n"
          "    return 0\n")
PY_HELPERS = ("def refresh_token():\n    return 1\n")
PY_TEST = ("import app\n"
           "def test_main():\n    assert app.main() == 0\n")
TS_LIB = "export function helper() { return 1; }\n"
TS_APP = 'import { helper } from "./lib";\nexport function go() { helper(); }\n'


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    _write(project, "app.py", PY_APP)
    _write(project, "pkg/helpers.py", PY_HELPERS)
    _write(project, "tests/test_app.py", PY_TEST)
    _write(project, "ui/app.ts", TS_APP)
    _write(project, "ui/lib.ts", TS_LIB)
    _write(project, "pyproject.toml",
           "[project]\nname = 'proj'\n\n[project.scripts]\nproj = 'app:main'\n")
    _write(project, "package.json",
           '{"main": "ui/app.ts"}\n')
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
    entry = reg.add(str(project), name="ovproj")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    return core, reg, entry, project


def test_overview_snapshot(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project)])
    assert result.exit_code == 0, result.output
    out = result.output
    # Sections, in stable order.
    assert out.startswith("project: proj — ")
    assert "languages:" in out
    assert "python:" in out and "typescript:" in out
    assert "directories:" in out
    assert "pkg/" in out and "ui/" in out and "tests/" in out
    assert "entry points:" in out
    assert "script: proj" in out                 # pyproject [project.scripts]
    assert "package.json main: ui/app.ts" in out
    assert "app.py:main" in out                  # main symbol
    assert "tests: 1 files" in out or "tests: 1 file" in out
    assert "config/build:" in out
    assert "pyproject.toml" in out and "package.json" in out
    assert "hotspots (fan-in):" in out
    assert "app.py" in out                       # imported by... helpers? no:
    # fan-in: ui/app.ts is imported by... nothing; pkg/helpers.py imported by
    # app.py; ui/lib.ts imported by ui/app.ts.
    assert "ui/lib.ts" in out


def test_overview_max_lines_cap(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--max-lines", "5"])
    assert result.exit_code == 0
    # The cap applies to the rendered overview on stdout; the coverage line
    # an incomplete index adds rides stderr beside the staleness line.
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) <= 5


def test_overview_stable_ordering(rig):
    core, reg, entry, project = rig
    r1 = runner.invoke(app, ["overview", "--project", str(project)]).output
    r2 = runner.invoke(app, ["overview", "--project", str(project)]).output
    assert r1 == r2  # deterministic for fixed data


def test_overview_path_prefix(rig):
    core, reg, entry, project = rig
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--path-prefix", "ui/"])
    assert result.exit_code == 0
    assert "typescript" in result.output
    assert "python" not in result.output.split("languages:")[1] \
        if "languages:" in result.output else True


def test_overview_json(rig):
    core, reg, entry, project = rig
    import json
    result = runner.invoke(app, ["overview", "--project", str(project),
                                 "--json"])
    data = json.loads(result.output)
    assert data["project"] == "proj"
    assert any(l["language"] == "python" for l in data["languages"])
    assert data["hotspots"]["fan_in"]


def test_hotspot_fan_in_counts_the_whole_store_not_a_sample(rig):
    """Hotspots are computed by the store, not read off a 5,000-row sample.

    The fixture gains 5,100 references to one defined name; the reported
    fan-in must move by exactly 5,100. A sampled scan could move it by at
    most 5,000 — and, with no ORDER BY beneath it, not reliably even that.
    """
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        from code_indexer.overview import build_overview

        def helper_fan_in() -> int | None:
            data = build_overview(m, str(project))
            for h in data["hotspots"]["fan_in"]:
                if h["file"] == "pkg/helpers.py":
                    return h["fan_in"]
            return None

        baseline = helper_fan_in()
        assert baseline is not None
        rows = [(i + 1, f"call_{i}", "refresh_token", "call", None)
                for i in range(5100)]
        m.replace_file_refs("generated/callers.py", rows, "ast")
        m.resolve_ref_targets()
        assert helper_fan_in() == baseline + 5100
        # ...and the aggregate is the same one the manifest computes.
        assert dict(m.file_fan_in())["pkg/helpers.py"] == baseline + 5100
    finally:
        m.close()


def test_path_prefix_scopes_by_resolved_target_and_never_reattributes(rig):
    """A prefix filters by the RESOLVED target; it never re-decides ownership.

    The shipped rule: a path prefix scopes the answer to references whose
    resolved target lies inside the prefix. It never re-attributes a
    reference to a definition outside it, and it never revives the old
    per-name owner. So a reference from outside the prefix still counts
    toward an in-prefix target, while a name defined on both sides of the
    prefix -- whose references all resolve to the source definition outside
    it -- reports nothing inside the prefix. The empty answer for `docs/`
    below is that second rule, measured and correct, not a dropped count.
    """
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        from code_indexer.manifest import ManifestFile, SymbolRow
        from code_indexer.overview import build_overview

        def add_file(path: str) -> None:
            m.upsert_files([ManifestFile(path=path, content_hash=path,
                                         size=1, chunk_count=1,
                                         status="indexed", loc=5)])

        for path, name in (("beta.py", "shared_topic"),
                           ("docs/alpha.py", "shared_topic"),
                           ("app/topic.py", "alpha_topic")):
            add_file(path)
            m.replace_file_symbols(path, [SymbolRow(
                file=path, name=name, symbol_type="function",
                start_line=1, end_line=2, source="ast")])
        for i in range(5):
            path = f"users/u{i}.py"
            add_file(path)
            m.replace_file_refs(
                path, [(1, f"caller_{i}", "alpha_topic", "call", None),
                       (2, f"caller_{i}", "shared_topic", "call", None)],
                "ast")
        m.resolve_ref_targets()

        def scoped_fan(prefix: str) -> dict[str, int]:
            return {h["file"]: h["fan_in"] for h in
                    build_overview(m, str(project), path_prefix=prefix)
                    ["hotspots"]["fan_in"]}

        # A reference from OUTSIDE the prefix to an in-prefix definition is
        # counted: the prefix scopes targets, not the reference files.
        assert scoped_fan("app/") == {"app/topic.py": 5}, scoped_fan("app/")

        # The shared name's references all resolve to `beta.py`, outside the
        # prefix, so the in-prefix definition of the same name reports none:
        # the prefix filters that target out and does not re-attribute.
        assert scoped_fan("docs/") == {}, scoped_fan("docs/")

        # Unscoped, each definition keeps only what resolved to it.
        full = dict(m.file_fan_in())
        assert full.get("beta.py") == 5, full
        assert full.get("app/topic.py") == 5, full
        assert full.get("docs/alpha.py", 0) == 0, full
    finally:
        m.close()


def test_unattributed_splits_names_defined_nowhere_from_ties(rig):
    """One number for "defined nowhere" and "tied" cannot be read as either.

    Three references shapes, all in one store: a name defined nowhere, a name
    defined twice with no same-file match (a tie), and a name resolved to its
    single definition. The first two are the split counts and sum to the
    total; the resolved one is credited to its definition and moves neither.
    """
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        from code_indexer.manifest import ManifestFile, SymbolRow
        from code_indexer.overview import build_overview, format_overview

        before = m.unattributed_ref_counts()
        before_in_users = m.unattributed_ref_counts("users/")

        def add_file(path: str) -> None:
            m.upsert_files([ManifestFile(path=path, content_hash=path,
                                         size=1, chunk_count=1,
                                         status="indexed", loc=5)])

        for path, name in (("core/settled.py", "settled_name"),
                           ("core/left.py", "tied_name"),
                           ("core/right.py", "tied_name")):
            add_file(path)
            m.replace_file_symbols(path, [SymbolRow(
                file=path, name=name, symbol_type="function",
                start_line=1, end_line=2, source="ast")])
        add_file("users/a.py")
        rows = [(line, "caller", name, "call", None)
                for line, name in (
                    [(i + 1, "settled_name") for i in range(4)]
                    + [(i + 10, "tied_name") for i in range(3)]
                    + [(i + 20, "nowhere_name") for i in range(2)])]
        m.replace_file_refs("users/a.py", rows, "ast")
        m.resolve_ref_targets()

        after = m.unattributed_ref_counts()
        delta = {k: after[k] - before[k] for k in after}
        assert delta == {"unattributed": 5, "unresolved": 2,
                         "ambiguous": 3}, delta
        # The three counts differ and the two reasons sum to the total.
        assert len({delta["unresolved"], delta["ambiguous"],
                    delta["unattributed"]}) == 3
        assert (delta["unresolved"] + delta["ambiguous"]) == \
            delta["unattributed"]
        # The resolved name's four references are credited, not counted here.
        assert m.unattributed_ref_count() == after["unattributed"]
        assert m.unattributed_ref_count("users/") == \
            (before_in_users["unattributed"] + 5)

        data = build_overview(m, str(project))
        hotspots = data["hotspots"]
        assert hotspots["unresolved"] == after["unresolved"], hotspots
        assert hotspots["ambiguous"] == after["ambiguous"], hotspots
        assert hotspots["unattributed"] == (hotspots["unresolved"]
                                           + hotspots["ambiguous"])
        text = format_overview(data, max_lines=200)
        assert (f"{hotspots['unattributed']} references credited to no file "
                f"({hotspots['unresolved']} names defined nowhere, "
                f"{hotspots['ambiguous']} tied between definitions)") in text
    finally:
        m.close()


def test_manifest_loc_language_columns(rig):
    core, reg, entry, project = rig
    m = core.manifest_for(entry.slug)
    try:
        f = m.get_file("app.py")
        assert f.language == "python"
        assert f.loc == 4  # approx: max end_line
        assert m.get_file("ui/lib.ts").language == "typescript"
    finally:
        m.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))