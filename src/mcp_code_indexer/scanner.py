"""Project scanner: walk, filter, and hash files per the design (§5/§6).

Filters: .gitignore (pathspec, nested per-directory), .codeindexignore,
directory skip-list, size cap, binary sniff. Hash = sha256 of content —
NEVER mtime (mtimes lie after branch switches/checkout; the content-hash
diff is what makes incremental indexing correct and cheap).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass

import pathspec

logger = logging.getLogger("mcp-code-indexer.scanner")

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    "dist", "build", "target", ".idea", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "site-packages", ".tox", "coverage", "htmlcov",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp", ".pdf",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".class", ".so", ".o", ".a", ".dll", ".exe", ".bin", ".wasm",
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".mp3", ".mp4",
    ".avi", ".mov", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".svgz",
    ".DS_Store", ".lockb",
}

_GITIGNORE_CACHE: dict[str, pathspec.PathSpec] = {}


@dataclass
class ScannedFile:
    path: str           # repo-relative, POSIX separators
    abs_path: str
    size: int
    content_hash: str


def _load_ignore_spec(dirpath: str) -> pathspec.PathSpec:
    """Build a PathSpec from a directory's .gitignore + .codeindexignore."""
    cached = _GITIGNORE_CACHE.get(dirpath)
    if cached is not None:
        return cached
    lines: list[str] = []
    for name in (".gitignore", ".codeindexignore"):
        p = os.path.join(dirpath, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    lines.extend(f.read().splitlines())
            except OSError as exc:
                logger.warning("Cannot read %s: %s", p, exc)
    spec = pathspec.GitIgnoreSpec.from_lines(lines)
    _GITIGNORE_CACHE[dirpath] = spec
    return spec


def _is_binary(path: str) -> bool:
    """Null byte in first 8KB or known binary extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def _looks_utf8ish(path: str) -> bool:
    """Cheap textual sniff: decode a sample; binary-adjacent fails."""
    try:
        with open(path, "rb") as f:
            sample = f.read(4096)
        if not sample:
            return True
        sample.decode("utf-8", errors="strict")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def scan_project(root: str, max_file_bytes: int = 1_048_576) -> list[ScannedFile]:
    """Walk *root* and return kept files with sha256 content hashes.

    Honors nested .gitignore/.codeindexignore with per-directory pathspec
    composition (a file is checked against every spec between root and its
    parent directory, matching git's semantics closely enough for indexing).
    """
    root = os.path.abspath(root)
    results: list[ScannedFile] = []
    _GITIGNORE_CACHE.clear()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel_dir = os.path.relpath(dirpath, root)

        # Per-directory ignore composition: check this dir's own ignore files,
        # and prune dirnames in-place so we never descend into ignored trees.
        if rel_dir != ".":
            specs = []
            cur = dirpath
            while cur != root and len(cur) > len(root):
                specs.append(_load_ignore_spec(cur))
                cur = os.path.dirname(cur)
            dir_rel = rel_dir.replace(os.sep, "/") + "/"
            if any(spec.match_file(dir_rel) for spec in specs):
                dirnames[:] = []
                continue

        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fname in filenames:
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            # Check every ignore spec from root down to this directory
            # (per-directory composition, design Risk 5).
            specs = []
            cur = dirpath
            while True:
                specs.append(_load_ignore_spec(cur))
                if cur == root:
                    break
                cur = os.path.dirname(cur)
            if any(spec.match_file(rel_path) for spec in specs):
                logger.debug("skip (ignored): %s", rel_path)
                continue
            try:
                st = os.stat(abs_path)
                if not os.path.isfile(abs_path):
                    continue
            except OSError:
                continue
            if st.st_size > max_file_bytes:
                logger.debug("skip (too large): %s", rel_path)
                continue
            if _is_binary(abs_path):
                logger.debug("skip (binary): %s", rel_path)
                continue
            if not _looks_utf8ish(abs_path):
                logger.debug("skip (non-utf8): %s", rel_path)
                continue
            sha = _hash_file(abs_path)
            if sha is None:
                continue
            results.append(ScannedFile(rel_path, abs_path, st.st_size, sha))

    return results


def _hash_file(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
    except OSError as exc:
        logger.warning("Cannot hash %s: %s", path, exc)
        return None
    return "sha256:" + h.hexdigest()