"""Project scanner: walk, filter, and hash files per the design (§5/§6).

Filters: .gitignore (pathspec, nested per-directory), .codeindexignore,
directory skip-list, size cap, binary sniff. Hash = sha256 of content —
NEVER mtime (mtimes lie after branch switches/checkout; the content-hash
diff is what makes incremental indexing correct and cheap).

Every file the walk sees is ACCOUNTED FOR: it is returned, or it is recorded
in ``ScanAccounting`` under a named skip reason. A file that leaves a pass
without a reason is a defect -- an index that reports 403 files against a
tree of 450 cannot be reconciled by a reader.

A DIRECTORY the walk could not enter is the same discipline one level up, and
it has no natural depth: ``os.walk`` yields nothing for it and does not raise,
so silence would stand for an empty subtree. The walk therefore reports each
failed directory into ``ScanAccounting.unenterable_dirs`` -- one fact, recorded
once here, read by the staleness diff and by the pass.

A caller may pass a ``ScanBudget`` so a very large tree stops the walk and
reports that it stopped, instead of walking for minutes: the staleness check
uses this to degrade to ``unknown`` rather than silence.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field

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
_REPO_EXCLUDE_CACHE: dict[str, tuple[str, pathspec.PathSpec] | None] = {}

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


def _repo_git(directory: str) -> tuple[str, str] | None:
    """(repository root, git directory) for ``directory``, or None in no repo.

    The search is the one git does: the nearest ancestor carrying ``.git``.
    A ``.git`` DIRECTORY is the git directory itself; a ``.git`` FILE (a
    linked worktree or a submodule) points elsewhere with a ``gitdir:`` line
    and the directory holding that file is still the repository root.
    """
    cur = os.path.abspath(directory)
    while True:
        dot_git = os.path.join(cur, ".git")
        if os.path.isdir(dot_git):
            return cur, dot_git
        if os.path.isfile(dot_git):
            try:
                with open(dot_git, encoding="utf-8", errors="replace") as f:
                    first = f.readline().strip()
            except OSError:
                return None
            if not first.startswith("gitdir:"):
                return None
            target = first[len("gitdir:"):].strip()
            if not os.path.isabs(target):
                target = os.path.normpath(
                    os.path.join(os.path.dirname(dot_git), target))
            return cur, target
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _repo_local_exclude(root: str
                        ) -> tuple[str, pathspec.PathSpec] | None:
    """The repository-local ``info/exclude`` and the directory it anchors at.

    Git reads this file from the repository, never from a user-global
    location: ``$GIT_COMMON_DIR/info/exclude`` (a linked worktree's git dir
    carries a ``commondir`` file naming it, and a plain repository's git dir
    IS its common dir), and its patterns are spelled relative to the
    REPOSITORY root -- which is not necessarily the directory being scanned.
    So the spec travels with the repository root as its anchor and
    :func:`_spec_path` re-expresses a scanned path from there. When no
    repository covers ``root`` the answer is None: the scan set of a tree
    outside version control does not depend on some other repository.

    Precedence: lower than every ``.gitignore``, exactly as in git, which
    reads this file before the per-directory ignore files.
    """
    if root in _REPO_EXCLUDE_CACHE:
        return _REPO_EXCLUDE_CACHE[root]
    answer: tuple[str, pathspec.PathSpec] | None = None
    found = _repo_git(root)
    if found is not None:
        repo_root, git_dir = found
        if os.path.isfile(os.path.join(git_dir, "commondir")):
            # A linked worktree: info/exclude lives in the common dir.
            try:
                with open(os.path.join(git_dir, "commondir"),
                          encoding="utf-8", errors="replace") as f:
                    common = f.readline().strip()
            except OSError:
                common = ""
            if common:
                common = os.path.normpath(os.path.join(git_dir, common))
                git_dir = common
        exclude = os.path.join(git_dir, "info", "exclude")
        if os.path.isfile(exclude):
            try:
                with open(exclude, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", exclude, exc)
                lines = []
            if lines:
                answer = (repo_root,
                          pathspec.GitIgnoreSpec.from_lines(lines))
    _REPO_EXCLUDE_CACHE[root] = answer
    return answer


def _ordered_specs(root: str, dirpath: str
                   ) -> list[tuple[str, pathspec.PathSpec]]:
    """The ignore specs governing ``dirpath``, with their directories, root first.

    A spec travels with the directory that carries it, because that directory
    is the anchor for its anchored patterns (``/x`` means "here, beside the
    file that says it"), exactly as in git.
    """
    chain = []
    cur = dirpath
    while True:
        chain.append(cur)
        if cur == root:
            break
        cur = os.path.dirname(cur)
    specs = [(path, _load_ignore_spec(path)) for path in reversed(chain)]
    exclude = _repo_local_exclude(root)
    if exclude is not None:
        # First, so every .gitignore rule below it is the later match and
        # therefore the winner -- git's precedence for info/exclude.
        specs.insert(0, exclude)
    return specs


def _spec_path(directory: str, root: str, rel_path: str) -> str:
    """``rel_path`` (root-relative) as seen from the directory carrying a spec."""
    if directory == root:
        return rel_path
    if directory.startswith(root + os.sep):
        prefix = os.path.relpath(directory, root).replace(os.sep, "/") + "/"
        if rel_path.startswith(prefix):
            return rel_path[len(prefix):]
        return rel_path
    # A spec anchored ABOVE the scanned tree -- the repository-local
    # ``info/exclude``: its patterns are spelled relative to the repository
    # root, so the path is re-expressed from there.
    return os.path.relpath(os.path.join(root, rel_path),
                           directory).replace(os.sep, "/")


def _ignored(rel_path: str, specs: list[tuple[str, pathspec.PathSpec]],
             root: str) -> bool:
    """Git's composition: the last matching rule wins, deepest spec last.

    Each spec is asked about the path relative to ITS OWN directory, so an
    anchored pattern in a nested ignore file resolves against that directory
    instead of the project root. ``check_file`` reports the verdict AND the
    index of the pattern that decided it inside that spec, so a rule in a
    deeper file overrides one in a shallower file -- a negation included.
    This is what keeps the scan and ``git check-ignore`` agreeing.
    """
    ignored = False
    for directory, spec in specs:
        checked = spec.check_file(_spec_path(directory, root, rel_path))
        if checked.index is not None:
            ignored = bool(checked.include)
    return ignored


def _is_binary(path: str) -> bool:
    """Null byte in first 8KB or known binary extension.

    An OSError here is NOT an answer, so it propagates. A path this process
    cannot open is not a binary file: it is a file the scan could not read,
    and the caller counts it as ``unreadable`` and keeps whatever row it
    already has. Answering ``True`` dropped the file as ``binary``, deleted
    its row and left ``index-status`` reading ``fresh`` over a file the index
    had silently forgotten.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    with open(path, "rb") as f:
        return b"\x00" in f.read(8192)


SNIFF_SAMPLE_BYTES = 4096


def _looks_utf8ish(path: str) -> bool:
    """Cheap textual sniff over a bounded sample: is this UTF-8 text?

    The sample is decoded through an INCREMENTAL decoder. A sample that ends
    inside a multi-byte character decodes fine: the cut is an artefact of how
    much was read, not a property of the file. Decoding the sample strictly
    made every file carrying a multi-byte character across byte 4096 look
    non-text -- eight tracked C# files with box-drawing characters near that
    offset were dropped from the index this way, with only a debug log to
    show for it.

    The sample IS the file whenever the file is shorter than the cap, and
    then there is no cut to excuse: a sequence the decoder is still holding
    at the end is the file's own truncated character, so the decoder is
    finalised and the file is refused as non-UTF-8. Only a full-size sample
    is left pending, because only there can a split be ours. Accepting the
    truncated tail stored U+FFFD under `parse_unavailable` instead.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with open(path, "rb") as f:
        sample = f.read(SNIFF_SAMPLE_BYTES)
    if not sample:
        return True
    try:
        decoder.decode(sample, final=len(sample) < SNIFF_SAMPLE_BYTES)
        return True
    except UnicodeDecodeError:
        return False


SKIP_IGNORED = "ignored"
SKIP_IGNORED_DIRECTORY = "ignored-directory"
SKIP_OVERSIZED = "oversized"
SKIP_BINARY = "binary"
SKIP_NON_UTF8 = "non-utf8"
SKIP_UNREADABLE = "unreadable"
SKIP_OUT_OF_SCOPE = "out-of-scope"
SKIP_SENSITIVE = "sensitive"

# How many entries a pruned-subtree count may visit before it reports that the
# count is a floor rather than an exact number.
_PRUNE_COUNT_BUDGET = 50_000


@dataclass
class ScanAccounting:
    """What became of every file the walk saw.

    ``walked`` counts file entries the walk considered: the files in the
    directories it entered, plus the files inside subtrees it pruned as
    ignored (counted, not walked, as ``ignored-directory``). Every one of
    them ends in exactly one bucket:

    * ``indexed`` -- the file has a row in the manifest after the pass and
      this pass did not fail to read it (an unreadable file keeps no claim
      on `indexed`, even when an older row for it survives);
    * ``uncommitted`` -- the pass read the file and could not store it (every
      chunk of it failed to embed), so a later pass tries again;
    * ``skipped`` -- the walk or the pass named a reason for leaving it out.

    So ``walked == indexed + uncommitted + skipped_total`` always holds, and
    it is the only form of the identity this code states. ``exact`` is False
    only when a pruned subtree was too large for the counting budget, which
    makes ``walked`` a floor.

    ``indexed`` is filled from the manifest after the pass commits, not from
    the scan: the scan's own result is not what a reader will be given.

    Directories on the built-in skip list (.git, node_modules, .venv, build
    output) are not the tree an indexer answers about and are never counted;
    ``skipped_directories`` records how many were passed over.
    """

    walked: int = 0
    indexed: int = 0
    skipped: Counter = field(default_factory=Counter)
    uncommitted: int = 0
    skipped_directories: int = 0
    exact: bool = True
    examples: dict[str, list[str]] = field(default_factory=dict)
    #: Paths the walk SAW and this process could not stat or read. ONE fact,
    #: two readers: the pass keeps whatever row such a path already has (an
    #: unreadable file is not a deleted file), and the staleness diff reports
    #: it as ``unreadable`` instead of as a ``deleted`` reason. Deliberately
    #: outside ``as_dict``: the stored accounting keeps its schema, and
    #: ``skipped['unreadable']`` is the count a reader needs.
    could_not_read: set[str] = field(default_factory=set)
    #: Directories the walk could NOT enter (project-relative, POSIX). The
    #: same shape of fact as ``could_not_read`` and recorded by the same walk
    #: that produced it: ``os.walk`` yields NOTHING for such a directory, so
    #: without this every row beneath it looks deleted -- "fresh by
    #: forgetting" applied to a whole subtree instead of one file. ONE fact,
    #: three readers: the staleness diff withholds those rows from its
    #: ``deleted`` reasons and reports the directories beside the verdict;
    #: the pass neither purges those rows nor stamps the current version axes
    #: over content it never parsed; ``index-status`` names the directories.
    #: Deliberately outside ``as_dict``, exactly like ``could_not_read``: the
    #: stored accounting payload keeps its schema and no version axis moves.
    unenterable_dirs: set[str] = field(default_factory=set)

    def under_unentered(self, path: str) -> bool:
        """True when ``path`` lies under a directory the walk could not enter.

        The one predicate both readers use, so "is this row's subtree an
        evidence gap?" cannot be answered two ways.
        """
        return any(path == d or path.startswith(d + "/")
                   for d in self.unenterable_dirs)

    def skip(self, reason: str, path: str | None = None) -> None:
        self.skipped[reason] += 1
        if path and len(self.examples.setdefault(reason, [])) < 50:
            self.examples[reason].append(path)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    def as_dict(self) -> dict:
        return {
            "walked": self.walked,
            "indexed": self.indexed,
            "uncommitted": self.uncommitted,
            "skipped": {k: v for k, v in sorted(self.skipped.items()) if v},
            "skipped_total": self.skipped_total,
            "skipped_directories": self.skipped_directories,
            "examples": {k: v for k, v in sorted(self.examples.items()) if v},
            "counted_exactly": self.exact,
        }


def _count_files_under(path: str) -> tuple[int, bool]:
    """Files in a pruned subtree, and whether the count is exact.

    A pruned subtree still holds files a reader can see on disk; counting them
    keeps the reconciliation honest without walking them into the index. The
    budget bounds the cost on a huge ignored tree.
    """
    seen = 0
    for _dirpath, _dirnames, filenames in os.walk(path):
        seen += len(filenames)
        if seen > _PRUNE_COUNT_BUDGET:
            return seen, False
    return seen, True


def scan_project(root: str, max_file_bytes: int = 1_048_576,
                 previous: dict[str, tuple[int, int, int, str]] | None = None,
                 include: list[str] | None = None,
                 priority: list[str] | None = None,
                 budget: ScanBudget | None = None,
                 accounting: ScanAccounting | None = None,
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
    them: the specs are consulted from the root outwards, each about the path
    relative to its own directory, and the LAST matching rule decides, so a
    deeper ``!pattern`` re-includes what a shallower spec excluded. An
    anchored pattern therefore anchors to the directory of the file carrying
    it, as in git, and works below the root. A directory the composed verdict
    ignores is pruned, which also carries git's rule that nothing inside an
    excluded directory can be re-included: there is no walk left to
    re-include in.

    The repository-local ``.git/info/exclude`` of the repository covering
    ``root`` composes too, anchored at the REPOSITORY root and read before
    every ``.gitignore`` -- git's own precedence, and never a user-global
    exclude file. A tree outside version control has no such spec.
    """
    previous = previous if previous is not None and \
        os.environ.get("PARANOID_HASH") not in ("1", "true") else {}
    root = os.path.abspath(root)
    # The walk's own root probe. ``os.walk`` reports NOTHING for a root it
    # cannot enter instead of raising, and "nothing" downstream means "every
    # row is deleted" -- which is how a tree the check could not look at was
    # read as a tree that had emptied. A root this process cannot open is an
    # OSError the caller answers as an ``unknown`` verdict, not an empty tree.
    # The probe covers the root only; the ``onerror`` hook below covers every
    # directory under it, for the same reason and at no natural depth.
    with os.scandir(root):
        pass
    results: list[ScannedFile] = []
    _GITIGNORE_CACHE.clear()
    _REPO_EXCLUDE_CACHE.clear()
    walked = 0
    started = time.monotonic()

    def keep_unreadable(rel_path: str, what: str) -> None:
        """The name is on disk and this pass could not look at it.

        Not a ``binary``/``non-utf8`` classification: nothing was examined,
        so nothing is claimed about the content. The path is counted as
        ``unreadable`` and named in ``could_not_read``, which is what stops a
        pass deleting its row and what stops the diff calling it ``deleted``.
        """
        logger.debug("skip (unreadable: %s): %s", what, rel_path)
        if accounting is not None:
            accounting.skip(SKIP_UNREADABLE, rel_path)
            accounting.could_not_read.add(rel_path)

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

    def on_walk_error(exc: OSError) -> None:
        """A directory the walk could not ENTER, reported instead of borne.

        ``os.walk`` does not raise for a subtree it cannot open: it calls
        this hook if one is given and otherwise yields nothing, so the
        subtree silently stops existing. Downstream, "nothing" is the shape
        of "every file here was deleted" -- the index forgot a whole
        directory and read ``fresh`` over it. So the failure is recorded as
        the fact it is, once, under the directory's project-relative name.

        A failure that cannot name a directory is not swallowed: raising it
        keeps "could not look" from becoming "empty", which is the one
        answer this hook exists to prevent.
        """
        name = getattr(exc, "filename", None)
        if not name:
            raise exc
        rel = os.path.relpath(name, root).replace(os.sep, "/")
        logger.warning("cannot enter directory %s: %s", rel, exc)
        if accounting is not None:
            accounting.unenterable_dirs.add(rel)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                onerror=on_walk_error):
        rel_dir = os.path.relpath(dirpath, root)

        # Per-directory ignore composition: prune dirnames in-place so we
        # never descend into ignored trees. A directory's OWN ignore file
        # says nothing about the directory itself, so the deciding specs are
        # those from the root down to its parent, in git's order.
        if rel_dir != ".":
            dir_rel = rel_dir.replace(os.sep, "/") + "/"
            parent_specs = _ordered_specs(root, os.path.dirname(dirpath))
            if _ignored(dir_rel, parent_specs, root):
                if accounting is not None:
                    # The subtree is not walked, but its files are still on
                    # disk and still part of what a reader would count: the
                    # count keeps the reconciliation honest.
                    files, exact = _count_files_under(dirpath)
                    accounting.walked += files
                    accounting.skipped[SKIP_IGNORED_DIRECTORY] += files
                    accounting.exact = accounting.exact and exact
                dirnames[:] = []
                continue

        kept_dirs = [d for d in dirnames if d not in SKIP_DIRS]
        if accounting is not None:
            accounting.skipped_directories += len(dirnames) - len(kept_dirs)
        dirnames[:] = kept_dirs
        specs = _ordered_specs(root, dirpath)

        for fname in filenames:
            check_budget()
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root).replace(os.sep, "/")
            if accounting is not None:
                accounting.walked += 1
            # The SKIP_DIRS prune above removes the .git DIRECTORY only. A
            # linked worktree (or a submodule) carries a .git FILE instead,
            # whose text is a "gitdir: <machine path>" pointer: there is
            # nothing to index in it, and it is the one way a host path could
            # reach the index.
            if fname == ".git":
                logger.debug("skip (git pointer file): %s", rel_path)
                if accounting is not None:
                    accounting.skip(SKIP_IGNORED, rel_path)
                continue
            # Every ignore spec from root down to this directory, root
            # first: the last matching rule decides (design Risk 5).
            if _ignored(rel_path, specs, root):
                logger.debug("skip (ignored): %s", rel_path)
                if accounting is not None:
                    accounting.skip(SKIP_IGNORED, rel_path)
                continue
            try:
                st = os.stat(abs_path)
                if not os.path.isfile(abs_path):
                    if accounting is not None:
                        accounting.skip(SKIP_UNREADABLE, rel_path)
                    continue
            except OSError:
                keep_unreadable(rel_path, "stat")
                continue
            if st.st_size > max_file_bytes:
                logger.debug("skip (too large): %s", rel_path)
                if accounting is not None:
                    accounting.skip(SKIP_OVERSIZED, rel_path)
                continue
            try:
                binary = _is_binary(abs_path)
                textish = None if binary else _looks_utf8ish(abs_path)
            except OSError:
                # An existing file this process cannot open. It used to be
                # answered as ``binary`` and dropped, which deleted the row
                # of a file the index had merely failed to read.
                keep_unreadable(rel_path, "read")
                continue
            if binary:
                logger.debug("skip (binary): %s", rel_path)
                if accounting is not None:
                    accounting.skip(SKIP_BINARY, rel_path)
                continue
            if not textish:
                logger.debug("skip (non-utf8): %s", rel_path)
                if accounting is not None:
                    accounting.skip(SKIP_NON_UTF8, rel_path)
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
                keep_unreadable(rel_path, "hash")
                continue
            results.append(ScannedFile(rel_path, abs_path, st.st_size, sha,
                                       st.st_mtime_ns, st.st_ino))

    if include:
        # Scoped indexing: gitignore-style INCLUSION — only files
        # matching any pattern are kept.
        scope = pathspec.GitIgnoreSpec.from_lines(include)
        kept = [f for f in results if _matches_include(scope, f.path)]
        if accounting is not None:
            accounting.skipped[SKIP_OUT_OF_SCOPE] += len(results) - len(kept)
        results = kept
    if priority:
        prio = pathspec.GitIgnoreSpec.from_lines(priority)
        head = [f for f in results
                if _matches_include(prio, f.path)]
        tail = [f for f in results
                if not _matches_include(prio, f.path)]
        results = head + tail
    if accounting is not None:
        # The one line that makes a drop visible without turning on debug
        # logging for a whole tree.
        accounting.indexed = len(results)
        logger.info("scan of %s: walked=%d kept=%d skipped=%s",
                    root, accounting.walked, accounting.indexed,
                    dict(sorted(accounting.skipped.items())))
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


# ---------------------------------------------------------------------------
# Scan accounting, as a pass records it and a read reports it.
# ---------------------------------------------------------------------------

SCAN_ACCOUNTING_META = "scan_accounting"


def write_scan_accounting(manifest, accounting: ScanAccounting) -> None:
    """Record one pass's accounting in the manifest, for the reads to report."""
    manifest.set_meta(SCAN_ACCOUNTING_META, json.dumps(accounting.as_dict()))


def read_scan_accounting(manifest) -> dict | None:
    """The recorded accounting, or None when no pass recorded one."""
    raw = manifest.get_meta(SCAN_ACCOUNTING_META)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def skip_reason_for(path: str, accounting: dict | None) -> str | None:
    """The recorded skip reason for ``path``, or None if it was not skipped.

    A reason is recorded for the first few files of each kind (``examples``);
    a path outside that sample still has its reason counted, it just cannot
    be named from the record alone.
    """
    examples = (accounting or {}).get("examples") or {}
    for reason, paths in examples.items():
        if path in paths:
            return reason
    return None


def skip_counts_phrase(accounting: dict | None) -> str | None:
    """The skip reasons the pass counted, for a path outside the named sample.

    ``skip_reason_for`` names a path only while it is among the first few
    examples of its kind. The 51st file of a reason is still COUNTED, so the
    answer names the classes the pass skipped and says that this path was not
    one of the examples -- a file the index cannot take is counted and named,
    not answered with a bare "not indexed".
    """
    skipped = (accounting or {}).get("skipped") or {}
    if not skipped:
        return None
    counts = ", ".join(f"{reason} ({n})"
                       for reason, n in sorted(skipped.items()))
    return (f"the last pass skipped files as {counts}, and recorded no "
            f"example for this path")


def accounting_phrase(accounting: dict | None) -> str | None:
    """One clause stating what a pass took and why it took nothing else.

    The same sentence on ``index-status`` and ``overview``, so the two never
    describe one pass in two vocabularies.
    """
    if not accounting:
        return None
    skipped = accounting.get("skipped") or {}
    why = ", ".join(f"{reason}:{n}" for reason, n in skipped.items()) or "none"
    # All three buckets in the arithmetic, so the numbers on the line add up to
    # `walked` even when a file was read and could not be stored.
    text = (f"scan={accounting.get('walked')} walked:"
            f"{accounting.get('indexed')} indexed,"
            f"{accounting.get('uncommitted') or 0} uncommitted,"
            f"{accounting.get('skipped_total')} skipped ({why})")
    if not accounting.get("counted_exactly", True):
        text += ",walked is a floor"
    return text
