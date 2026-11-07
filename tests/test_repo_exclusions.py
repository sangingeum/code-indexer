"""This repository's own index exclusions take effect.

The root ``.codeindexignore`` names the non-source trees the scanner must
skip: ``runs/`` (captured round records, gate output and command logs) and
``eval/`` (the retrieval evaluation corpus). Both stay tracked in git; only
the index skips them. The test copies the repository's own file into a
throwaway tree shaped like this one, so it checks the patterns that ship
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
    """A miniature project with the same non-source trees at its root."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def main():\n    pass\n")
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
