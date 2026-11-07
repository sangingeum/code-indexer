"""A file the sensitive layer withholds is not a staleness reason.

The index pass drops a sensitive file on purpose and writes no manifest row
for it, so the tree-vs-manifest diff saw a scanned path with no row and
reported it as ``added`` on every read -- the banner every reader learned to
ignore. These tests pin the corrected state:

1. after a real index pass the skipped file leaves the verdict FRESH and is
   named as ``skipped`` with the layer that fired;
2. a sensitive file that CHANGES is still not a staleness reason (it is still
   withheld, and it still has no row);
3. a genuinely added non-sensitive file still reports ``added`` -- the
   negative control that keeps the skip from becoming a blind spot;
4. a removed file still reports ``deleted``;
5. the JSON block carries the count and the reason, and names neither the
   secret material nor a fabricated row.

The secret fixtures are assembled from fragments on purpose: a literal key id
here would make this module itself sensitive, and it would then have no rows
to test with.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402
from code_indexer.sensitive import (SKIP_REASON_CONTENT,  # noqa: E402
                                    SKIP_REASON_FILENAME)
from code_indexer import staleness  # noqa: E402

SECRET = "aws_access_key_id = " + "AKIA" + "IOSFODNN7" + "EXAMPLE\n"
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n"


@pytest.fixture()
def rig():
    """One real index pass over a tree that carries a sensitive file."""
    tmp = tempfile.mkdtemp(prefix="ci-skip-")
    project = tempfile.mkdtemp(prefix="ci-skip-proj-")
    with open(os.path.join(project, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    with open(os.path.join(project, "b.py"), "w") as f:
        f.write("def b():\n    return 2\n")
    with open(os.path.join(project, "secretive.py"), "w") as f:
        f.write("CONFIG = {\n    " + repr(SECRET) + \
                "\n}\n")
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                 index_root=tmp, embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=3,
                 watch_sweep_interval=300)
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name="skipproj")
    core = Core(cfg)
    core.registry = reg
    result = core.run_index(entry.slug, project, vectors="off")
    assert result["state"] == "idle", result
    yield core, entry, project
    reg.close()
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(project, ignore_errors=True)


def _manifest_paths(core: Core, slug: str) -> set[str]:
    m = core.manifest_for(slug)
    try:
        return set(m.all_files())
    finally:
        m.close()


def test_a_skipped_file_is_fresh_after_an_index_pass(rig):
    core, entry, project = rig
    # The pass really did withhold it: no row exists for the path.
    assert "secretive.py" not in _manifest_paths(core, entry.slug)

    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.reasons == ()
    assert verdict.reason_count == 0
    assert verdict.skipped_count == 1
    assert [(s.file, s.reason) for s in verdict.skipped] == [
        ("secretive.py", SKIP_REASON_CONTENT)]
    block = staleness.block(verdict)
    assert block["stale"] is False and block["stale_known"] is True
    assert block["skipped_count"] == 1
    assert block["skipped_summary"] == "1 file skipped (sensitive content)"


def test_a_changed_sensitive_file_is_not_stale(rig):
    core, entry, project = rig
    with open(os.path.join(project, "secretive.py"), "w") as f:
        f.write("TOKEN = " + repr(PEM) + "\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.reason_count == 0
    assert [(s.file, s.reason) for s in verdict.skipped] == [
        ("secretive.py", SKIP_REASON_CONTENT)]


def test_a_genuinely_added_file_still_reports_stale(rig):
    core, entry, project = rig
    with open(os.path.join(project, "c.py"), "w") as f:
        f.write("def c():\n    return 3\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [("added", "c.py")]
    # The skip is still reported beside the stale reason, not swallowed by it.
    assert verdict.skipped_count == 1


def test_a_removed_file_still_reports_deleted(rig):
    core, entry, project = rig
    os.remove(os.path.join(project, "b.py"))
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [("deleted", "b.py")]


def test_a_filename_skip_needs_no_content_verdict(rig):
    core, entry, project = rig
    os.mkdir(os.path.join(project, "keys"))
    with open(os.path.join(project, "keys", "deploy.pem"), "w") as f:
        f.write("placeholder, no key material\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    reasons = {s.file: s.reason for s in verdict.skipped}
    assert reasons["keys/deploy.pem"] == SKIP_REASON_FILENAME
    assert verdict.skipped_count == 2


def test_the_json_block_carries_the_count_and_no_secret_material(rig):
    core, entry, project = rig
    payload = json.dumps(staleness.block(core.staleness(entry)))
    assert '"skipped_count": 1' in payload
    assert "sensitive content" in payload
    assert "secretive.py" in payload
    # The state names the path and the layer -- never what it withheld.
    assert "AKIA" not in payload
    assert "PRIVATE KEY" not in payload
    assert "IOSFODNN7" not in payload
