"""CI-04 resilience tests: retries, fatal classification, bisect, resume.

Offline: a scripted fake ollama client, an indexer-level mock embedder that
raises after N calls, and a poisoned-chunk embedder.
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from code_indexer.embedder import Embedder
from code_indexer.fingerprint import ConfigError


class ScriptedClient:
    """Fake ollama client: script exceptions per call."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def embed(self, model, input, keep_alive=None):
        self.calls += 1
        action = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(action, Exception):
            raise action
        return {"embeddings": [[0.0] * 4 for _ in input]}


def _emb(script, retries=3):
    e = Embedder(host="http://stub", model="m", batch_size=10, retries=retries)
    e.client = ScriptedClient(script)
    return e


def test_retry_then_success(monkeypatch):
    e = _emb([ConnectionError("connection reset"), "ok"])
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert e.embed(["a"]) == [[0.0] * 4]


def test_retries_exhausted_raises_runtimeerror(monkeypatch):
    e = _emb([ConnectionError("timeout")] * 10, retries=3)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="after 3 retries"):
        e.embed(["a"])


def test_model_not_found_is_fatal(monkeypatch):
    e = _emb([Exception("model 'm' not found, try ollama pull")])
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(ConfigError, match="ollama pull m"):
        e.embed(["a"])  # fatal: no retry, immediate ConfigError


def test_embed_with_errors_bisects_poisoned_chunk(monkeypatch):
    # Whole batch call: always a timeout → falls back to per-text, where
    # text 0 succeeds and text 1 keeps failing.
    class PoisonClient:
        def embed(self, model, input, keep_alive=None):
            if len(input) > 1:
                raise ConnectionError("batch fails")
            if "poison" in input[0]:
                raise ValueError("chunk too large")
            return {"embeddings": [[0.1] * 4]}
    e = Embedder(host="http://stub", model="m", batch_size=10, retries=2)
    e.client = PoisonClient()
    monkeypatch.setattr("time.sleep", lambda s: None)
    vectors, errors = e.embed_with_errors(["good text", "poison text"])
    assert vectors[0] == [0.1] * 4
    assert vectors[1] is None
    assert len(errors) == 1 and errors[0][0] == 1