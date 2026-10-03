"""CI-10 outcome tests: eval-gated contextual header and chunker changes.

The richer v2 header (qualified symbol/type + signature + docstring) and the
statement-boundary chunk split were BOTH evaluated against this repository's
42-query set and did not clear the retrieval-quality gate — the header
regressed overall (MRR 0.6136 -> 0.5883, nDCG 0.6848 -> 0.6553) and the
chunker split was a wash (8 improved / 8 regressed, MRR -0.019). Both were
reverted per the gate; the tests below pin the surviving, non-gated pieces:

- EMBED_FORMAT stays contextual-header-v1 (the eval history lives in the
  embed_text module docstring and the commit message table).
- CHUNK_MAX_CHARS env seam exists (default 1000 unchanged) for future
  eval runs of larger caps.
- Built-in lockfile/minified/generated ignore defaults skip whole files
  that carry no reviewable code (complementary to the ranking layer's
  data-chunk down-weight).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.chunker import chunk_max_chars  # noqa: E402
from code_indexer.embed_text import EMBED_FORMAT, embed_text  # noqa: E402
from code_indexer.fingerprint import Fingerprint, fingerprint_mismatches  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402


def test_embed_format_stays_v1_after_failed_eval():
    assert EMBED_FORMAT == "contextual-header-v1"


def test_v1_header_shape_stable():
    source = ('def refresh(self):\n'
              '    """Refresh the OAuth token if expired."""\n'
              '    return self.token\n')
    (chunk,) = ts_chunker.chunk_text("x.py", source)
    text = embed_text("src/net/session.py", chunk)
    # v1: path + symbol only — no signature/doc header lines.
    assert text.startswith("src/net/session.py\n")
    assert "symbol: refresh" in text
    assert "signature:" not in text and "doc:" not in text
    assert "Refresh the OAuth token" in text  # body intact


def test_deterministic_for_identical_input():
    source = 'def a():\n    """Do a thing."""\n    return 1\n'
    (c1,) = ts_chunker.chunk_text("f.py", source)
    (c2,) = ts_chunker.chunk_text("f.py", source)
    assert embed_text("f.py", c1) == embed_text("f.py", c2)


def test_chunk_max_chars_env_override(monkeypatch):
    assert chunk_max_chars() == 1000  # default unchanged
    monkeypatch.setenv("CHUNK_MAX_CHARS", "1500")
    assert chunk_max_chars() == 1500
    monkeypatch.setenv("CHUNK_MAX_CHARS", "bogus")
    assert chunk_max_chars() == 1000  # bad value falls back
    monkeypatch.setenv("CHUNK_MAX_CHARS", "10")
    assert chunk_max_chars() == 200   # floor


def test_builtin_generated_lockfile_ignores(tmp_path):
    from code_indexer.scanner import scan_project
    for name in ("package-lock.json", "yarn.lock", "bundle.min.js",
                 "gen_pb2.py", "types_generated.ts", "keep.py"):
        (tmp_path / name).write_text("x = 1\n")
    scanned = {f.path for f in scan_project(str(tmp_path))}
    assert "keep.py" in scanned
    for skipped in ("package-lock.json", "yarn.lock", "bundle.min.js",
                    "gen_pb2.py", "types_generated.ts"):
        assert skipped not in scanned, skipped


def test_format_version_change_flags_reindex_by_design():
    """A future v2 header bump must keep flagging old indexes via the
    CI-02 fingerprint — this pins the mechanism the eval gate relies on."""
    v1_index = Fingerprint("m", 4096, "contextual-header-v1", 1)
    hypothetical_v2 = Fingerprint("m", 4096, "contextual-header-v2", 1)
    diffs = fingerprint_mismatches(v1_index, hypothetical_v2)
    assert any("embed_text_version" in d for d in diffs)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))