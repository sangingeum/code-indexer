"""Store-read completeness tests (issue #22).

The store layer hands over everything it holds: `callers_of`,
`callees_of_file_symbol` and `import_rows` have NO default cap, and the
per-file fan-in aggregate is computed by SQLite, not sampled from a row scan.
Every pin here fails against the capped reads that used to sit under the graph
walks and under `overview`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer.manifest import Manifest, ManifestFile, SymbolRow  # noqa: E402

# One more than the old default of 200.
OVER_CAP = 250


def _add_file(m: Manifest, path: str, *, symbols: list[SymbolRow] | None = None,
              refs: list[tuple] | None = None,
              imports: list[tuple] | None = None,
              confidence: str = "ast") -> None:
    m.upsert_files([ManifestFile(path=path, content_hash=path, size=1,
                                 chunk_count=1, status="indexed", loc=10)])
    if symbols:
        m.replace_file_symbols(path, symbols)
    if refs:
        m.replace_file_refs(path, refs, confidence)
    if imports:
        m.replace_file_imports(path, imports)


def _definition(path: str, name: str) -> SymbolRow:
    return SymbolRow(file=path, name=name, symbol_type="function",
                     start_line=1, end_line=2, source="ast")


def _manifest(tmp_path) -> Manifest:
    return Manifest(str(tmp_path / "manifest.db"))


def test_callers_of_returns_the_whole_store_by_default(tmp_path):
    m = _manifest(tmp_path)
    try:
        _add_file(m, "target.py", symbols=[_definition("target.py", "pairA")])
        for i in range(OVER_CAP):
            path = f"caller_{i:03d}.py"
            _add_file(m, path, refs=[(i + 1, f"call_{i}", "pairA", "call",
                                      None)])
        rows = m.callers_of("pairA")
        assert len(rows) == OVER_CAP, len(rows)
        # Deterministic order, so a capped read is at least a prefix.
        assert [r["file"] for r in rows] == sorted(r["file"] for r in rows)
        # An explicit limit stays usable for a caller that wants one.
        assert len(m.callers_of("pairA", limit=200)) == 200
        assert m.callers_of("pairA", limit=200) == rows[:200]
    finally:
        m.close()


def test_callees_of_file_symbol_returns_the_whole_fan_out(tmp_path):
    m = _manifest(tmp_path)
    try:
        refs = [(i + 1, "hub", f"target_{i:03d}", "call", None)
                for i in range(OVER_CAP)]
        _add_file(m, "hub.py", symbols=[_definition("hub.py", "hub")],
                  refs=refs)
        by_symbol = m.callees_of_file_symbol("hub.py", "hub")
        assert len(by_symbol) == OVER_CAP, len(by_symbol)
        assert [r["line"] for r in by_symbol] == sorted(r["line"]
                                                        for r in by_symbol)
        # The whole-file form (`symbol=None`) is the same store read.
        whole_file = m.callees_of_file_symbol("hub.py", None)
        assert len(whole_file) == OVER_CAP, len(whole_file)
        # Explicit limits remain honored.
        assert len(m.callees_of_file_symbol("hub.py", "hub", limit=10)) == 10
        assert len(m.callees_of_file_symbol("hub.py", None, limit=10)) == 10
    finally:
        m.close()


def test_import_rows_is_complete_and_ordered(tmp_path):
    m = _manifest(tmp_path)
    try:
        paths = [f"pkg/mod_{i:02d}.py" for i in range(30)]
        # Inserted in reverse: insertion order is not the answer, and is not
        # even a stable subset when a read is capped.
        for path in reversed(paths):
            _add_file(m, path, imports=[("pkg.base", "pkg/base.py",
                                         "python", 1)])
        rows = m.import_rows()
        assert len(rows) == len(paths), len(rows)
        assert [r["file"] for r in rows] == sorted(paths)
        # A capped read is a deterministic prefix of the full order.
        assert m.import_rows(limit=5) == rows[:5]
    finally:
        m.close()


def test_callers_of_order_is_total_inside_a_one_line_tie_group(tmp_path):
    """A reference tie group on ONE line comes back in the key's order.

    `refs` is keyed `(file, line, to_name, relationship)`, so one line can
    carry several rows; a live index has 73,911 such lines. `ORDER BY file,
    line` left their order to the query plan, so a cut inside the group was
    not a reproducible prefix. The key now names the primary key (plus the
    receiver) and the rows are inserted in the REVERSE of that order, so a
    plan-order answer cannot pass.
    """
    m = _manifest(tmp_path)
    try:
        _add_file(m, "target.py",
                  symbols=[_definition("target.py", "target_fn")])
        _add_file(m, "one_user.py", refs=[
            (1, "use_member", "target_fn", "member", "obj"),
            (1, "use_type", "target_fn", "type", None),
            (1, "use_call", "target_fn", "call", None),
            (2, "later", "target_fn", "call", None),
        ])
        rows = m.callers_of("target_fn")
        assert [(r["line"], r["relationship"]) for r in rows] == [
            (1, "call"), (1, "member"), (1, "type"), (2, "call")], rows
        # Cuts inside the tie group are still prefixes of the full read.
        assert m.callers_of("target_fn", limit=1) == rows[:1]
        assert m.callers_of("target_fn", limit=2) == rows[:2]
        assert m.callers_of("target_fn", limit=3) == rows[:3]
    finally:
        m.close()


def test_import_rows_order_is_total_inside_a_one_line_tie_group(tmp_path):
    """`imports` is keyed `(file, raw_target, line)`, so two rows can share
    one `(file, line)` (3 such groups on one live index). Inserted in the
    reverse of the key's order: only a total key returns the same prefix.
    """
    m = _manifest(tmp_path)
    try:
        _add_file(m, "pkg/a.py", imports=[
            ("pkg.zeta", "pkg/zeta.py", "python", 1),
            ("pkg.middle", None, "python", 1),
            ("pkg.alpha", "pkg/alpha.py", "python", 1),
            ("pkg.alpha", "pkg/alpha.py", "python", 2),
        ])
        rows = m.import_rows()
        assert [(r["line"], r["raw_target"]) for r in rows] == [
            (1, "pkg.alpha"), (1, "pkg.middle"), (1, "pkg.zeta"),
            (2, "pkg.alpha")], rows
        assert m.import_rows(limit=1) == rows[:1]
        assert m.import_rows(limit=2) == rows[:2]
    finally:
        m.close()


def test_definitions_of_returns_every_definition_row(tmp_path):
    """The definition-roots read has no browse cap (issue #22).

    Forty definitions of one name is not a browse; a caller that wants a
    bound asks for one, and the order is total so a capped read is a
    reproducible prefix.
    """
    m = _manifest(tmp_path)
    try:
        for i in range(40):
            path = f"defs/mod_{i:02d}.py"
            _add_file(m, path, symbols=[_definition(path, "shared")])
        rows = m.definitions_of("shared")
        assert len(rows) == 40, len(rows)
        assert [r.file for r in rows] == sorted(r.file for r in rows)
        # The browse cap is still available to a caller that names one.
        assert len(m.find_symbols("shared", limit=25)) == 25
        assert m.find_symbols("shared", limit=None) == rows
    finally:
        m.close()


def test_file_fan_in_matches_a_manual_per_file_count(tmp_path):
    """The aggregate equals a hand count of the same store, including the
    rules: imports into a file count; a reference from the file itself does
    not; a name defined in two files belongs to the first definition."""
    m = _manifest(tmp_path)
    try:
        _add_file(m, "dup_a.py", symbols=[_definition("dup_a.py", "dup_fn")])
        _add_file(m, "dup_b.py", symbols=[_definition("dup_b.py", "dup_fn")])
        _add_file(m, "hot.py", symbols=[_definition("hot.py", "hot_fn")])
        _add_file(m, "cold.py", symbols=[_definition("cold.py", "cold_fn")])
        _add_file(m, "imported.py")

        refs_by_file: dict[str, list[tuple]] = {}
        # Insertion order puts the SMALL counts first and the big one last, so
        # a sample taken from the front of the table cannot produce the answer.
        refs_by_file["cold.py"] = [(9, "cold_fn", "cold_fn", "call", None)]
        refs_by_file["a.py"] = [(1, "use_cold", "cold_fn", "call", None),
                                (2, "use_dup", "dup_fn", "call", None)]
        refs_by_file["b.py"] = [(1, "use_cold", "cold_fn", "call", None),
                                (2, "use_dup", "dup_fn", "call", None)]
        for i in range(OVER_CAP):
            refs_by_file.setdefault(f"users/hot_{i:03d}.py", []).append(
                (1, f"use_hot_{i}", "hot_fn", "call", None))
        for path, refs in refs_by_file.items():
            _add_file(m, path, refs=refs)

        _add_file(m, "import_one.py",
                  imports=[("imported", "imported.py", "python", 1)])
        _add_file(m, "import_two.py",
                  imports=[("imported", "imported.py", "python", 1),
                           ("imported", "imported.py", "python", 2)])
        _add_file(m, "self_import.py",
                  imports=[("self_import", "self_import.py", "python", 1)])

        # Manual count, from the fixture's own declared rows.
        expected: dict[str, int] = {}
        for path, refs in refs_by_file.items():
            for (_line, _frm, to_name, _rel, _recv) in refs:
                target = "dup_a.py" if to_name == "dup_fn" else \
                    {"hot_fn": "hot.py", "cold_fn": "cold.py"}[to_name]
                if path != target:
                    expected[target] = expected.get(target, 0) + 1
        expected["imported.py"] = 3  # two import rows + one

        got = dict(m.file_fan_in())
        assert got == expected, (got, expected)
        assert got["hot.py"] == OVER_CAP
        # Sorted by count desc, then file — the overview's slice is stable.
        assert m.file_fan_in()[0] == ("hot.py", OVER_CAP)
    finally:
        m.close()


def test_file_fan_in_does_not_stop_at_a_sample(tmp_path):
    """5,100 references to one file's symbol: a 5,000-row sample would report
    5,000 and, with no ORDER BY, not even a stable 5,000."""
    m = _manifest(tmp_path)
    try:
        _add_file(m, "defs/wide.py",
                  symbols=[_definition("defs/wide.py", "wide_fn")])
        _add_file(m, "defs/narrow.py",
                  symbols=[_definition("defs/narrow.py", "narrow_fn")])
        _add_file(m, "defs/narrow_user.py",
                  refs=[(1, "u", "narrow_fn", "call", None)])
        rows = [(i + 1, f"call_{i}", "wide_fn", "call", None)
                for i in range(5100)]
        _add_file(m, "defs/wide_user.py", refs=rows)
        counts = dict(m.file_fan_in())
        assert counts["defs/wide.py"] == 5100
        assert counts["defs/narrow.py"] == 1
    finally:
        m.close()
