"""Publishing-hygiene tests (offline; part of the default suite).

`scripts/publishing_hygiene_lint.py` is the mechanical form of the publishing
rules. This module keeps it honest in both directions:

- the tracked tree is clean, so the rule needs nobody's memory;
- each detector actually fires, so a green tree is not a green because the
  check stopped working;
- the surface split holds: every authored surface is governed — a record under
  `runs/` included — and capture data keeps its payload except for absolute
  host paths.

Fixtures are assembled at run time on purpose: a literal host path or a
roster name in this file would trip the very check it exercises. The name rule
is pinned by injecting a synthetic digest into the loaded module's set, so
this file never spends the secrecy the digest roster buys.

One import is deliberate: the sanitiser test loads
`runs/issue22/reverify/sanitize_capture.py`, a historical record artifact
committed with the round that produced it. Nothing else imports it — it is
evidence, not a library, and the test exists so the artifact keeps working.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "publishing_hygiene_lint.py"
CAPTURE_SANITIZER = REPO / "runs" / "issue22" / "reverify" / "sanitize_capture.py"

# Assembled, never written as a literal (see the module docstring).
HOST_PATH = os.sep.join(["", "home", "hygiene" + "probe", "src", "app.py"])
SYNTHETIC_PATH = os.sep.join(["", "home", "x", "proj"])
CARD_ID = "T-" + f"{1:06d}"

# A probe name that is not on the protected roster. Its digest is injected into
# the loaded module, so the matching path is tested without naming anyone the
# digest list protects.
PROBE_NAME = "injected" + "probe"

# The roster the digest set shipped with: at least this many entries must
# remain, and each must be a whole sha256 digest.
ROSTER_SIZE_AT_INTRODUCTION = 11

# Home-path segments that must be reported. Every one of these is a plausible
# real account name, and three of them carry a character the former
# metacharacter exemption swallowed outright (`$`, `+`, `,`); the rest pin the
# ordinary account-name characters the rule must keep treating as a path.
REPORTED_ACCOUNT_SEGMENTS = {
    "plain": "probe" + "account",
    "dotted": "probe" + "." + "account",
    "trailing-dollar": "probe" + "$",
    "plus": "probe" + "+" + "account",
    "comma": "probe" + "," + "account",
    "dash": "probe" + "-" + "account",
    "underscore": "probe" + "_" + "account",
    "bracket-not-leading": "probe" + "]",
}

# Segments that are pattern text and stay exempt: only a LEADING `[`, `{` or
# `<` earns the exemption, which is all the tree's one such line needs.
EXEMPT_SEGMENT_LEADERS = {
    "regex-character-class": "[" + "a-z0-9" + "]" + "+",
    "brace-placeholder": "{" + "account" + "}",
    "angle-placeholder": "<" + "account" + ">",
}


def _home_path(segment: str) -> str:
    """A host home path built from a segment, with no literal path in the file."""
    return os.sep.join(["", "home", segment]) + "/"


def _lint(probe_name: str | None = None):
    spec = importlib.util.spec_from_file_location("publishing_hygiene_lint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if probe_name is not None:
        digest = hashlib.sha256(probe_name.lower().encode()).hexdigest()
        setattr(module, "PROFILE_NAME_DIGESTS", frozenset(
            set(module.PROFILE_NAME_DIGESTS) | {digest}))
    return module


def _scan(path: Path, rel: str, probe_name: str | None = None) -> list[str]:
    found = _lint(probe_name)._scan(path, Path(rel))
    assert found is not None, f"{rel} was skipped unexpectedly"
    return found


def _sanitizer():
    spec = importlib.util.spec_from_file_location(
        "sanitize_capture", CAPTURE_SANITIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        f"Evidence: {HOST_PATH}\nAuthor note: {PROBE_NAME} reviewed this.\n",
        encoding="utf-8",
    )
    found = _scan(record, "record.md", PROBE_NAME)
    assert _classes(found) == {"absolute-host-path", "internal-profile-name"}
    assert [line.split(":")[1] for line in found] == ["1", "2"]
    # The report names the class, never the forbidden name itself.
    assert PROBE_NAME not in "\n".join(found)


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

    # Capture-side fixture carries the capture suffix, so the exemption is
    # pinned to the directory rule rather than to a file type the rule never
    # governed.
    capture = tmp_path / "runs" / "capture.txt"
    capture.parent.mkdir()
    capture.write_text(f"board: {CARD_ID}\n", encoding="utf-8")
    assert _scan(capture, "runs/capture.txt") == []


def test_record_under_runs_is_governed(tmp_path):
    """A Markdown record under `runs/` is authored, not capture data.

    This is the regression that would republish cleaned round records: a
    directory-shaped exemption would let every record under `runs/` through.
    """
    record = tmp_path / "runs" / "issue27-publishing-hygiene" / "review.md"
    record.parent.mkdir(parents=True)
    record.write_text(
        f"Note: {PROBE_NAME} reviewed this. Card {CARD_ID}.\n", encoding="utf-8")
    found = _scan(record, "runs/issue27-publishing-hygiene/review.md", PROBE_NAME)
    assert _classes(found) == {"internal-profile-name", "board-card-id"}
    assert [line.split(":")[1] for line in found] == ["1", "1"]


def test_capture_keeps_names_but_not_host_paths(tmp_path):
    capture = tmp_path / "runs" / "capture.txt"
    capture.parent.mkdir()
    capture.write_text(
        f"{PROBE_NAME}/review-2026-09-25.md\n{HOST_PATH}\n",
        encoding="utf-8",
    )
    found = _scan(capture, "runs/capture.txt", PROBE_NAME)
    assert _classes(found) == {"absolute-host-path"}
    assert [line.split(":")[1] for line in found] == ["2"]


def test_digest_roster_is_well_formed():
    """Every digest is 64 lowercase hex and the set has not shrunk.

    A hand-pasted digest that is wrong or truncated would silently stop
    matching, so the shape is asserted rather than assumed.
    """
    digests = _lint().PROFILE_NAME_DIGESTS
    assert len(digests) >= ROSTER_SIZE_AT_INTRODUCTION
    for digest in digests:
        assert re.fullmatch(r"[0-9a-f]{64}", digest), digest


def test_hash_name_prints_the_digest(capsys):
    assert _lint().main(["--hash-name", "example"]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b"example").hexdigest()


@pytest.mark.parametrize(
    ("label", "segment"), sorted(REPORTED_ACCOUNT_SEGMENTS.items()))
def test_account_name_shapes_are_reported(tmp_path, label, segment):
    """A segment is a candidate account name whatever it carries, unless it
    begins with `[`, `{` or `<` — so `$`, `+`, `,`, `-` and `_` segments fire,
    and a bracket that merely appears inside the segment does not exempt it."""
    record = tmp_path / "record.md"
    home = _home_path(segment)
    record.write_text(f"path: {home}src/app.py\n", encoding="utf-8")
    assert _scan(record, "record.md") == [
        f"record.md:1: absolute-host-path: {home}"], label


@pytest.mark.parametrize(
    ("label", "segment"), sorted(EXEMPT_SEGMENT_LEADERS.items()))
def test_pattern_text_segments_are_exempt(tmp_path, label, segment):
    """A segment beginning with `[`, `{` or `<` is a regex class or a
    placeholder, not an account, and stays unreported."""
    record = tmp_path / "record.md"
    record.write_text(f"pattern: {_home_path(segment)}src/app.py\n",
                      encoding="utf-8")
    assert _scan(record, "record.md") == [], label


def test_capture_sanitizer_rewrites_only_this_hosts_prefixes():
    """The sanitiser placeholders this checkout and this account home, and
    leaves a foreign machine's path exactly as it found it.

    The sanitiser is loaded from `runs/issue22/reverify/` on purpose: it is a
    historical record artifact kept working, not an importable utility (see the
    module docstring).
    """
    sanitize = _sanitizer().sanitize_capture
    repo_path = str(REPO / "src" / "code_indexer" / "cli.py")
    home_path = str(Path.home() / ("elsewhere" + "-checkout") / "uv.lock")
    foreign_path = os.sep.join(
        ["", "home", "foreign" + "account", "checkout", "README.md"])
    text = f"repo {repo_path}\nhome {home_path}\nforeign {foreign_path}\n"
    assert sanitize(text) == (
        "repo <repo>/src/code_indexer/cli.py\n"
        "home <home>/elsewhere-checkout/uv.lock\n"
        f"foreign {foreign_path}\n"
    )
