"""This repository's own index exclusions take effect.

The root ``.codeindexignore`` names the non-source trees the scanner must
skip: ``runs/`` (round captures, gate output and command logs, kept in the
working checkout and not tracked) and ``eval/`` (the retrieval evaluation
corpus, which is tracked and ships). The test copies the repository's own file
into a throwaway tree shaped like this one, so it checks the patterns that ship
rather than a fixture written to match the scanner.
"""

from pathlib import Path

from code_indexer.scanner import scan_project

REPO = Path(__file__).resolve().parent.parent



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



def test_a_root_negation_cannot_re_include_a_nested_exclusion(tmp_path):
    """Per-directory specs compose by ``any()``; a nested match always wins.

    The root file excludes and then re-includes ``keep.txt``; a nested file
    excludes it again. The nested spec is consulted even where the root one
    says no, so the negation stops at the directory that re-excluded it.
    """
    root = _repo_shaped_tree(tmp_path)
    (root / "keep.txt").write_text("root level\n")
    (root / "src" / "keep.txt").write_text("nested level\n")
    (root / ".codeindexignore").write_text(
        _repo_ignore_patterns() + "\nkeep.txt\n!keep.txt\n", encoding="utf-8"
    )
    (root / "src" / ".codeindexignore").write_text("keep.txt\n", encoding="utf-8")

    scanned = {f.path for f in scan_project(str(root))}
    assert "keep.txt" in scanned
    assert "src/keep.txt" not in scanned
