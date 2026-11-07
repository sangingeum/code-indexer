"""Project scanner: walk, filter, and hash files per the design (§5/§6).

Filters: .gitignore (pathspec, nested per-directory), .codeindexignore,
directory skip-list, size cap, binary sniff. Hash = sha256 of content —
NEVER mtime (mtimes lie after branch switches/checkout; the content-hash
diff is what makes incremental indexing correct and cheap).

A caller may pass a ``ScanBudget`` so a very large tree stops the walk and
reports that it stopped, instead of walking for minutes: the staleness check
uses this to degrade to ``unknown`` rather than silence.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass

import pathspec

logger = logging.getLogger("code-indexer.scanner")

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

# Built-in ignore defaults: lockfiles, minified bundles, generated code.
# Deliberately conservative — anything that is reviewable source stays
# indexed (the data-file ranking mitigation handles .json/.yaml/.toml).
_BUILTIN_IGNORE_LINES = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "uv.lock",
    "*.lock", "*.min.js", "*.min.css", "*.map",
    "*.pb.go", "*_pb2.py", "*_generated.*",
]


class ScanBudgetExceeded(Exception):
    """The walk exceeded its own budget; the caller reports it, never guesses."""


@dataclass(frozen=True)
class ScanBudget:
    """Bounds one walk. ``None`` on either field means unbounded there."""

    max_files: int | None = None
    max_seconds: float | None = None


@dataclass
class ScannedFile:
    path: str           # repo-relative, POSIX separators
    abs_path: str
    size: int
    content_hash: str
    mtime_ns: int | None = None  # stat fast-path bookkeeping
    inode: int | None = None


def _load_ignore_spec(dirpath: str) -> pathspec.PathSpec:
    """Build a PathSpec from a directory's .gitignore + .codeindexignore,
    plus the built-in generated/lockfile defaults.

    Built-in defaults (index-time skip, privacy work item overlap note: the
    ranking layer separately down-weights data-file CHUNKS — these globs skip
    whole generated/lock files that carry no reviewable code at all, so both
    layers are complementary, not duplicates):
        package-lock.json, yarn.lock, pnpm-lock.yaml, poetry.lock,
        Cargo.lock, go.sum, Gemfile.lock, composer.lock, uv.lock,
        *.min.js, *.min.css, *.map, *.pb.go, *_pb2.py, *_generated.*,
        *.lock
    """
    cached = _GITIGNORE_CACHE.get(dirpath)
    if cached is not None:
        return cached
    lines: list[str] = list(_BUILTIN_IGNORE_LINES)
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


def _ordered_specs(root: str, dirpath: str) -> list[pathspec.PathSpec]:
    """The ignore specs governing ``dirpath``, root first -- git's order."""
    chain = []
    cur = dirpath
    while True:
        chain.append(cur)
        if cur == root:
            break
        cur = os.path.dirname(cur)
    return [_load_ignore_spec(path) for path in reversed(chain)]


def _ignored(rel_path: str, specs: list[pathspec.PathSpec]) -> bool:
    """Git's composition: the last matching rule wins, deepest spec last.

    ``check_file`` reports the verdict AND the index of the pattern that
    decided it inside that spec, so a rule in a deeper file overrides one in
    a shallower file -- a negation included. This is what keeps the scan and
    ``git check-ignore`` agreeing on the repository's own exclusions.
    """
    ignored = False
    for spec in specs:
        checked = spec.check_file(rel_path)
        if checked.index is not None:
            ignored = bool(checked.include)
    return ignored


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


def scan_project(root: str, max_file_bytes: int = 1_048_576,
                 previous: dict[str, tuple[int, int, int, str]] | None = None,
                 include: list[str] | None = None,
                 priority: list[str] | None = None,
                 budget: ScanBudget | None = None,
                 ) -> list[ScannedFile]:
    """Walk ``root`` and return ScannedFile rows.

    ``previous`` maps path -> (size, mtime_ns, inode, content_hash) from the
    last completed pass. When a file's stat triple matches exactly, its
    recorded content hash is reused instead of re-reading/hashing the file
    (the stat fast-path; env PARANOID_HASH=1 disables it for
    filesystems/tools that preserve mtime, e.g. ``rsync -t``/``cp -p``).

    ``include`` (scoped indexing): gitignore-style patterns limiting
    the scan to a subtree (e.g. ``src/**``); None = whole project.
    ``priority``: matching files are returned FIRST (indexed before the
    rest of the pass).

    Nested ``.gitignore``/``.codeindexignore`` compose the way git composes
    them: the specs are consulted from the root outwards, and the LAST
    matching rule decides, so a deeper ``!pattern`` re-includes what a
    shallower spec excluded. A directory the composed verdict ignores is
    pruned, which also carries git's rule that nothing inside an excluded
    directory can be re-included: there is no walk left to re-include in.
    """
    previous = previous if previous is not None and \
        os.environ.get("PARANOID_HASH") not in ("1", "true") else {}
    root = os.path.abspath(root)
    results: list[ScannedFile] = []
    _GITIGNORE_CACHE.clear()
    walked = 0
    started = time.monotonic()

    def check_budget() -> None:
        """Stop the walk when its own budget is spent (never a silent long run)."""
        nonlocal walked
        walked += 1
        if budget is None:
            return
        if budget.max_files is not None and walked > budget.max_files:
            raise ScanBudgetExceeded(
                f"scan budget exceeded after {budget.max_files} entries")
        if (budget.max_seconds is not None and walked % 256 == 0
                and time.monotonic() - started > budget.max_seconds):
            raise ScanBudgetExceeded(
                f"scan budget exceeded after {budget.max_seconds:g}s")

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel_dir = os.path.relpath(dirpath, root)

        # Per-directory ignore composition: prune dirnames in-place so we
        # never descend into ignored trees. A directory's OWN ignore file
        # says nothing about the directory itself, so the deciding specs are
        # those from the root down to its parent, in git's order.
        if rel_dir != ".":
            dir_rel = rel_dir.replace(os.sep, "/") + "/"
            parent_specs = _ordered_specs(root, os.path.dirname(dirpath))
            if _ignored(dir_rel, parent_specs):
                dirnames[:] = []
                continue

        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        specs = _ordered_specs(root, dirpath)

        for fname in filenames:
            check_budget()
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            # Every ignore spec from root down to this directory, root
            # first: the last matching rule decides (design Risk 5).
            if _ignored(rel_path, specs):
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
            # Stat fast-path: (size, mtime_ns, inode) unchanged since the
            # last pass -> trust the recorded hash without re-reading.
            prev = previous.get(rel_path)
            if prev is not None and (st.st_size, st.st_mtime_ns,
                                     st.st_ino) == prev[:3]:
                results.append(ScannedFile(rel_path, abs_path, st.st_size,
                                           prev[3], st.st_mtime_ns, st.st_ino))
                continue
            sha = _hash_file(abs_path)
            if sha is None:
                continue
            results.append(ScannedFile(rel_path, abs_path, st.st_size, sha,
                                       st.st_mtime_ns, st.st_ino))

    if include:
        # Scoped indexing: gitignore-style INCLUSION — only files
        # matching any pattern are kept.
        scope = pathspec.GitIgnoreSpec.from_lines(include)
        results = [f for f in results
                   if _matches_include(scope, f.path)]
    if priority:
        prio = pathspec.GitIgnoreSpec.from_lines(priority)
        head = [f for f in results
                if _matches_include(prio, f.path)]
        tail = [f for f in results
                if not _matches_include(prio, f.path)]
        results = head + tail
    return results


def _matches_include(spec: pathspec.GitIgnoreSpec, rel_path: str) -> bool:
    """GitIgnoreSpec used as an INCLUSION filter: a bare directory pattern
    (``services/a``) matches everything under it, and ``dir/**`` matches all
    files beneath dir (GitIgnoreSpec.match_file handles both via negation
    semantics — a pattern 'match' means keep here)."""
    return bool(spec.match_file(rel_path)) or any(
        spec.match_file(f"{rel_path}/{name}") or False
        for name in ("", "x"))


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