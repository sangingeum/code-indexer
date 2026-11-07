"""A file the sensitive layer withholds is not a staleness reason.

The index pass drops a sensitive file on purpose and writes no manifest row
for it, so the tree-vs-manifest diff saw a scanned path with no row and
reported it as ``added`` on every read -- the banner every reader learned to
ignore. These tests pin the corrected state:

1. after a real index pass the withheld file leaves the verdict FRESH and is
   named as ``withheld`` with the layer that fired;
2. a sensitive file that CHANGES is still not a staleness reason (it is still
   withheld, and it still has no row);
3. a genuinely added non-sensitive file still reports ``added`` -- the
   negative control that keeps the withholding from becoming a blind spot;
4. a removed file still reports ``deleted``;
5. the JSON block carries the count and the reason, and names neither the
   secret material nor a fabricated row;
6. the project's ``allow_sensitive`` override classifies nothing, because the
   next pass will write the row;
7. the re-check budget is charged in bytes read, so one huge new file cannot
   cost a later file its classification;
8. the summary drops the per-reason numeral unless both layers fired.

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


def _config(index_root: str, max_file_bytes: int = 1048576) -> Config:
    return Config(ollama_url="", qdrant_url="", embed_model="stub-model",
                  index_root=index_root, embed_batch=48, upsert_batch=256,
                  max_file_bytes=max_file_bytes, watch_debounce=3,
                  watch_sweep_interval=300)


def _write(project: str, name: str, text: str) -> None:
    with open(os.path.join(project, name), "w", encoding="utf-8") as f:
        f.write(text)


def _index(tmp: str, project: str, name: str, *,
           max_file_bytes: int = 1048576) -> tuple[Core, Registry, object]:
    """One real index pass over ``project``; returns core, registry, entry."""
    reg = Registry(os.path.join(tmp, "registry.db"))
    entry = reg.add(project, name=name)
    core = Core(_config(tmp, max_file_bytes))
    core.registry = reg
    result = core.run_index(entry.slug, project, vectors="off")
    assert result["state"] == "idle", result
    return core, reg, entry


@pytest.fixture()
def rig():
    """One real index pass over a tree that carries a sensitive file."""
    tmp = tempfile.mkdtemp(prefix="ci-skip-")
    project = tempfile.mkdtemp(prefix="ci-skip-proj-")
    _write(project, "a.py", "def a():\n    return 1\n")
    _write(project, "b.py", "def b():\n    return 2\n")
    _write(project, "secretive.py", "CONFIG = {\n    " + repr(SECRET) + "\n}\n")
    core, reg, entry = _index(tmp, project, "skipproj")
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


def _set_allow_sensitive(core: Core, slug: str, value: str) -> None:
    m = core.manifest_for(slug)
    try:
        m.set_meta("allow_sensitive", value)
    finally:
        m.close()


def test_a_withheld_file_is_fresh_after_an_index_pass(rig):
    core, entry, project = rig
    # The pass really did withhold it: no row exists for the path.
    assert "secretive.py" not in _manifest_paths(core, entry.slug)

    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.reasons == ()
    assert verdict.reason_count == 0
    assert verdict.withheld_count == 1
    assert verdict.unexamined_count == 0
    assert [(s.file, s.reason) for s in verdict.withheld] == [
        ("secretive.py", SKIP_REASON_CONTENT)]
    block = staleness.block(verdict)
    assert block["stale"] is False and block["stale_known"] is True
    assert block["withheld_count"] == 1
    assert block["withheld_summary"] == "1 file skipped (sensitive content)"


def test_a_changed_sensitive_file_is_not_stale(rig):
    core, entry, project = rig
    _write(project, "secretive.py", "TOKEN = " + repr(PEM) + "\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    assert verdict.reason_count == 0
    assert [(s.file, s.reason) for s in verdict.withheld] == [
        ("secretive.py", SKIP_REASON_CONTENT)]


def test_a_genuinely_added_file_still_reports_stale(rig):
    core, entry, project = rig
    _write(project, "c.py", "def c():\n    return 3\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [("added", "c.py")]
    # The withholding is still reported beside the stale reason, not swallowed.
    assert verdict.withheld_count == 1


def test_a_removed_file_still_reports_deleted(rig):
    core, entry, project = rig
    os.remove(os.path.join(project, "b.py"))
    verdict = core.staleness(entry)
    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [("deleted", "b.py")]


def test_a_filename_skip_needs_no_content_verdict(rig):
    core, entry, project = rig
    os.mkdir(os.path.join(project, "keys"))
    _write(project, os.path.join("keys", "deploy.pem"),
           "placeholder, no key material\n")
    verdict = core.staleness(entry)
    assert verdict.state == staleness.FRESH
    reasons = {s.file: s.reason for s in verdict.withheld}
    assert reasons["keys/deploy.pem"] == SKIP_REASON_FILENAME
    assert verdict.withheld_count == 2


def test_the_json_block_carries_the_count_and_no_secret_material(rig):
    core, entry, project = rig
    payload = json.dumps(staleness.block(core.staleness(entry)))
    assert '"withheld_count": 1' in payload
    assert "sensitive content" in payload
    assert "secretive.py" in payload
    # The state names the path and the layer -- never what it withheld.
    assert "AKIA" not in payload
    assert "PRIVATE KEY" not in payload
    assert "IOSFODNN7" not in payload


def test_the_summary_drops_the_numeral_for_one_reason_category(rig):
    core, entry, project = rig
    _write(project, "another.py", "TOKEN = " + repr(PEM) + "\n")
    block = staleness.block(core.staleness(entry))
    assert block["withheld_count"] == 2
    assert block["withheld_summary"] == "2 files skipped (sensitive content)"


def test_the_summary_names_both_categories_when_both_fired(rig):
    core, entry, project = rig
    os.mkdir(os.path.join(project, "keys"))
    _write(project, os.path.join("keys", "deploy.pem"), "placeholder\n")
    block = staleness.block(core.staleness(entry))
    assert block["withheld_summary"] == \
        "2 files skipped (1 sensitive content, 1 sensitive filename)"


def test_allow_sensitive_classifies_nothing(rig):
    """The override decides the diff exactly as it decides the pass."""
    core, entry, project = rig
    _set_allow_sensitive(core, entry.slug, "1")

    verdict = core.staleness(entry)
    # The next pass writes a row for it, so the diff must not call it withheld.
    assert verdict.state == staleness.STALE
    assert [(r.kind, r.file) for r in verdict.reasons] == [
        ("added", "secretive.py")]
    assert verdict.withheld_count == 0
    assert verdict.withheld == ()

    _set_allow_sensitive(core, entry.slug, "0")
    verdict = core.staleness(entry)
    # Unset, the same rowless path is the withheld state again.
    assert verdict.state == staleness.FRESH
    assert verdict.reasons == ()
    assert [(s.file, s.reason) for s in verdict.withheld] == [
        ("secretive.py", SKIP_REASON_CONTENT)]


def test_a_huge_new_file_does_not_spend_the_recheck_budget():
    """The budget is charged in bytes read, not in bytes on disk."""
    tmp = tempfile.mkdtemp(prefix="ci-budget-")
    project = tempfile.mkdtemp(prefix="ci-budget-proj-")
    _write(project, "a.py", "def a():\n    return 1\n")
    core, reg, entry = _index(tmp, project, "budgetproj",
                              max_file_bytes=64 * 1024 * 1024)
    try:
        # Sorted first and charged by SIZE, this would spend the whole 8 MiB
        # budget and leave the secret behind it unclassified.
        _write(project, "aa_big.py", "# filler\n" * (12 * 1024 * 1024 // 9))
        _write(project, "z_secret.py", "CONFIG = {" + repr(SECRET) + "}\n")
        verdict = core.staleness(entry)
        assert [(s.file, s.reason) for s in verdict.withheld] == [
            ("z_secret.py", SKIP_REASON_CONTENT)]
        assert ("added", "aa_big.py") in [(r.kind, r.file)
                                          for r in verdict.reasons]
        assert verdict.unexamined_count == 0
    finally:
        reg.close()
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)
