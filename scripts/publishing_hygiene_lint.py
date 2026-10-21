#!/usr/bin/env python3
"""Publishing-hygiene check over the content this repository publishes.

The project's publishing rules (see AGENT.md) keep three classes of material
out of published content. This script enforces them mechanically:

  absolute-host-path      a path from the machine that produced the material is
                          never publishable, in any tracked file. A short
                          allow-list of synthetic account-home directories is
                          exempt, because unit tests need plausible fixtures:
                          the account segments x, y, user, example, test,
                          alice and bob. A segment that looks like a
                          placeholder or a regex fragment (it carries a
                          metacharacter) is pattern text, not a real path, and
                          is not reported. Windows user paths and smb shares
                          are caught too.
  internal-profile-name   internal profile names are forbidden in authored
                          surfaces. The names are held as sha256 digests, so
                          the check forbids them without publishing the roster
                          it protects; `--hash-name NAME` prints a digest to
                          add. Matching is whole-word and case-insensitive, so
                          a word that merely contains a name is not flagged.
  board-card-id           internal board card ids (a T- prefix plus six digits)
                          are forbidden in authored surfaces.

Surface split. "Authored" means the file types prose, code, scripts, CI config
and records are written in: .md, .py, .sh, .yml, .yaml, .toml, .cs. Capture
data — a .json, .txt or .log file under runs/ — is quoted evidence about
another repository: its payload keeps whatever names it holds, because
redacting it would falsify the record. Absolute host paths are the exception
and are checked in capture data as well: a path belonging to the machine that
ran a capture is never legitimate, whatever file holds it.

Discovery. By default every tracked path is scanned (`git ls-files` at the
repository root). Untracked local evidence is out of scope by design — a file
that was never published cannot leak. `--paths P ...` scans explicit paths
instead; the pre-commit hook passes the staged files that way. Binary files (a
NUL byte), files over 2 MB, and anything under .git/, .venv/,
.pytest_cache/ or .ruff_cache/ are skipped, and the summary always reports the
counts, so a skip is never a silent pass.

Output is one line per violation, `path:line: class: detail`, then a summary.
Exit 0 clean, 1 on any violation, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
SKIP_DIR_NAMES = frozenset({".git", ".venv", ".pytest_cache", ".ruff_cache"})
AUTHORED_SUFFIXES = frozenset({".md", ".py", ".sh", ".yml", ".yaml", ".toml", ".cs"})
CAPTURE_SUFFIXES = frozenset({".json", ".txt", ".log"})
CAPTURE_ROOT = "runs"

# Synthetic account-home directories that unit-test fixtures may use. These are
# not real accounts on any host this repository is built on: the watcher tests
# cover-overlap checks use a pair of single-letter roots.
SYNTHETIC_SEGMENTS = frozenset(
    {"x", "y", "user", "example", "test", "alice", "bob"})

# sha256 of each forbidden name, lowercased. Digests keep the roster itself out
# of the repository; `--hash-name NAME` prints one to append here.
PROFILE_NAME_DIGESTS = frozenset({
    "8b63510ba2c2ce7fba51da1f0e86a98385edb7eaa074bae9f7642669fbe2cf40",
    "c7f6d322bc205f26de153999e8d923b63b9261ce34bcb0ef1b9c711f843e05d5",
    "084328c53a9de563b83b23b6f500521ff442af7cfe60f104574c2c8cce22cf2a",
    "e010fd1ce1acc173e3b4835b7635f8d4600d774869102adb5cb7b5d7895649ba",
    "70d98166acace7c3de03b6452dc57ec1abaccb8f774289e8eb0be756367a6775",
    "eda508206872a4068028fa3e818aca129299ebe1c4cda872d076c0175b0b4863",
    "55579b557896d0ce1764c47fed644f9b35f58bad620674af23f356d80ed0c503",
    "c541435befefd132b09d535750841798d595082f8107c409a254fd4d55787ccc",
    "b9f633dc43572056c554fd73e0906140b698fab76315cdc90e5319255f0008b1",
    "accb7fc4d0144dcaa760e485a8100fc977f3404d3bfdc8e589384f66ca934330",
    "3c28d0b1b4e928435aa8c0095a6c9657e5f422479f4c27f5d85a206a9609897c",
})

# Account-home roots and the shapes that only appear on a developer machine.
_HOME_ROOTS = "|".join(("home", "Users"))
HOME_PATH_RE = re.compile(r"/(?:%s)/([^/\s\"'`]+)/" % _HOME_ROOTS)
WINDOWS_USER_RE = re.compile(
    r"(?i)[a-z]:[\\/]{1,2}(?:users|documents and settings)[\\/]")
SMB_RE = re.compile(r"(?i)smb:/{2}")
CARD_ID_RE = re.compile(r"\b" + "T" + r"-[0-9]{6}\b")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# A home-path segment carrying one of these is pattern text or a placeholder
# (a regex character class, a brace placeholder), not a real account name.
_PLACEHOLDER_CHARS = frozenset("[]{}()<>*+?^$|\\,")


def _digest(name: str) -> str:
    return hashlib.sha256(name.lower().encode()).hexdigest()


def _is_authored(rel: Path) -> bool:
    """True for the surfaces the name and card-id rules govern."""
    if rel.suffix.lower() not in AUTHORED_SUFFIXES:
        return False
    return not _is_capture(rel)


def _is_capture(rel: Path) -> bool:
    parts = rel.parts
    if not parts or parts[0] != CAPTURE_ROOT:
        return False
    return rel.suffix.lower() in CAPTURE_SUFFIXES


def _inside_skipped_dir(rel: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in rel.parts)


def _relative(path: Path, root: Path) -> Path:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return path


def _check_absolute_path(line: str) -> list[str]:
    details: list[str] = []
    for match in HOME_PATH_RE.finditer(line):
        segment = match.group(1)
        if segment.lower() in SYNTHETIC_SEGMENTS:
            continue
        if any(char in _PLACEHOLDER_CHARS for char in segment):
            continue
        details.append(match.group(0))
    for pattern in (WINDOWS_USER_RE, SMB_RE):
        for match in pattern.finditer(line):
            details.append(match.group(0))
    return details


def _check_profile_name(line: str) -> list[str]:
    details: list[str] = []
    for match in TOKEN_RE.finditer(line):
        token = match.group(0)
        if _digest(token) in PROFILE_NAME_DIGESTS:
            details.append(f"forbidden internal name at column {match.start() + 1}")
    return details


def _check_card_id(line: str) -> list[str]:
    return [match.group(0) for match in CARD_ID_RE.finditer(line)]


def _scan(path: Path, rel: Path) -> list[str] | None:
    """Return violation lines for one file, or None when the file is skipped."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{rel}:0: read-error: {exc}"]
    if len(raw) > MAX_BYTES:
        return None
    if b"\x00" in raw:
        return None
    authored = _is_authored(rel)
    violations: list[str] = []
    for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
        for detail in _check_absolute_path(line):
            violations.append(f"{rel}:{lineno}: absolute-host-path: {detail}")
        if not authored:
            continue
        for detail in _check_profile_name(line):
            violations.append(f"{rel}:{lineno}: internal-profile-name: {detail}")
        for detail in _check_card_id(line):
            violations.append(f"{rel}:{lineno}: board-card-id: {detail}")
    return violations


def _tracked_paths(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"publishing hygiene: cannot list tracked files: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return [Path(name) for name in result.stdout.decode().split("\0") if name]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="publishing_hygiene_lint",
        description=(
            "Check published content for absolute host paths, internal profile "
            "names and board card ids. By default every tracked path is scanned "
            "(git ls-files); untracked local evidence is out of scope by design."
        ),
        epilog=(
            "Synthetic account-home fixtures are allowed for these account "
            "segments only: " + ", ".join(sorted(SYNTHETIC_SEGMENTS)) + "."
        ),
    )
    parser.add_argument("--paths", nargs="+", metavar="P",
                        help="scan these paths instead of the tracked tree")
    parser.add_argument("--root", metavar="DIR", default=None,
                        help="repository root (default: current directory)")
    parser.add_argument("--hash-name", metavar="NAME", default=None,
                        help="print the digest to add to the forbidden-name set")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.hash_name is not None:
        print(_digest(args.hash_name))
        return 0

    root = Path(args.root) if args.root else Path.cwd()
    if not root.is_dir():
        print(f"publishing hygiene: not a directory: {root}", file=sys.stderr)
        return 2

    if args.paths:
        candidates = [Path(name) for name in args.paths]
    else:
        candidates = _tracked_paths(root)

    scanned = 0
    skipped = 0
    violations: list[str] = []
    for candidate in sorted(candidates, key=lambda p: str(p)):
        path = candidate if candidate.is_absolute() else root / candidate
        rel = _relative(path, root)
        if _inside_skipped_dir(rel):
            skipped += 1
            continue
        if not path.is_file():
            print(f"publishing hygiene: not a file: {candidate}", file=sys.stderr)
            return 2
        found = _scan(path, rel)
        if found is None:
            skipped += 1
            continue
        scanned += 1
        violations.extend(found)

    for line in violations:
        print(line)
    print(f"publishing hygiene: scanned={scanned} skipped={skipped} "
          f"violations={len(violations)} (untracked files are out of scope)")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
