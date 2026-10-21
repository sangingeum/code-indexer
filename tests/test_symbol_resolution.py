"""Symbol-mode resolution tests (issue #23).

An exact-name symbol read defaults to 25 rows. Resolving
``get-code-context --symbol`` by reading that capped list and filtering to the
requested file afterwards silently answers "not indexed in this file" for
every definition that sorts past the cap — the row is in the store and the
command denies it. ``evalharness._symbol_ranges`` had the same shape and
mis-scored eval runs on the same name population.

These fixtures put one name in more definitions than the cap, with the target
file's row deliberately past position 25, plus a name with more than five
definitions in a single file (the segment cap that used to shorten the
answer). All offline: the embedder/store seams are stubbed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from code_indexer import evalharness  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

# One shared name, defined at the first line of each early file. Ties on
# start_line are broken by file, so the late file — defined at a later line —
# always sorts after all of them, i.e. past a 25-row cap.
SHARED_NAME = "prepare"
EARLY_FILE_COUNT = 25
EARLY_FILE = "def prepare():\n    return 1\n"
LATE_FILE = "def unrelated():\n    return 0\n\n\ndef prepare():\n    return 2\n"

# One name with two rows in the same file whose ranges do NOT overlap: a
# duplicate method definition inside one class, both extracted as rows.
DISJOINT_FILE = (
    "class Holder:\n"
    "    def repeated(self):\n"
    "        return 1\n"
    "\n"
    "    def repeated(self):\n"
    "        return 2\n"
)


# One decorated definition long enough that the chunker splits it: the
# decorator line and the declaration line land in different chunks, so
# extraction stores TWO rows for the one definition — same end line, starts
# one line apart. That is the shape measured on cli.py's `get_code_context`.
_DECORATED_FIELDS = "\n".join(
    f"    field_{i}: str = typer.Option(None, help='value {i}')"
    for i in range(18))
DECORATED_FILE = (
    "def decorator(fn):\n"
    "    return fn\n"
    "\n"
    "\n"
    "@decorator\n"
    "def prepare(\n"
    f"{_DECORATED_FIELDS}\n"
    "):\n"
    "    return 1\n"
)


def _repeated_definitions(count: int) -> str:
    return "".join(f"def prepare():\n    return {i}\n\n\n" for i in range(count))


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]


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


def _stub_core(cfg: Config) -> Core:
    c = Core(cfg)
    c.embedder = StubEmbedder()  # type: ignore[assignment]
    c.store = StubStore()  # type: ignore[assignment]
    c.indexer.embedder = c.embedder  # type: ignore[assignment]
    c.indexer.store = c.store  # type: ignore[assignment]
    return c


def _build_rig(tmp_path, monkeypatch, files: dict[str, str]):
    """Index a throwaway project holding ``files``; return the core + entry."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 stale_ttl=0, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = _stub_core(cfg)
    project = tmp_path / "proj"
    project.mkdir()
    for name, text in files.items():
        (project / name).write_text(text)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="symbolres")
    core.run_index(entry.slug, str(project))
    return core, reg, entry, project, cfg


@pytest.fixture()
def wide_rig(tmp_path, monkeypatch):
    """25 files define the name first; the target file's definition is 26th."""
    files = {f"early_{i:02d}.py": EARLY_FILE for i in range(EARLY_FILE_COUNT)}
    files["late_definition.py"] = LATE_FILE
    core, reg, entry, project, cfg = _build_rig(tmp_path, monkeypatch, files)
    yield core, reg, entry, project, cfg
    reg.close()
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False


@pytest.fixture()
def repeated_rig(tmp_path, monkeypatch):
    """Six definitions of one name inside a single file."""
    files = {"repeated_definitions.py": _repeated_definitions(6)}
    core, reg, entry, project, cfg = _build_rig(tmp_path, monkeypatch, files)
    yield core, reg, entry, project, cfg
    reg.close()
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False


@pytest.fixture()
def disjoint_rig(tmp_path, monkeypatch):
    """One label, two rows in ONE file, with genuinely disjoint ranges."""
    files = {"disjoint_rows.py": DISJOINT_FILE}
    core, reg, entry, project, cfg = _build_rig(tmp_path, monkeypatch, files)
    yield core, reg, entry, project, cfg
    reg.close()
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False


@pytest.fixture()
def decorated_rig(tmp_path, monkeypatch):
    """One decorated definition — two stored rows, one definition."""
    files = {"decorated.py": DECORATED_FILE}
    core, reg, entry, project, cfg = _build_rig(tmp_path, monkeypatch, files)
    yield core, reg, entry, project, cfg
    reg.close()
    import code_indexer.cli as cli
    cli._core = None
    cli._core_skip = False


# ---------------------------------------------------------------------------
# the capped-read shape: the target's row sorts past the 25-row default
# ---------------------------------------------------------------------------

def test_the_name_population_outgrows_the_default_cap(wide_rig):
    """The fixture really is wider than 25 — otherwise the pin is vacuous."""
    core, reg, entry, project, cfg = wide_rig
    m = core.manifest_for(entry.slug)
    try:
        capped = m.find_symbols(SHARED_NAME, substring=False)
        whole = m.definitions_of(SHARED_NAME)
    finally:
        m.close()
    assert len(whole) == EARLY_FILE_COUNT + 1
    assert len(capped) == 25
    assert "late_definition.py" not in [r.file for r in capped]


def test_symbol_mode_resolves_a_row_past_the_cap(wide_rig):
    """Manifest path: the stored row for the late file must be returned."""
    core, reg, entry, project, cfg = wide_rig
    data = core.code_context(entry, "late_definition.py", symbol=SHARED_NAME)
    assert data["stale"] is False
    assert data["re_resolved"] is False
    body = "\n".join(data["segments"][0]["lines"])
    assert "def prepare():" in body
    assert "return 2" in body


def test_cli_symbol_mode_does_not_deny_an_indexed_row(wide_rig):
    """User-facing: no InternalError, just the definition's lines."""
    import code_indexer.cli as cli
    core, reg, entry, project, cfg = wide_rig
    cli._core = core
    cli._core_skip = True
    result = runner.invoke(app, ["get-code-context", "late_definition.py",
                                 "--project", project, "--symbol", SHARED_NAME,
                                 "--skip-stale-check"])
    assert result.exit_code == 0, result.output
    assert "not indexed in" not in result.output
    assert "def prepare():" in result.output
    cli._core = None
    cli._core_skip = False


def test_eval_symbol_range_resolves_a_row_past_the_cap(wide_rig):
    """The eval harness scored this definition as absent and missed a hit."""
    core, reg, entry, project, cfg = wide_rig
    m = core.manifest_for(entry.slug)
    try:
        ranges = evalharness._symbol_ranges(m, "late_definition.py", SHARED_NAME)
    finally:
        m.close()
    assert ranges == [(5, 6)]


# ---------------------------------------------------------------------------
# the segment cap: every matching definition is returned, not the first five
# ---------------------------------------------------------------------------

def test_symbol_mode_returns_every_definition_in_the_file(repeated_rig):
    core, reg, entry, project, cfg = repeated_rig
    data = core.code_context(entry, "repeated_definitions.py",
                             symbol=SHARED_NAME)
    assert len(data["segments"]) == 6
    starts = [seg["start_line"] for seg in data["segments"]]
    assert starts == sorted(starts)


def test_re_resolved_symbol_mode_returns_every_definition(repeated_rig):
    """Changed-file path: the live re-resolve answers with every range too."""
    core, reg, entry, project, cfg = repeated_rig
    target = project / "repeated_definitions.py"
    target.write_text("# edited\n" + _repeated_definitions(6))
    data = core.code_context(entry, "repeated_definitions.py",
                             symbol=SHARED_NAME)
    assert data["re_resolved"] is True
    assert data["stale"] is False
    assert len(data["segments"]) == 6


# ---------------------------------------------------------------------------
# the exact-name rule: extraction stores no dotted name, so none is matched
# ---------------------------------------------------------------------------

def test_re_resolved_symbol_mode_matches_the_exact_name_only(
        tmp_path, monkeypatch):
    """A live chunk named ``pkg.prepare`` is not a match for ``prepare``.

    Extraction stores the leaf of a qualified name (C#) and the last part of
    a ``Foo::bar`` declarator (C++), so a dotted symbol never reaches the
    store and the changed-file re-resolve path must not invent a dotted
    match either: the exact name is the whole rule.
    """
    files = {"dotted.py": "def unrelated():\n    return 0\n"}
    core, reg, entry, project, cfg = _build_rig(tmp_path, monkeypatch, files)
    try:
        target = project / "dotted.py"
        target.write_text("# edited\n" + files["dotted.py"])

        class DottedChunk:
            symbol = "pkg.prepare"
            start_line = 10
            end_line = 12

        monkeypatch.setattr(ts_chunker, "chunk_text",
                            lambda rel, text: [DottedChunk()])
        with pytest.raises(ValueError, match="not indexed in dotted.py"):
            core.code_context(entry, "dotted.py", symbol=SHARED_NAME)
    finally:
        reg.close()
        import code_indexer.cli as cli
        cli._core = None
        cli._core_skip = False


def test_eval_scores_a_hit_inside_a_later_row(disjoint_rig):
    """Every row of a label is scored, not just the first (issue #25).

    The two rows here are genuinely disjoint, so a hit inside the second one
    is a correct answer to "where is this definition in this file" and must
    not be scored as a miss. First-row-only scoring passes only because the
    measured duplicates are decorator double-rows with near-identical ranges.
    """
    core, reg, entry, project, cfg = disjoint_rig
    m = core.manifest_for(entry.slug)
    try:
        ranges = evalharness._symbol_ranges(m, "disjoint_rows.py",
                                            "repeated")
    finally:
        m.close()
    assert len(ranges) == 2, ranges
    (first_s, first_e), (second_s, second_e) = ranges
    assert second_s > first_e, ranges  # genuinely disjoint, not a double-row

    rel = evalharness.Relevant(file="disjoint_rows.py", symbol="repeated")
    inside_second = {"file": "disjoint_rows.py", "symbol": None,
                     "start_line": second_s, "end_line": second_e}
    assert evalharness._hit_correct(core, entry, inside_second, rel) is True

    inside_first = {"file": "disjoint_rows.py", "symbol": None,
                    "start_line": first_s, "end_line": first_e}
    assert evalharness._hit_correct(core, entry, inside_first, rel) is True

    gap = first_e + 1
    assert gap < second_s
    between_rows = {"file": "disjoint_rows.py", "symbol": None,
                    "start_line": gap, "end_line": gap}
    assert evalharness._hit_correct(core, entry, between_rows, rel) is False


def test_a_decorated_definition_is_one_segment(decorated_rig):
    """Two stored rows for one decorated definition answer as ONE range.

    A caller counting segments to count definitions must not overcount. The
    merge is the answer's stated rule: overlapping or touching same-name
    ranges collapse to one span, so here the decorator-line row and the
    declaration-line row become the single span of the whole definition
    (issue #26).
    """
    core, reg, entry, project, cfg = decorated_rig
    m = core.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(SHARED_NAME, substring=False,
                              file="decorated.py", limit=None)
    finally:
        m.close()
    assert len(rows) == 2, [(r.start_line, r.end_line) for r in rows]
    first, second = sorted((r.start_line, r.end_line) for r in rows)
    assert first[1] == second[1]  # decorator line + declaration line
    assert second[0] > first[0]

    data = core.code_context(entry, "decorated.py", symbol=SHARED_NAME)
    assert len(data["segments"]) == 1
    segment = data["segments"][0]
    assert (segment["start_line"], segment["end_line"]) == (first[0], first[1])
    body = "\n".join(segment["lines"])
    assert "@decorator" in body
    assert "def prepare(" in body


# ---------------------------------------------------------------------------
# context padding is presentation: it never changes the segment count
# ---------------------------------------------------------------------------

def test_context_padding_keeps_six_definitions_apart(repeated_rig):
    """Six definitions four lines apart stay SIX segments when padded.

    ``--context-lines 2`` makes consecutive windows touch and overlap: rows
    (1,2) and (5,6) pad to (1,4) and (3,8). Padding before the merge fused
    every window into one span covering the whole file. The rows' own ranges
    are merged first, then each surviving span is padded, so the count is
    still six and each span is widened by two lines on both sides (issue
    #26).
    """
    core, reg, entry, project, cfg = repeated_rig
    data = core.code_context(entry, "repeated_definitions.py",
                             symbol=SHARED_NAME, context_lines=2)
    spans = [(seg["start_line"], seg["end_line"]) for seg in data["segments"]]
    assert spans == [(1, 4), (3, 8), (7, 12), (11, 16), (15, 20), (19, 24)], \
        spans
    # The padded windows really do touch — otherwise this pin is vacuous.
    assert all(spans[i][0] <= spans[i - 1][1] for i in range(1, len(spans)))


def test_context_padding_keeps_two_close_definitions_apart(disjoint_rig):
    """Two genuinely disjoint definitions a few lines apart stay TWO spans.

    The rows are (2,3) and (5,6); ``--context-lines 2`` pads them to (1,5)
    and (3,8), which touch. Merging the padded windows would answer one
    segment whose body contains the code between the two definitions.
    """
    core, reg, entry, project, cfg = disjoint_rig
    m = core.manifest_for(entry.slug)
    try:
        rows = m.find_symbols("repeated", substring=False,
                              file="disjoint_rows.py", limit=None)
    finally:
        m.close()
    own = sorted((r.start_line, r.end_line) for r in rows)
    assert len(own) == 2, own
    expected = [(max(1, s - 2), e + 2) for s, e in own]
    # The padded windows of these two rows touch, so merging them first
    # would have answered one segment covering the code between them.
    assert expected[1][0] <= expected[0][1]

    data = core.code_context(entry, "disjoint_rows.py", symbol="repeated",
                             context_lines=2)
    spans = [(seg["start_line"], seg["end_line"]) for seg in data["segments"]]
    assert spans == expected, (spans, expected)


def test_context_padding_keeps_every_definition_on_the_live_path(
        repeated_rig):
    """The changed-file re-resolve path pads after merging too."""
    core, reg, entry, project, cfg = repeated_rig
    target = project / "repeated_definitions.py"
    target.write_text("# edited\n" + _repeated_definitions(6))
    data = core.code_context(entry, "repeated_definitions.py",
                             symbol=SHARED_NAME, context_lines=2)
    assert data["re_resolved"] is True
    spans = [(seg["start_line"], seg["end_line"]) for seg in data["segments"]]
    assert len(spans) == 6, spans
    # The one-line comment shifts every definition down by one.
    assert spans == [(1, 5), (4, 9), (8, 13), (12, 17), (16, 21), (20, 25)], \
        spans


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
