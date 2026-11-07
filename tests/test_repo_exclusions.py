"""This repository's own index exclusions take effect.

The root ``.codeindexignore`` names the non-source trees the scanner must
skip: ``runs/`` (round captures, gate output and command logs, kept in the
working checkout and not tracked) and ``eval/`` (the retrieval evaluation
corpus, which is tracked and ships). The test copies the repository's own file
into a throwaway tree shaped like this one, so it checks the patterns that ship
rather than a fixture written to match the scanner.

Nested files compose by git's rule -- the deepest matching rule decides -- so
the suite also pins that a nested negation re-includes what a shallower spec
excluded, and checks the whole composition against ``git check-ignore``.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from code_indexer.scanner import scan_project

REPO = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")


def _repo_ignore_patterns() -> str:
    """The exclusion set the repository root ships, read as-is."""
    path = REPO / ".codeindexignore"
    assert path.is_file(), "the repository root has no .codeindexignore"
    return path.read_text(encoding="utf-8")


def _repo_shaped_tree(tmp_path: Path) -> Path:
    """A miniature project with the same non-source trees at its root.

    The same names also sit below the root, under ``src/`` and ``pkg/``:
    those are ordinary source and must stay in the scan.
    """
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def main():\n    pass\n")
    (root / "src" / "runs").mkdir()
    (root / "src" / "runs" / "b.py").write_text("VALUE = 1\n")
    (root / "src" / "eval").mkdir()
    (root / "src" / "eval" / "q.py").write_text("def score():\n    return 0\n")
    (root / "pkg" / "runs").mkdir(parents=True)
    (root / "pkg" / "runs" / "c.py").write_text("VALUE = 2\n")
    (root / "runs" / "round").mkdir(parents=True)
    (root / "runs" / "round" / "sweep.txt").write_text("captured output\n")
    (root / "runs" / "dump.py").write_text("print('captured helper')\n")
    (root / "eval").mkdir()
    (root / "eval" / "queries.jsonl").write_text(
        '{"query": "where is the project lock taken"}\n'
    )
    (root / ".codeindexignore").write_text(
        _repo_ignore_patterns(), encoding="utf-8"
    )
    return root


def test_repo_exclusions_keep_non_source_trees_out_of_the_index(tmp_path):
    scanned = {f.path for f in scan_project(str(_repo_shaped_tree(tmp_path)))}
    assert "src/app.py" in scanned
    assert "runs/round/sweep.txt" not in scanned
    assert "runs/dump.py" not in scanned
    assert "eval/queries.jsonl" not in scanned
    # The patterns are anchored to the root, so the nested names are source.
    assert "src/runs/b.py" in scanned
    assert "src/eval/q.py" in scanned
    assert "pkg/runs/c.py" in scanned


def test_a_nested_negation_re_includes_what_a_shallower_spec_excluded(tmp_path):
    """The deepest matching rule decides, exactly as git composes the files.

    The root file excludes ``secret.txt`` and ``*.log``; ``src/`` negates both.
    Git answers "not ignored" for ``src/secret.txt`` and ``src/trace.log``,
    so the scan keeps them -- while the root-level ``secret.txt``, which no
    deeper file re-includes, stays out. A composition that let ANY spec's
    match win would drop the nested pair instead.
    """
    root = _repo_shaped_tree(tmp_path)
    (root / "secret.txt").write_text("root level\n")
    (root / "src" / "secret.txt").write_text("nested level\n")
    (root / "src" / "trace.log").write_text("nested log\n")
    (root / ".codeindexignore").write_text(
        _repo_ignore_patterns() + "\nsecret.txt\n*.log\n", encoding="utf-8"
    )
    (root / "src" / ".codeindexignore").write_text(
        "!secret.txt\n!trace.log\n", encoding="utf-8"
    )

    scanned = {f.path for f in scan_project(str(root))}
    assert "src/secret.txt" in scanned
    assert "src/trace.log" in scanned
    assert "secret.txt" not in scanned


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_the_scan_agrees_with_git_check_ignore(tmp_path):
    """One composition, two readers: the scan keeps what git does not ignore.

    The same rules are written to ``.gitignore`` and ``.codeindexignore`` in
    two directories, then ``git check-ignore`` is asked about every file. The
    scan set and git's answer must be the same set, which is the guarantee a
    nested negation gives and ``any()`` could not.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    paths = ["secret.txt", "src/secret.txt", "src/trace.log", "src/keep.py"]
    for rel in paths:
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    rules = {".": "secret.txt\n*.log\n", "src": "!secret.txt\n!trace.log\n"}
    for sub, text in rules.items():
        (root / sub / ".gitignore").write_text(text, encoding="utf-8")
        (root / sub / ".codeindexignore").write_text(text, encoding="utf-8")
    subprocess.run([GIT, "init", "-q", str(root)], check=True,
                   capture_output=True, text=True)
    listed = subprocess.run(
        [GIT, "-C", str(root), "check-ignore", "--stdin"],
        input="\n".join(paths) + "\n", capture_output=True, text=True,
        check=True)
    git_ignored = set(listed.stdout.split())

    scanned = {f.path for f in scan_project(str(root))} & set(paths)
    assert scanned == {p for p in paths if p not in git_ignored}


def test_a_nested_negation_cannot_resurrect_a_pruned_directory(tmp_path):
    """The prune is git's rule, not an optimisation a later change may remove.

    Git refuses to re-include anything inside an excluded directory: there is
    no walk left to re-include in. The scanner prunes for that reason, so the
    FILE SET is the contract here, not the walk length -- a nested negation
    inside a pruned tree must not resurrect rows (which would also make the
    scanner disagree with ``git check-ignore``).
    """
    root = tmp_path / "repo"
    (root / "skipdir").mkdir(parents=True)
    (root / "keep").mkdir()
    paths = ["e.py", "keep/d.py", "skipdir/a.py", "skipdir/b.py"]
    for rel in paths:
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    for name in (".gitignore", ".codeindexignore"):
        (root / name).write_text("skipdir/\n", encoding="utf-8")
        (root / "skipdir" / name).write_text("!a.py\n", encoding="utf-8")

    scanned = {f.path for f in scan_project(str(root))} & set(paths)
    # Asserted without git too, so the guarantee survives on a git-less host.
    assert scanned == {"e.py", "keep/d.py"}, scanned

    if GIT is None:
        return
    subprocess.run([GIT, "init", "-q", str(root)], check=True,
                   capture_output=True, text=True)
    listed = subprocess.run(
        [GIT, "-C", str(root), "check-ignore", "--stdin"],
        input="\n".join(paths) + "\n", capture_output=True, text=True,
        check=True)
    git_ignored = set(listed.stdout.split())
    assert scanned == {p for p in paths if p not in git_ignored}
