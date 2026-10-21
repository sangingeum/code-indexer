"""Publishing-hygiene tests (offline; part of the default suite).

`scripts/publishing_hygiene_lint.py` is the mechanical form of the publishing
rules. This module keeps it honest in both directions:

- the tracked tree is clean, so the rule needs nobody's memory;
- each detector actually fires, so a green tree is not a green because the
  check stopped working;
- the surface split holds: authored records are governed, capture data keeps
  its payload except for absolute host paths.

Fixtures are assembled at run time on purpose: a literal host path or profile
name in this file would trip the very check it exercises.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "publishing_hygiene_lint.py"

# Assembled, never written as a literal (see the module docstring).
HOST_PATH = os.sep.join(["", "home", "hygiene" + "probe", "src", "app.py"])
SYNTHETIC_PATH = os.sep.join(["", "home", "x", "proj"])
INTERNAL_NAME = "sol" + "omon"
CARD_ID = "T-" + f"{1:06d}"


def _lint():
    spec = importlib.util.spec_from_file_location("publishing_hygiene_lint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan(path: Path, rel: str) -> list[str]:
    found = _lint()._scan(path, Path(rel))
    assert found is not None, f"{rel} was skipped unexpectedly"
    return found


def _classes(found: list[str]) -> set[str]:
    return {line.split(": ")[1] for line in found}


def test_tracked_tree_is_clean(capsys):
    """The whole tracked tree must pass its own check, this file included."""
    exit_code = _lint().main(["--root", str(REPO)])
    output = capsys.readouterr().out
    if exit_code != 0:
        pytest.fail("publishing hygiene violations in the tracked tree:\n" + output)


def test_absolute_host_path_and_name_are_flagged(tmp_path):
    record = tmp_path / "record.md"
    record.write_text(
        f"Evidence: {HOST_PATH}\nAuthor note: {INTERNAL_NAME} reviewed this.\n",
        encoding="utf-8",
    )
    found = _scan(record, "record.md")
    assert _classes(found) == {"absolute-host-path", "internal-profile-name"}
    assert [line.split(":")[1] for line in found] == ["1", "2"]
    # The report names the class, never the forbidden name itself.
    assert INTERNAL_NAME not in "\n".join(found)


def test_synthetic_fixture_is_not_flagged(tmp_path):
    record = tmp_path / "test_fixture.py"
    record.write_text(f'ROOT = "{SYNTHETIC_PATH}"\n', encoding="utf-8")
    assert _scan(record, "test_fixture.py") == []


def test_word_containing_a_name_is_not_flagged(tmp_path):
    record = tmp_path / "record.md"
    record.write_text("Several servers were covered by the same rule.\n",
                      encoding="utf-8")
    assert _scan(record, "record.md") == []


def test_card_id_is_flagged_in_records_only(tmp_path):
    record = tmp_path / "record.md"
    record.write_text(f"Card {CARD_ID} was read only.\n", encoding="utf-8")
    assert _classes(_scan(record, "record.md")) == {"board-card-id"}

    capture = tmp_path / "runs" / "capture.json"
    capture.parent.mkdir()
    capture.write_text(f'{{"board": "{CARD_ID}"}}\n', encoding="utf-8")
    assert _scan(capture, "runs/capture.json") == []


def test_capture_keeps_names_but_not_host_paths(tmp_path):
    capture = tmp_path / "runs" / "capture.txt"
    capture.parent.mkdir()
    capture.write_text(
        f"{INTERNAL_NAME}/review-2026-09-25.md\n{HOST_PATH}\n",
        encoding="utf-8",
    )
    found = _scan(capture, "runs/capture.txt")
    assert _classes(found) == {"absolute-host-path"}
    assert [line.split(":")[1] for line in found] == ["2"]


def test_hash_name_prints_the_digest(capsys):
    assert _lint().main(["--hash-name", "example"]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b"example").hexdigest()
