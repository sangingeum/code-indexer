"""The provider contract suite -- the registration test (ADR-0006 s.1, s.3-s.7).

A provider is registered only after it passes here, and the suite is answered
by ``provider_stub.FixtureProvider``: no language server is installed, spawned
or probed by anything in this module. The three refusal shapes are asserted
by behaviour -- absent is exit 3 ``ProviderUnavailable``, unsupported is
exit 2 ``UnsupportedOperation`` with no edit produced, and a provider that
answers nothing in time is exit 3 ``ProviderTimeout``, never an empty answer.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

import provider_stub  # noqa: E402
from code_indexer import plans  # noqa: E402
from code_indexer import refactoring_provider as provider  # noqa: E402
from code_indexer.errors import (  # noqa: E402
    AmbiguousSymbolError,
    EXIT_BACKEND,
    EXIT_USAGE,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedOperationError,
)

ROOT = provider_stub.FIXTURE_DIR
SAMPLE = os.path.join(ROOT, "sample.py")
SUPPORTED_CAPS = {"resolve_symbol": "supported",
                  "find_references": "supported",
                  "prepare_rename": "supported"}


def session(*, capabilities=None, factory=None, deadline=5.0, start_budget=5.0):
    """One session over the fixture provider."""
    def build(root):
        return provider_stub.FixtureProvider(root, capabilities=capabilities)

    return provider.ProviderSession(
        ROOT, language="python", factory=factory or build,
        request_deadline=deadline, start_budget=start_budget)


def greet_declaration(sess):
    resolution = sess.resolve_symbol(provider.SymbolQuery(qualified_name="greet"))
    assert len(resolution.candidates) == 1
    return resolution.candidates[0]


# ---------------------------------------------------------------------------
# capabilities, and capability honesty
# ---------------------------------------------------------------------------

def test_the_capability_report_names_the_provider_and_its_version():
    report = session().capabilities()
    block = report.block()
    assert block["provider"] == {"name": "stub-fixture", "version": "1.0.0",
                                 "transport": "in-process"}
    assert set(block["operations"]) == set(provider.SEMANTIC_OPERATIONS)
    assert set(block["operations"].values()) <= set(provider.CAPABILITY_STATES)


def test_the_capability_report_says_rename_is_unsupported():
    """The risk the plan names as acceptable: this provider cannot rename."""
    report = provider_stub.capability_report()
    assert report["operations"]["prepare_rename"] == provider.UNSUPPORTED
    assert report["operations"]["resolve_symbol"] == provider.SUPPORTED
    # A capability report is data a caller can read without prose.
    assert report["provider"]["name"] == "stub-fixture"


def test_unknown_is_a_distinct_value_and_is_never_a_denial():
    caps = dict(SUPPORTED_CAPS, find_references=provider.UNKNOWN)
    sess = session(capabilities=caps)
    report = sess.capabilities()
    assert report.state("find_references") == provider.UNKNOWN
    assert report.state("find_references") not in (provider.SUPPORTED,
                                                   provider.UNSUPPORTED)
    # The request is attempted, and the provider's own answer is the answer.
    answer = sess.find_references(greet_declaration(sess))
    assert len(answer.locations) == 1


# ---------------------------------------------------------------------------
# the three refusal shapes, by behaviour
# ---------------------------------------------------------------------------

def test_an_absent_provider_is_provider_unavailable_at_exit_3():
    assert provider.registered_languages() == ()
    sess = provider.ProviderSession(ROOT, language="python")
    with pytest.raises(ProviderUnavailableError) as caught:
        sess.capabilities()
    assert caught.value.error_type == "ProviderUnavailable"
    assert caught.value.exit_code == EXIT_BACKEND
    message = str(caught.value)
    assert "Install route:" in message
    assert "python-lsp-server" in message
    assert "\n" not in message  # one line, as the record requires


def test_an_unsupported_operation_is_exit_2_and_produces_no_edit():
    sess = session()
    stub = sess._provider()  # the same instance the session will use
    declaration = greet_declaration(sess)
    with pytest.raises(UnsupportedOperationError) as caught:
        sess.prepare_rename(declaration, "hello")
    assert caught.value.error_type == "UnsupportedOperation"
    assert caught.value.exit_code == EXIT_USAGE
    # Refused before the provider was asked, and no edit exists to materialize.
    assert "prepare_rename" not in stub.calls


def test_a_provider_that_answers_nothing_in_time_is_a_timeout_at_exit_3():
    sess = provider.ProviderSession(
        ROOT, language="python",
        factory=lambda root: provider_stub.SilentAnswerProvider(
            root, capabilities=SUPPORTED_CAPS),
        request_deadline=0.05)
    stub = sess._provider()
    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError) as caught:
        sess.resolve_symbol(provider.SymbolQuery(qualified_name="greet"))
    elapsed = time.monotonic() - started
    assert caught.value.error_type == "ProviderTimeout"
    assert caught.value.exit_code == EXIT_BACKEND
    assert "cancelled" in str(caught.value).lower()
    assert elapsed < 0.5  # bounded waiting, not the provider's own sleep
    assert stub.cancelled is True  # cancelled, not left running


def test_a_provider_that_never_becomes_ready_is_unavailable_at_exit_3():
    sess = provider.ProviderSession(
        ROOT, language="python",
        factory=lambda root: provider_stub.SlowStartProvider(root),
        start_budget=0.05)
    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError) as caught:
        sess.capabilities()
    assert caught.value.exit_code == EXIT_BACKEND
    assert "become ready" in str(caught.value)
    assert time.monotonic() - started < 0.5


def test_a_provider_refusal_is_typed_and_is_not_retried_as_text_replacement():
    sess = session()
    declaration = sess.resolve_symbol(
        provider.SymbolQuery(qualified_name="farewell")).candidates[0]
    with pytest.raises(ProviderRefusalError) as caught:
        sess.find_references(declaration)
    assert caught.value.error_type == "ProviderRefusal"
    assert "no reference answer" in str(caught.value)
    # No fallback: the refusal is the whole answer, so nothing was materialized.
    assert not sess._provider().calls.count("prepare_rename")


# ---------------------------------------------------------------------------
# the documented results
# ---------------------------------------------------------------------------

def test_resolve_symbol_answers_by_name_and_by_position():
    sess = session()
    by_name = sess.resolve_symbol(provider.SymbolQuery(qualified_name="greet"))
    assert len(by_name.candidates) == 1
    candidate = by_name.candidates[0]
    assert candidate.path == "sample.py"
    assert candidate.qualified_name == "greet"
    assert (candidate.range.start_line, candidate.range.start_byte) == (1, 0)
    assert candidate.range.end_line == 2
    by_position = sess.resolve_symbol(
        provider.SymbolQuery(path="sample.py", line=1, character=0))
    assert by_position.candidates == by_name.candidates


def test_resolve_symbol_refuses_an_ambiguous_name_with_its_candidates():
    sess = session()
    with pytest.raises(AmbiguousSymbolError) as caught:
        sess.resolve_symbol(provider.SymbolQuery(qualified_name="duplicated"))
    assert caught.value.exit_code == EXIT_USAGE
    assert len(caught.value.candidates) == 2


def test_an_unknown_name_is_an_empty_answer_not_a_crash():
    resolution = session().resolve_symbol(
        provider.SymbolQuery(qualified_name="nothing_here"))
    assert resolution.candidates == ()


def test_find_references_answers_in_plan_coordinates():
    sess = session()
    declaration = greet_declaration(sess)
    answer = sess.find_references(declaration)
    assert len(answer.locations) == 1
    location = answer.locations[0]
    assert location.path == "sample.py"
    assert (location.range.start_line, location.range.start_byte) == (1, 4)
    assert (location.range.end_line, location.range.end_byte) == (1, 9)


def test_a_rename_edit_materializes_into_a_line_aligned_plan_edit():
    sess = session(capabilities=SUPPORTED_CAPS)
    declaration = greet_declaration(sess)
    rename = sess.prepare_rename(declaration, "hello")
    assert rename.provider.name == "stub-fixture"
    assert rename.edits[0].start.line == 0  # provider-native, pre-normalization
    tf = plans.read_text_file(ROOT, "sample.py")
    body = provider.materialize(tf, rename)
    assert body["provider"]["name"] == "stub-fixture"
    edits = body["edits"]
    assert edits[0]["kind"] == provider.PROVIDER_EDIT_KIND
    assert edits[0]["new_text"] == "def hello(name):"
    assert edits[0]["start_line"] == 1 and edits[0]["end_line"] == 1


# ---------------------------------------------------------------------------
# provenance, lifecycle, and what the interface must not touch
# ---------------------------------------------------------------------------

def test_every_semantic_answer_carries_its_provider_provenance():
    sess = session(capabilities=SUPPORTED_CAPS)
    resolution = sess.resolve_symbol(provider.SymbolQuery(qualified_name="greet"))
    references = sess.find_references(resolution.candidates[0])
    rename = sess.prepare_rename(resolution.candidates[0], "hello")
    for block in (resolution.block()["provider"],
                  references.block()["provider"],
                  rename.block()["provider"]):
        assert block == {"name": "stub-fixture", "version": "1.0.0",
                         "transport": "in-process"}


def test_the_provider_starts_lazily_and_is_reused_across_the_invocation():
    sess = session()
    assert sess.starts == 0
    assert sess._instance is None
    sess.capabilities()
    sess.resolve_symbol(provider.SymbolQuery(qualified_name="greet"))
    sess.resolve_symbol(provider.SymbolQuery(qualified_name="farewell"))
    assert sess.starts == 1
    assert sess._instance.starts == 1


def test_the_provider_is_terminated_at_close_and_no_daemon_survives():
    sess = session()
    sess.capabilities()
    stub = sess._instance
    sess.close()
    assert stub.closed is True
    assert sess._instance is None
    with pytest.raises(ProviderUnavailableError):
        sess.capabilities()


def test_one_session_serves_one_project_per_invocation():
    first = provider.session_for(ROOT)
    second = provider.session_for(ROOT)
    assert first is second
    other = provider.session_for(os.path.join(ROOT, "elsewhere"))
    assert other is not first
    provider.close_all()


def test_nothing_in_the_contract_path_spawns_a_process(monkeypatch):
    """No language server is installed, spawned or probed by this suite."""
    import subprocess

    def refuse(*args, **kwargs):
        raise AssertionError("the contract suite must not spawn a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    sess = session(capabilities=SUPPORTED_CAPS)
    declaration = greet_declaration(sess)
    sess.find_references(declaration)
    sess.prepare_rename(declaration, "hello")
    sess.close()


def test_the_index_storage_never_sees_a_provider_identifier():
    """ADR-0006 section 4: a provider's ids are not written into symbol rows."""
    source_path = os.path.join(os.path.dirname(__file__), "..", "src",
                              "code_indexer", "refactoring_provider.py")
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()
    for forbidden in ("from .store", "from .manifest", "manifest_for",
                      "INSERT INTO", "UPDATE ", "core.store", "core.manifest"):
        assert forbidden not in source, forbidden


def test_the_suite_needs_no_language_server_to_pass():
    """The stub is the only provider in this leg."""
    report = provider_stub.capability_report()
    assert report["provider"]["transport"] == "in-process"
    assert provider.registered_languages() == ()
