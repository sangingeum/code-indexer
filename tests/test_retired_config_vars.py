"""Retired environment variables are accepted and inert.

``STALE_TTL`` once tuned how long an answer could be served without a fresh
look at the sources. Reads stopped probing, so nothing consumed it; the config
field and its integer parse are gone. A deployment that still exports the
variable must start and behave exactly as before -- and a value that is not a
number must no longer raise the way the old parse did.
"""

from __future__ import annotations

from code_indexer.config import RETIRED_ENV_VARS, load_config


def test_stale_ttl_is_declared_retired():
    assert "STALE_TTL" in RETIRED_ENV_VARS


def test_stale_ttl_is_not_a_config_field():
    assert not hasattr(load_config([]), "stale_ttl")


def test_setting_stale_ttl_changes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    monkeypatch.delenv("STALE_TTL", raising=False)
    baseline = load_config([])

    for raw in ("0", "60", "not-a-number"):
        monkeypatch.setenv("STALE_TTL", raw)
        assert load_config([]) == baseline, f"STALE_TTL={raw} changed the config"
