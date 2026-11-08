"""The scan set honours the repository-local exclude file, as git does.

`.git/info/exclude` is part of what git ignores, yet the scanner read only
`.gitignore` and `.codeindexignore`: on a copy of a tree that excluded twelve
`.artifacts/*.log` files there, git reported them ignored and the index held
them. The scanner now reads the exclude file of the repository covering the
scan root, anchored at the REPOSITORY root and read before every .gitignore
(git's precedence), never a user-global file.

Against real git: the scan set is compared with `git ls-files` minus
`git check-ignore` hits, on a copy that exercises a tracked .gitignore rule
and a local exclude rule together.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from code_indexer.scanner import SKIP_IGNORED, ScanAccounting, scan_project

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git is not installed")

TRACKED_GITIGNORE = "build/\n*.tmp\n"
# The shape the round-1 gate found: a repository-local rule, not a tracked
# one, so nothing in the tree says these files exist and only git knows.
LOCAL_EXCLUDE = ".artifacts/\n*.local-only\n"
TWELVE_ARTIFACT_LOGS = 12


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run([GIT, "-C", str(root), *args], input=stdin,
                          capture_output=True, text=True, check=True)
    return done.stdout


def _repo_with_both_kinds_of_rule(tmp_path: Path) -> Path:
    """A repository whose ignored set needs BOTH ignore files to explain it.

    Twelve `.artifacts/*.log` files (the local exclude), a `.tmp` file and a
    `build/` tree (the tracked .gitignore), plus three tracked sources that
    must stay in the scan.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / ".gitignore").write_text(TRACKED_GITIGNORE, encoding="utf-8")
    (root / "src" / "app.py").write_text("def main():\n    pass\n")
    (root / "docs" / "notes.md").write_text("# notes\n")
    (root / "src" / "scratch.tmp").write_text("leftover editor state\n")
    (root / "build" / "gen.py").mkdir(parents=True)
    (root / "build" / "gen.py" / "x.py").write_text("GENERATED = 1\n")
    (root / ".artifacts").mkdir()
    for n in range(TWELVE_ARTIFACT_LOGS):
        (root / ".artifacts" / f"run{n}.log").write_text(
            f"captured output {n}\n", encoding="utf-8")

    _git(root, "init", "-q")
    (root / ".git" / "info" / "exclude").write_text(
        LOCAL_EXCLUDE, encoding="utf-8")
    # `add -A` stages what git tracks and leaves the ignored paths alone, so
    # the index and the working tree disagree by exactly the ignored set.
    _git(root, "add", "-A")
    return root


def _on_disk(root: Path) -> list[str]:
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    )


@needs_git
def test_the_indexed_set_is_git_ls_files_minus_the_ignored_files(tmp_path):
    """Twelve `.artifacts/*.log` files that git reports ignored entered the
    index because the scanner never read `.git/info/exclude`."""
    root = _repo_with_both_kinds_of_rule(tmp_path)
    disk = _on_disk(root)
    tracked = _git(root, "ls-files").split()
    ignored = set(_git(root, "check-ignore", "--stdin",
                       stdin="\n".join(disk) + "\n").split())

    indexed = {f.path for f in scan_project(str(root))}

    artifacts = {p for p in disk if p.startswith(".artifacts/")}
    assert len(artifacts) == TWELVE_ARTIFACT_LOGS
    assert artifacts <= ignored, "git does not ignore the local-excluded logs"
    assert indexed == set(tracked) - ignored, (
        f"indexed={sorted(indexed)}"
        f" tracked-not-ignored={sorted(set(tracked) - ignored)}")
    # The count is the reconciliation, not a by-product of the set test:
    # every file on disk is either tracked (and indexed) or ignored.
    assert len(indexed) == len(set(tracked) - ignored)
    assert len(disk) == len(tracked) + len(ignored), (
        len(disk), len(tracked), len(ignored))
    # Neither rule alone explains the gap: both fired.
    assert not any(p in indexed for p in (
        ".artifacts/run0.log", "build/gen.py/x.py", "src/scratch.tmp"))
    assert "src/app.py" in indexed and "docs/notes.md" in indexed


@needs_git
def test_a_scan_of_a_subdirectory_still_reads_the_repositorys_exclude(tmp_path):
    """The exclude file belongs to the REPOSITORY, so a scan of one of its
    subdirectories must still read it -- with the patterns anchored at the
    repository root, not at the directory being scanned."""
    root = tmp_path / "repo"
    (root / "src" / "nested").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / ".git" / "info" / "exclude").write_text(
        "*.log\n/nested.log\n", encoding="utf-8")
    (root / "src" / "deep.log").write_text("x = 1\n")
    (root / "src" / "keep.py").write_text("x = 2\n")
    (root / "src" / "nested" / "nested.log").write_text("x = 3\n")

    indexed = {f.path for f in scan_project(str(root / "src"))}
    assert "deep.log" not in indexed
    assert "nested/nested.log" not in indexed
    assert "keep.py" in indexed


def test_a_tree_outside_version_control_reads_no_exclude_file(tmp_path):
    """Per-repository, not global: the rule lives in one repository's .git,
    so a plain directory with the same filenames keeps every file."""
    root = tmp_path / "plain"
    (root / ".artifacts").mkdir(parents=True)
    (root / ".artifacts" / "run0.log").write_text("captured output\n")
    (root / "app.py").write_text("x = 1\n")

    indexed = {f.path for f in scan_project(str(root))}
    assert indexed == {".artifacts/run0.log", "app.py"}, indexed


def test_a_tracked_gitignore_negation_overrides_the_local_exclude(tmp_path):
    """Git reads info/exclude BEFORE the .gitignore files, so a later rule
    wins. A scanner that let the exclude file answer last would drop a file
    git checks in."""
    root = tmp_path / "repo"
    (root / ".git" / "info").mkdir(parents=True)
    (root / ".git" / "info" / "exclude").write_text("*.log\n", encoding="utf-8")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".gitignore").write_text("!keep.log\n", encoding="utf-8")
    (root / "keep.log").write_text("kept\n")
    (root / "drop.log").write_text("dropped\n")

    indexed = {f.path for f in scan_project(str(root))}
    assert "keep.log" in indexed
    assert "drop.log" not in indexed


@needs_git
def test_a_linked_worktree_s_pointer_file_is_not_indexed(tmp_path):
    """A linked worktree carries a `.git` FILE, and the directory prune
    cannot see it: the file's text is `gitdir: <host path>`, the one way a
    machine path reaches the index."""
    main = tmp_path / "main"
    (main / "src").mkdir(parents=True)
    _git(main, "init", "-q")
    (main / "src" / "app.py").write_text("def main():\n    pass\n",
                                          encoding="utf-8")
    identity = ("-c", "user.name=test", "-c",
                "user.email=test@example.invalid")
    _git(main, *identity, "add", "-A")
    _git(main, *identity, "commit", "-qm", "first")

    worktree = tmp_path / "worktree"
    _git(main, "worktree", "add", "-q", str(worktree), "-b", "side")
    assert (worktree / ".git").is_file(), (
        "the linked worktree did not get a pointer file")
    assert (worktree / ".git").read_text(encoding="utf-8").startswith(
        "gitdir:")
    (worktree / "src" / "side.py").write_text("SIDE = 1\n", encoding="utf-8")
    _git(worktree, *identity, "add", "-A")
    _git(worktree, *identity, "commit", "-qm", "side")

    accounting = ScanAccounting()
    rows = scan_project(str(worktree), accounting=accounting)
    indexed = {f.path for f in rows}
    tracked = set(_git(worktree, "ls-files").split())

    assert ".git" not in indexed, sorted(indexed)
    assert indexed == tracked, (sorted(indexed), sorted(tracked))
    assert ".git" in accounting.examples.get(SKIP_IGNORED, []), (
        accounting.as_dict())
    leaked = [f.path for f in rows
              if "gitdir:" in Path(f.abs_path).read_text(
                  encoding="utf-8", errors="replace")]
    assert leaked == [], leaked
