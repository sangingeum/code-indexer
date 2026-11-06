"""Ambiguity is reported, never resolved silently (ADR-0002 section 5).

A name with more than one identity answers with every candidate; a command
that must act on ONE symbol refuses with ArgumentError and lists them; and
the two graph commands union over every candidate and label each hit's
target instead of taking the first row.
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
from code_indexer.errors import AmbiguousSymbolError, ArgumentError  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

# The same name, twice, each definition reachable by its own caller — the
# shape of the 141 duplicated names the identity record was written for.
DUPLICATE = {
    "alpha.py": "def step():\n    return 1\n",
    "beta.py": "def step():\n    return 2\n",
    "caller_alpha.py": "from alpha import step\n\n\ndef run_alpha():\n    return step()\n",
    "caller_beta.py": "from beta import step\n\n\ndef run_beta():\n    return step()\n",
}

CANDIDATE_FIELDS = ("file", "range", "kind", "container", "signature",
                    "language", "decl_hash")


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
def ambiguous_rig(tmp_path, monkeypatch):
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
    project = tmp_path / "dupes"
    project.mkdir()
    for name, text in DUPLICATE.items():
        (project / name).write_text(text)
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="dupes")
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, reg, entry, project
    reg.close()
    cli._core = None
    cli._core_skip = False


def _invoke(project, *args):
    return runner.invoke(app, [*args, "--project", str(project)])


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------

def test_a_duplicated_name_answers_with_every_candidate(ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-symbol", "step", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["match_count"] == 2
    assert payload["truncated"] is False
    candidates = payload["ambiguity"]["candidates"]
    assert len(candidates) == 2, candidates
    assert {c["file"] for c in candidates} == {"alpha.py", "beta.py"}


def test_each_candidate_carries_every_field_the_record_names(ambiguous_rig):
    _, _, _, project = ambiguous_rig
    payload = json.loads(
        _invoke(project, "find-symbol", "step", "--json").output)
    for candidate in payload["ambiguity"]["candidates"]:
        for field in CANDIDATE_FIELDS:
            assert field in candidate, (field, candidate)
        assert set(candidate["range"]) == {"start_line", "end_line"}
        assert candidate["decl_hash"].startswith("sha256:")
        assert candidate["language"] == "python"
        assert candidate["kind"] == "function"
    # Two identities: the key is (file, qualified_name, type, decl_hash), and
    # these two declarations are textually identical, so the FILE half of the
    # key is what separates them — the record's deliberate choice.
    candidates = payload["ambiguity"]["candidates"]
    assert len({(c["file"], c["decl_hash"]) for c in candidates}) == 2


def test_one_definition_carries_match_count_and_no_ambiguity_block(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    payload = json.loads(
        _invoke(project, "find-symbol", "run_alpha", "--json").output)
    assert payload["match_count"] == 1
    assert "ambiguity" not in payload


# ---------------------------------------------------------------------------
# a command that must act on ONE symbol
# ---------------------------------------------------------------------------

def test_a_duplicated_name_refuses_with_two_candidates_never_one(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-definition", "step", "--exactly-one")
    assert result.exit_code == 2, result.output
    assert "ArgumentError" in result.output
    assert "resolves to 2 symbols" in result.output
    # Both candidates are listed, with the disambiguators that exist.
    assert "alpha.py:1-2" in result.output
    assert "beta.py:1-2" in result.output
    assert "--container" in result.output


def test_the_refusal_carries_the_candidates_as_data_in_json_mode(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-definition", "step", "--exactly-one",
                     "--json")
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["ErrorType"] == "ArgumentError"
    assert len(payload["candidates"]) == 2
    assert {c["file"] for c in payload["candidates"]} == {"alpha.py",
                                                          "beta.py"}


def test_the_resolver_refuses_with_an_argument_error(ambiguous_rig):
    """The shared resolver, not just the command, refuses."""
    from code_indexer import identity

    core, _, entry, _ = ambiguous_rig
    m = core.read_manifest(entry.slug)
    try:
        rows = m.definitions_of("step")
        assert len(rows) == 2
        with pytest.raises(AmbiguousSymbolError) as excinfo:
            identity.resolve_one("step", rows)
        assert isinstance(excinfo.value, ArgumentError)
        assert excinfo.value.candidates and len(excinfo.value.candidates) == 2
    finally:
        m.close()


def test_one_definition_resolves_without_a_refusal(ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-definition", "run_alpha", "--exactly-one",
                     "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [d["file"] for d in payload["definitions"]] == ["caller_alpha.py"]


# ---------------------------------------------------------------------------
# the graph commands union over every candidate and label each hit
# ---------------------------------------------------------------------------

def _direct_hits(payload: dict) -> list[dict]:
    """The level-0 children of every definition root."""
    hits = []
    for root in payload["roots"]:
        hits.extend(root["children"])
    return hits


def test_find_callers_unions_over_both_candidates_and_labels_each_hit(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-callers", "step", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    # Both candidates are in the answer, and the envelope says there are two.
    assert payload["match_count"] == 2
    assert len(payload["ambiguity"]["candidates"]) == 2
    root_labels = {f"{r['file']}:{r['line']}-{r['line'] + 1}"
                   for r in payload["roots"]}
    assert root_labels == {"alpha.py:1-2", "beta.py:1-2"}

    hits = _direct_hits(payload)
    assert hits, payload
    assert all(hit["targets"] for hit in hits), hits
    target_labels = {label for hit in hits for label in hit["targets"]}
    assert target_labels == root_labels, target_labels

    # The import edge is attributable: caller_alpha imports alpha, so that hit
    # names ONE target; the name-level call row names both, because the store
    # cannot say which definition it means.
    import_hits = [h for h in hits if h["relationship"].startswith("import:")]
    assert import_hits, hits
    for hit in import_hits:
        assert len(hit["targets"]) == 1, hit
    assert {h["file"] for h in import_hits} == {"caller_alpha.py",
                                               "caller_beta.py"}
    ref_hits = [h for h in hits if not h["relationship"].startswith("import:")]
    assert ref_hits and all(len(h["targets"]) == 2 for h in ref_hits), ref_hits


def test_find_callers_text_prints_the_target_labels(ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-callers", "step")
    assert result.exit_code == 0, result.output
    assert "targets: alpha.py:1-2" in result.output
    assert "beta.py:1-2" in result.output
    # The tree alone does not say the name had two definitions; the one-line
    # sentence does, as `find-symbol` text mode already did for its answer.
    assert "2 definitions match 'step'" in result.output
    assert "ambiguous" in result.output


def test_find_callees_text_says_the_subject_name_was_ambiguous(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-callees", "step")
    assert result.exit_code == 0, result.output
    assert "2 definitions match 'step'" in result.output


def test_a_single_definition_says_nothing_about_ambiguity(ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-callers", "run_alpha")
    assert result.exit_code == 0, result.output
    assert "definitions match" not in result.output


def test_find_callees_labels_the_definition_each_callee_came_from(
        ambiguous_rig):
    _, _, _, project = ambiguous_rig
    result = _invoke(project, "find-callees", "run_alpha", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["match_count"] == 1
    assert "ambiguity" not in payload
    # A single candidate: no per-hit target label is needed, so none is made
    # up — the label exists to disambiguate, and there is nothing to pick.
    assert all(not hit["targets"] for hit in _direct_hits(payload))
