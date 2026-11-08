"""Coverage travels with every read answer, and an empty answer names its cause.

Every empty-reason case CONSTRUCTS its condition on a real index built from
real files: a parsed project that does not hold the name, a project whose only
file never parsed, a language with no AST symbol reader, and a registered
project with no files at all. Nothing here asserts a constant the code could
agree with by accident -- each case first checks that the index it built really
has the shape the reason is about (parsed count, verdict, language).

The roster case derives the read commands that answer JSON from the typer
registration and the read/write roster, the same way the flag-parity roster is
derived, so a NEW read command cannot ship without a coverage object.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
import typer  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.command_classes import READ_COMMANDS  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

PY = "def helper(x):\n    return x + 1\n"
SH = "#!/bin/sh\nmain() {\n  echo run\n}\nmain\n"
MD = "# notes\n\nthe documentation\n"


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


@pytest.fixture()
def index_project(tmp_path, monkeypatch):
    """Build and index a project from a {relative path: text} mapping."""
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None
    cli._core_skip = False
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))

    def build(files: dict[str, str], name: str = "proj"):
        project = tmp_path / name
        project.mkdir()
        for rel, text in files.items():
            path = project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        entry = reg.add(str(project), name=name)
        core.run_index(entry.slug, str(project))
        return core, entry, project

    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield build
    reg.close()
    cli._core = None
    cli._core_skip = False


def _json(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _files(core, entry) -> dict[str, tuple[str | None, str | None]]:
    m = core.read_manifest(entry.slug)
    try:
        return {path: (row.language, row.parse_verdict)
                for path, row in m.all_files().items()}
    finally:
        m.close()


# ---------------------------------------------------------------------------
# one case per empty reason, each condition constructed
# ---------------------------------------------------------------------------

def test_a_name_absent_from_a_parsed_index(index_project):
    core, entry, project = index_project({"mod.py": PY})
    assert _files(core, entry) == {"mod.py": ("python", "parsed")}

    result = runner.invoke(app, ["find-symbol", "zzz_missing", "--project",
                                 str(project), "--json"])
    payload = _json(result)
    assert payload["match_count"] == 0
    assert payload["empty_reason"] == "name-absent"
    assert payload["empty_reason_sentence"].startswith(
        "no symbols matching 'zzz_missing': the name is absent")
    assert "parsed 1 of 1 files" in payload["empty_reason_sentence"]

    text = runner.invoke(app, ["find-symbol", "zzz_missing", "--project",
                               str(project)])
    assert text.exit_code == 0, text.output
    assert "the name is absent from an index that parsed 1 of 1 files" \
        in text.stdout


def test_files_that_never_parsed_are_named_by_count_and_language(index_project):
    core, entry, project = index_project({"notes.md": MD}, name="docs")
    assert _files(core, entry) == {"notes.md": ("markdown",
                                               "parse_unavailable")}

    payload = _json(runner.invoke(
        app, ["find-symbol", "zzz_missing", "--project", str(project),
              "--json"]))
    assert payload["empty_reason"] == "files-not-parsed"
    assert "1 of 1 files were not parsed (markdown)" \
        in payload["empty_reason_sentence"]
    assert payload["coverage"]["files_parsed"] == 0
    assert payload["coverage"]["answer_languages_without_ast"] == ["markdown"]
    # The old shared key is gone from both levels: one name, one meaning.
    assert "languages_without_ast" not in payload
    assert "languages_without_ast" not in payload["coverage"]

    text = runner.invoke(app, ["find-symbol", "zzz_missing", "--project",
                               str(project)])
    assert "1 of 1 files were not parsed (markdown)" in text.stdout


def test_a_language_without_an_ast_reader_is_named(index_project):
    core, entry, project = index_project({"run.sh": SH}, name="scripts")
    # The condition is the parsed-but-symbol-less file, not a constant: the
    # shell file parsed (its grammar exists) and still produced no symbol.
    assert _files(core, entry) == {"run.sh": ("shell", "parsed")}

    payload = _json(runner.invoke(
        app, ["find-symbol", "zzz_missing", "--project", str(project),
              "--json"]))
    assert payload["empty_reason"] == "language-without-ast"
    assert "shell has no AST reader" in payload["empty_reason_sentence"]
    assert payload["coverage"]["files_parsed"] == 1
    assert payload["coverage"]["answer_languages_without_ast"] == ["shell"]

    text = runner.invoke(app, ["find-symbol", "zzz_missing", "--project",
                               str(project)])
    assert "shell has no AST reader" in text.stdout


def test_an_empty_index_says_the_project_is_not_indexed(index_project):
    core, entry, project = index_project({}, name="blank")
    assert _files(core, entry) == {}

    payload = _json(runner.invoke(
        app, ["find-symbol", "zzz_missing", "--project", str(project),
              "--json"]))
    assert payload["empty_reason"] == "index-empty"
    assert "the project is not indexed" in payload["empty_reason_sentence"]
    assert payload["coverage"]["files"] == 0
    # `complete` counts parsed-versus-unparsed files, not whether the index
    # holds anything: it is vacuously true on a zero-file index, and the doc
    # says so -- a reader must branch on `files: 0`, never on `complete`.
    assert payload["coverage"]["complete"] is True
    schema = os.path.join(os.path.dirname(__file__), "..", "docs",
                          "json-schema.md")
    with open(schema, encoding="utf-8") as handle:
        flat = " ".join(handle.read().split())
    assert "on an index that holds no files the counts are 0" in flat
    assert "`complete` is vacuously" in flat

    text = runner.invoke(app, ["find-symbol", "zzz_missing", "--project",
                               str(project)])
    assert "the project is not indexed" in text.stdout


def test_the_reason_vocabulary_is_the_same_on_every_name_read(index_project):
    """find-definition and find-references answer the same value set."""
    core, entry, project = index_project({"run.sh": SH}, name="scripts")
    for argv in (["find-definition", "zzz_missing"],
                 ["find-references", "zzz_missing"],
                 ["find-callers", "zzz_missing"],
                 ["find-callees", "zzz_missing"]):
        payload = _json(runner.invoke(
            app, [*argv, "--project", str(project), "--json"]))
        assert payload["empty_reason"] == "language-without-ast", argv


def test_the_reason_vocabulary_is_closed_by_a_type_not_a_convention():
    """Every name read answers from the same value set.

    ``defined-not-referenced`` is the fifth value: a name the index DEFINES
    with nothing referencing it is a fact about the code, not an absence.
    """
    from typing import get_args

    from code_indexer.coverage import EMPTY_REASONS, EmptyReason

    assert get_args(EmptyReason) == EMPTY_REASONS
    assert len(EMPTY_REASONS) == 5


def test_the_search_surface_words_its_own_cause(index_project):
    """Same four VALUES, but a search's sentence speaks about its query.

    `no results: the name is absent ...` was read by a search over a query
    that isn't a name; the reason value must stay (it is machine surface) and
    only the human sentence is worded for the surface that answers.
    """
    core, entry, project = index_project({"mod.py": PY})
    payload = _json(runner.invoke(
        app, ["semantic-search", "zzz_absent_query", "--project",
              str(project), "--json"]))
    assert payload["hits"] == []
    assert payload["empty_reason"] == "name-absent"
    sentence = payload["empty_reason_sentence"]
    assert sentence.startswith("no results:")
    assert "matched the query" in sentence
    assert "the name" not in sentence

    text = runner.invoke(app, ["semantic-search", "zzz_absent_query",
                               "--project", str(project)])
    assert text.exit_code == 0, text.output
    assert "matched the query" in text.stdout
    assert "the name is absent" not in text.stdout

    # The symbol reads on the SAME index keep their own wording.
    sym = _json(runner.invoke(
        app, ["find-symbol", "zzz_absent", "--project", str(project),
              "--json"]))
    assert sym["empty_reason"] == "name-absent"
    assert "the name is absent" in sym["empty_reason_sentence"]
    assert "matched the query" not in sym["empty_reason_sentence"]


def test_the_search_surface_names_unparsed_files_without_speaking_of_a_name(
        index_project):
    core, entry, project = index_project({"notes.md": MD}, name="docs")
    payload = _json(runner.invoke(
        app, ["semantic-search", "zzz_absent_query", "--project",
              str(project), "--json"]))
    assert payload["empty_reason"] == "files-not-parsed"
    sentence = payload["empty_reason_sentence"]
    assert "so the query may live in files the index never read" in sentence
    assert "the name" not in sentence


# ---------------------------------------------------------------------------
# coverage travels with the answer
# ---------------------------------------------------------------------------

def test_coverage_rides_the_json_answer_and_is_scoped(index_project):
    core, entry, project = index_project({"mod.py": PY, "notes.md": MD})
    payload = _json(runner.invoke(
        app, ["find-symbol", "helper", "--project", str(project), "--json"]))
    coverage = payload["coverage"]
    assert coverage["scope"] == "project"
    assert (coverage["files"], coverage["files_parsed"],
            coverage["files_not_parsed"]) == (2, 1, 1)
    assert coverage["freshness"] == "fresh"
    assert coverage["complete"] is False

    # An outline answer read ONE file: its coverage says so.
    scoped = _json(runner.invoke(
        app, ["outline", "mod.py", "--project", str(project), "--json"]))
    assert scoped["coverage"]["scope"] == "answer"
    assert scoped["coverage"]["files"] == 1
    assert scoped["coverage"]["files_parsed"] == 1
    assert scoped["coverage"]["complete"] is True


def test_a_prefix_narrows_the_answer_not_the_coverage_scope(index_project):
    """The counts stay project-wide under a prefix, and that is the contract.

    The block says whether an absence in THIS answer can be trusted; a file
    outside the prefix can only make a prefix answer look less complete, never
    more, so the scope does not narrow with the question and the counts stay
    comparable with `index-status`.
    """
    core, entry, project = index_project({"src/mod.py": PY, "notes.md": MD})
    overview = _json(runner.invoke(
        app, ["overview", "--path-prefix", "src", "--project", str(project),
              "--json"]))
    assert overview["files"] == 1
    assert overview["coverage"]["scope"] == "project"
    assert overview["coverage"]["files"] == 2

    skeletal = _json(runner.invoke(
        app, ["skeleton", "src", "--project", str(project), "--json"]))
    assert skeletal["coverage"]["scope"] == "project"
    assert skeletal["coverage"]["files"] == 2


def test_the_text_line_is_suppressed_on_a_clean_answer(index_project):
    core, entry, project = index_project({"mod.py": PY})
    result = runner.invoke(app, ["find-symbol", "helper", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "helper" in result.stdout
    assert "coverage:" not in result.output


def test_the_text_line_appears_only_when_coverage_is_incomplete(index_project):
    core, entry, project = index_project({"mod.py": PY, "notes.md": MD})
    result = runner.invoke(app, ["find-symbol", "helper", "--project",
                                 str(project)])
    assert result.exit_code == 0, result.output
    assert "coverage: 1 of 2 files parsed, 1 not parsed" in result.output


def test_withheld_files_are_counted_in_the_coverage(index_project):
    core, entry, project = index_project({"mod.py": PY, ".env": "TOKEN=1\n"})
    payload = _json(runner.invoke(
        app, ["find-symbol", "helper", "--project", str(project), "--json"]))
    assert payload["coverage"]["files_withheld"] == 1
    assert payload["coverage"]["complete"] is False

    text = runner.invoke(app, ["find-symbol", "helper", "--project",
                               str(project)])
    assert "1 file withheld for privacy" in text.output


# ---------------------------------------------------------------------------
# the fingerprint sentence
# ---------------------------------------------------------------------------

def _set_meta(core, entry, **values: str) -> None:
    """Record meta values on a built index (a test constructs the state)."""
    m = core.manifest_for(entry.slug)
    try:
        for key, value in values.items():
            m.set_meta(key, value)
    finally:
        m.close()


def _status_json(project) -> dict:
    return _json(runner.invoke(
        app, ["index-status", "--project", str(project), "--json"]))


def test_the_fingerprint_sentence_matches_the_verdict_it_prints_beside(
        index_project):
    """Comparable keys matched and the vector side matches: say both."""
    core, entry, project = index_project({"mod.py": PY})
    payload = _status_json(project)
    # The state this sentence is printed in: the row keys matched, and the
    # vector side is the one this build would write.
    assert payload["fingerprint"] == "unverified"
    assert payload["vectors_state"] == "current"
    meaning = payload["fingerprint_meaning"]
    for compared in ("embedding model", "embedded-text format", "chunker",
                     "extraction version"):
        assert compared in meaning, compared
    # The dimension gap stays: it is the one key a read cannot compare, and
    # the recorded model/format WERE compared and matched.
    assert "dimension" in meaning and "uncompared" in meaning
    assert "not compared against the configured model" not in meaning
    assert "another model" not in meaning
    # Nothing here says a key did not match, because none did.
    assert "did not match" not in meaning
    assert "stale" not in meaning

    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert len(text.stdout.splitlines()) == 1
    assert "fingerprint=unverified" in text.stdout
    assert "fingerprint meaning:" in text.stderr


def test_the_fingerprint_sentence_names_the_vector_key_that_did_not_match(
        index_project):
    """A changed embedding model is the state the sentence must not call a match.

    This is the run the previous wording was false in: `vectors_state=stale`
    is printed in the same line, and the embedding model is exactly the key
    that did NOT match.
    """
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, embed_model="a-different-model")
    payload = _status_json(project)
    assert payload["vectors_state"] == "stale"
    meaning = payload["fingerprint_meaning"]
    assert "embed_model:" in meaning, meaning
    assert "a-different-model" in meaning, meaning
    assert "did not match" in meaning
    assert "stale" in meaning
    assert "reindex-project" in meaning
    assert "every fingerprint key a read can compare matched" not in meaning

    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert len(text.stdout.splitlines()) == 1
    assert "vectors_state=stale" in text.stdout
    line = [ln for ln in text.stderr.splitlines()
            if ln.startswith("fingerprint meaning:")]
    assert len(line) == 1, text.stderr
    assert "embed_model:" in line[0]


def test_the_fingerprint_sentence_names_the_row_key_that_did_not_match(
        index_project):
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, chunker_version="999")
    payload = _status_json(project)
    assert payload["fingerprint"] == "needs-reindex"
    meaning = payload["fingerprint_meaning"]
    assert "chunker_version: 999 ->" in meaning, meaning
    assert "reindex-project" in meaning
    assert "every fingerprint key a read can compare matched" not in meaning


def test_the_fingerprint_sentence_says_when_the_comparison_did_not_run(
        index_project):
    """No recorded fingerprint is no comparison -- never a pass."""
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, embed_model="", embed_dim="",
              embed_text_version="", chunker_version="",
              extraction_version="")
    payload = _status_json(project)
    assert "fingerprint" not in payload
    meaning = payload["fingerprint_meaning"]
    assert "did not run" in meaning, meaning
    assert "matched" not in meaning

    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert len(text.stdout.splitlines()) == 1
    assert "did not run" in text.stderr


@pytest.mark.parametrize("meta", [{"embed_model": "a-different-model"},
                                  {"embed_text_version": "an-older-format"}])
def test_the_sentence_never_claims_agreement_on_a_stale_verdict(
        index_project, meta):
    """The pin that failed before: agreement claimed while stale is printed.

    Both vector-side keys are constructed, because either one alone makes the
    vector verdict `stale`, and one sentence covered both.
    """
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, **meta)
    payload = _status_json(project)
    assert payload["vectors_state"] == "stale"
    meaning = payload["fingerprint_meaning"]
    assert "every fingerprint key a read can compare matched" not in meaning
    assert "did not match" in meaning
    assert "reindex-project" in meaning


def test_the_fingerprint_sentence_names_the_keys_the_read_path_compares():
    """The sentence is pinned to the comparison the code actually makes."""
    from code_indexer import fingerprint as fp

    compared = set(fp.PROBE_FREE_KEYS)
    assert compared == {"embed_model", "embed_text_version", "chunker_version",
                        "extraction_version"}
    # The one key a read cannot learn without the service is the one the
    # sentence says was not compared.
    assert "embed_dim" in fp.FINGERPRINT_KEYS
    assert "embed_dim" not in compared

    recorded = fp.Fingerprint(embed_model="other-model", embed_dim=8,
                              embed_text_version="v1", chunker_version=1,
                              extraction_version=1)
    configured = fp.configured_values("stub")
    diffs = fp.probe_free_mismatches(recorded, configured)
    assert any(d.startswith("embed_model: ") for d in diffs)
    assert not any(d.startswith("embed_dim: ") for d in diffs)


# ---------------------------------------------------------------------------
# the fingerprint sentence over the WHOLE vector state space
# ---------------------------------------------------------------------------
#
# The sentence used to take its vector verdict from the fingerprint side (the
# identity comparison alone) while the line beside it printed the mode- and
# marker-aware verdict. Two sources, one sentence: with vectors off the line
# said `vectors=off vectors_state=current` and the sentence said the vector
# side was stale. Every row below fixes mode x identity x marker x row keys,
# reads the SAME command, and asserts the sentence agrees with the fields
# printed beside it -- so a sentence fed any other source fails a row.

_VECTORS_OFF_WITH_A_DIFFERENT_MODEL = "reproducer-vectors-off-other-model"


@pytest.mark.parametrize("mode, identity, marker, row_key", [
    # vectors on, everything current: the ordinary read.
    pytest.param("on", "match", "current", "match", id="on-current"),
    # vectors on, identity differs: the run in which the model did NOT match.
    pytest.param("on", "differ", "current", "match", id="on-stale"),
    # vectors on, no marker recorded (legacy manifest).
    pytest.param("on", "match", "absent", "match", id="on-absent"),
    # vectors on, a recorded marker nothing in this build writes.
    pytest.param("on", "match", "stale", "match", id="on-recorded-stale"),
    # a row key differs AND the manifest records the collection stale too.
    pytest.param("on", "match", "stale", "differ",
                 id="on-needs-reindex-and-marked-stale"),
    pytest.param("on", "match", "error", "match", id="on-error"),
    # vectors on, a row key differs: the row surfaces refuse.
    pytest.param("on", "match", "current", "differ", id="on-needs-reindex"),
    pytest.param("on", "differ", "current", "differ",
                 id="on-needs-reindex-and-stale"),
    # vectors OFF: no vector side exists, whatever the identity or marker says.
    pytest.param("off", "differ", "current", "match",
                 id="off-different-model"),
    pytest.param("off", "match", "current", "match", id="off-current"),
    pytest.param("off", "match", "current", "differ",
                 id="off-needs-reindex"),
    pytest.param("off", "differ", "stale", "match",
                 id="off-recorded-stale"),
    # vectors off, marker still `current` from an earlier vectors-on build.
    pytest.param("off", "differ", "current", "differ",
                 id="off-everything-else-differs"),
    # vectors off, identity matches, and the manifest still records `stale`:
    # the inert clause the matched branch appends.
    pytest.param("off", "match", "stale", "match",
                 id="off-matched-recorded-stale"),
])
def test_the_sentence_and_the_line_cannot_contradict(
        index_project, mode, identity, marker, row_key):
    core, entry, project = index_project({"mod.py": PY})
    meta: dict[str, str] = {"vectors": mode, "vectors_state": marker}
    if identity == "differ":
        meta["embed_model"] = _VECTORS_OFF_WITH_A_DIFFERENT_MODEL
    if row_key == "differ":
        meta["chunker_version"] = "999"
    _set_meta(core, entry, **meta)

    payload = _status_json(project)
    meaning = payload["fingerprint_meaning"]
    vectors, vectors_state = payload["vectors"], payload["vectors_state"]

    # ---- the two fields the sentence is printed beside ------------------
    assert payload["fingerprint"] in ("unverified", "needs-reindex")
    assert (payload["fingerprint"] == "needs-reindex") == (row_key == "differ")
    assert vectors == mode
    if mode == "on":
        assert vectors_state == ("stale" if identity == "differ" else marker)
    else:
        # Pre-existing: a vectors-off read prints the recorded marker as-is.
        assert vectors_state == marker

    # ---- the sentence, against those fields ----------------------------
    if row_key == "differ":
        assert "chunker_version: 999 ->" in meaning, meaning
        assert "reindex-project" in meaning, meaning
        assert "every fingerprint key a read can compare matched" not in meaning
        if vectors == "off":
            assert "no vectors are stored" in meaning, meaning
            assert "vector rebuild is what clears this" not in meaning, meaning
        elif identity == "differ":
            assert "its vectors are stale too" in meaning, meaning
            assert _VECTORS_OFF_WITH_A_DIFFERENT_MODEL in meaning, meaning
        elif marker == "stale":
            # Identity matches, so no key is named, but the recorded marker
            # makes the reindex rebuild the collection too.
            assert ("the manifest also records the vector collection as "
                    "stale") in meaning, meaning
        return
    if vectors == "off":
        # There is no vector side: say so, and never prescribe a rebuild.
        assert "no vectors are stored" in meaning, meaning
        assert "vector rebuild is what clears this" not in meaning, meaning
        assert "--vectors on" not in meaning, meaning
        assert "reindex-project" not in meaning, meaning
        if identity == "differ":
            assert _VECTORS_OFF_WITH_A_DIFFERENT_MODEL in meaning, meaning
            assert "inert" in meaning, meaning
            assert "no vector side to be stale" in meaning, meaning
        if marker == "stale":
            # A recorded marker nothing in this build writes, inert while off.
            assert "inert while vectors are off" in meaning, meaning
    elif identity == "differ":
        assert "did not match" in meaning, meaning
        assert _VECTORS_OFF_WITH_A_DIFFERENT_MODEL in meaning, meaning
        assert "vector rebuild is what clears this" in meaning, meaning
        assert "every fingerprint key a read can compare matched" not in meaning
    elif marker == "stale":
        # Identity matches, so no key is named -- but the printed verdict
        # says stale, and the sentence cannot claim otherwise.
        assert "records the vector collection" in meaning, meaning
        assert "stale" in meaning, meaning
    else:
        assert "every fingerprint key a read can compare matched" in meaning
        assert "did not match" not in meaning, meaning
        assert "stale" not in meaning, meaning

    # ---- one line out, one line of explanation, both from the same run --
    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert text.exit_code == 0, text.output
    assert len(text.stdout.splitlines()) == 1
    stderr_lines = [ln for ln in text.stderr.splitlines()
                    if ln.startswith("fingerprint meaning:")]
    assert len(stderr_lines) == 1, text.stderr
    assert stderr_lines[0] == f"fingerprint meaning: {meaning}"


@pytest.mark.parametrize("mode", ["on", "off"])
def test_the_sentence_reports_no_comparison_when_none_was_recorded(
        index_project, mode):
    """No recorded fingerprint is no comparison -- in both vector modes."""
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, vectors=mode, embed_model="", embed_dim="",
              embed_text_version="", chunker_version="",
              extraction_version="")
    payload = _status_json(project)
    assert "fingerprint" not in payload
    meaning = payload["fingerprint_meaning"]
    assert "did not run" in meaning, meaning
    assert "matched" not in meaning, meaning
    if mode == "off":
        assert "no vector side here either" in meaning, meaning


def test_the_sentence_takes_its_vector_verdict_from_the_line_not_the_rows(
        index_project, monkeypatch):
    """The seam pin: two sources that disagree, and only one may be read.

    The row-side report is made to claim a stale vector identity while the
    mode is off, which is exactly the disagreement the old sentence read
    itself into. The line prints the mode-aware verdict, so the sentence must
    follow it; a sentence fed the fingerprint side instead says `stale` here
    and fails.
    """
    core, entry, project = index_project({"mod.py": PY})
    _set_meta(core, entry, vectors="off")
    row_side = core.fingerprint_status

    def disagreeing(entry_):
        status = row_side(entry_)
        return {**status, "vectors": "stale",
                "vectors_reason": "embed_model: elsewhere -> stub"}

    monkeypatch.setattr(core, "fingerprint_status", disagreeing)
    payload = _status_json(project)
    assert payload["vectors"] == "off"
    assert payload["vectors_state"] == "current"
    meaning = payload["fingerprint_meaning"]
    assert "no vectors are stored" in meaning, meaning
    assert "did not match" not in meaning, meaning
    assert "elsewhere -> stub" not in meaning, meaning


# ---------------------------------------------------------------------------
# the roster: a new read command cannot ship without coverage
# ---------------------------------------------------------------------------

def _json_read_roster() -> set[str]:
    """Read commands the CLI gives a JSON form (``--json`` or ``--format``)."""
    group = typer.main.get_command(app)
    found: set[str] = set()
    for name, command in group.commands.items():  # type: ignore[attr-defined]
        if name not in READ_COMMANDS:
            continue
        opts = {opt for param in command.params
                for opt in getattr(param, "opts", ())}
        if "--json" in opts or "--format" in opts:
            found.add(name)
    return found


def test_every_read_with_a_json_form_carries_coverage(index_project,
                                                     monkeypatch):
    core, entry, project = index_project({"mod.py": PY})
    # doctor's own checks are another module's subject; the case here is the
    # envelope every read answer shares, so its check list is stubbed empty.
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **kw: [])
    # changed-symbols diffs against git, so the answer it must carry coverage
    # on is read from a repository, not a bare directory.
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    prepared = runner.invoke(app, [
        "plan-replace-body", "helper", "--project", str(project),
        "--file", "mod.py", "--text", "    return x + 2"])
    assert prepared.exit_code == 0, prepared.output
    plan_dir = os.path.join(str(project), ".code-indexer", "plans")
    plan_path = os.path.join(plan_dir, sorted(os.listdir(plan_dir))[0])

    cases: dict[str, tuple[str, ...]] = {
        "changed-symbols": (),
        "deps": ("mod.py", "--format", "json"),
        "doctor": (),
        "file-outline": ("mod.py",),
        "find-callees": ("helper",),
        "find-callers": ("helper",),
        "find-definition": ("helper",),
        "find-references": ("helper",),
        "find-symbol": ("helper",),
        "get-code-context": ("mod.py", "--start-line", "1", "--end-line",
                            "2"),
        "index-status": (),
        "list-projects": (),
        "lookup-project": (),
        "map": (),
        "outline": ("mod.py",),
        "overview": (),
        "plan-show": (plan_path,),
        "plan-validate": (plan_path,),
        "semantic-search": ("helper",),
        "skeleton": (),
    }
    roster = _json_read_roster()
    assert roster == set(cases), roster ^ set(cases)

    no_project = {"doctor", "list-projects"}
    for command_name in sorted(roster):
        argv = [command_name, *cases[command_name]]
        if command_name not in no_project:
            argv += ["--project", str(project)]
        # deps carries its own --format vocabulary (json is one of its values)
        # and has no --json alias; every other read in the roster does.
        if command_name != "deps":
            argv.append("--json")
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, (command_name, result.output)
        payload = _json(result)
        assert "coverage" in payload, command_name
        assert payload["coverage"]["scope"] in ("project", "answer", "none"), \
            command_name


# ---------------------------------------------------------------------------
# the seam: one computation feeds the sentence AND the machine field
# ---------------------------------------------------------------------------
#
# The rule the fingerprint fix embodies is not "pass a verdict object"; it is
# that a human sentence and a machine field are rendered from ONE computation.
# Each surface below already satisfies it by construction, which is exactly
# why these tests exist: they are what keeps it true when the next change
# reaches for a second source.


def test_the_staleness_banner_is_derived_from_the_block_it_ships_with(
        index_project):
    """The `Warning:` line counts what the JSON block counts -- one verdict."""
    core, entry, project = index_project({"mod.py": PY})
    (project / "mod.py").write_text(
        PY + "\ndef extra():\n    return 2\n", encoding="utf-8")

    payload = _json(runner.invoke(
        app, ["find-symbol", "helper", "--project", str(project), "--json"]))
    assert payload["stale"] is True and payload["stale_known"] is True
    modified = sum(1 for reason in payload["stale_reasons"]
                   if reason["kind"] == "modified")
    assert modified == 1, payload["stale_reasons"]

    text = runner.invoke(app, ["find-symbol", "helper", "--project",
                               str(project)])
    banner = [line for line in text.stderr.splitlines()
              if line.startswith("Warning: index is stale")]
    assert len(banner) == 1, text.stderr
    # the count in the sentence IS the block's count, not a second walk
    assert f"{modified} modified file" in banner[0], banner[0]
    assert f"{payload['stale_reason_count']} " in banner[0] or modified == \
        payload["stale_reason_count"], (banner[0], payload)


def test_the_coverage_line_is_derived_from_the_block_it_ships_with(
        index_project):
    """The one text line and the coverage object are the same dict."""
    core, entry, project = index_project({"mod.py": PY, "notes.md": MD})
    payload = _json(runner.invoke(
        app, ["find-symbol", "helper", "--project", str(project), "--json"]))
    block = payload["coverage"]
    assert block["complete"] is False

    text = runner.invoke(app, ["find-symbol", "helper", "--project",
                               str(project)])
    lines = [line for line in text.stderr.splitlines()
             if line.startswith("coverage: ")]
    assert len(lines) == 1, text.stderr
    # built from the block's own values, so a second count cannot appear
    assert lines[0] == (f"coverage: {block['files_parsed']} of {block['files']} "
                        f"files parsed, {block['files_not_parsed']} not parsed"), \
        (lines[0], block)


def test_the_pass_summary_line_and_the_payload_are_one_result(index_project):
    """A pass prints the payload's own numbers, in text and in JSON."""
    core, entry, project = index_project({"mod.py": PY})
    payload = _json(runner.invoke(
        app, ["reindex-project", str(project), "--json"]))
    assert payload["index"]["state"] == "idle", payload
    result = payload["index"]["result"]

    text = runner.invoke(app, ["reindex-project", str(project)])
    assert text.exit_code == 0, text.output
    lines = [line for line in text.stdout.splitlines() if line.strip()]
    assert lines, text.output
    # the text form is the payload's result, rendered -- not a recount
    head, _, body = lines[0].partition(": ")
    assert head == entry.slug, lines[0]
    printed = json.loads(body)
    # `duration_s` is a wall-clock measurement: two passes measure two
    # different intervals, so comparing it compares the runner, not the two
    # forms. Every other field is a recount the moment it differs.
    assert printed.keys() == result.keys(), (printed, result)
    assert _except_duration(printed) == _except_duration(result), (
        printed, result)
    # Present, numeric and non-negative in both forms -- never equal.
    for form in (printed, result):
        assert isinstance(form["duration_s"], (int, float)), form
        assert not isinstance(form["duration_s"], bool), form
        assert form["duration_s"] >= 0, form


def _except_duration(result: dict) -> dict:
    """Every field of a pass result except the timing."""
    return {key: value for key, value in result.items() if key != "duration_s"}


# ---------------------------------------------------------------------------
# the error verdict names its own fact: a total beside the capped sample
# ---------------------------------------------------------------------------

def test_the_error_verdict_prints_the_total_beside_three_examples(
        index_project):
    """Five lost chunks print as a total of five and three examples.

    The sample is capped; the count is not. Three examples with no total is
    the shape that reads as "three chunks".
    """
    core, entry, project = index_project({"a.py": PY})
    m = core.manifest_for(entry.slug)
    try:
        for i in range(5):
            m.record_index_error("a.py", i, f"embed failed: timeout {i}")
    finally:
        m.close()

    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert text.exit_code == 0, text.output
    line = text.stdout.splitlines()[0]
    assert "error_count=5" in line, line
    sample = line.split("last_errors=", 1)[1]
    assert sample.count("a.py#") == 3, sample

    payload = _status_json(project)
    assert payload["error_count"] == 5
    assert len(payload["last_errors"]) == 3


def test_no_recorded_errors_prints_neither_field(index_project):
    core, entry, project = index_project({"a.py": PY})
    payload = _status_json(project)
    assert "error_count" not in payload
    assert "last_errors" not in payload
    text = runner.invoke(app, ["index-status", "--project", str(project)])
    assert "error_count=" not in text.stdout
    assert "last_errors=" not in text.stdout

