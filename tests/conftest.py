"""Marker convention for live-backend tests (CI scaffolding).

Tests that need real Ollama/Qdrant are marked ``@pytest.mark.live`` and are
excluded from default runs (pyproject: ``addopts = -m 'not live'``); run them
explicitly with ``pytest -m live`` (needs backends up). Anything that runs
with stub embedders/stores stays unmarked.
"""

from __future__ import annotations

import pytest

# Re-export so tests can `from conftest import live` — keeps the marker
# registered in one place.
live = pytest.mark.live