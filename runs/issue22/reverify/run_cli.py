"""Run the CLI from an explicit source tree, so a measurement names its tree.

    CI_SRC=/abs/tree/src [CI_TREE=tip|base] <venv>/bin/python run_cli.py <args...>

Prints the resolved package path on stderr — relative to the tree it was told
to use, so a capture can say which checkout it measured without carrying a
machine-specific absolute path.
"""
import os
import sys

src = os.path.abspath(os.environ["CI_SRC"])
sys.path.insert(0, src)
import code_indexer  # noqa: E402

label = os.environ.get("CI_TREE", "tree")
rel = os.path.relpath(os.path.abspath(code_indexer.__file__),
                      os.path.dirname(src))
sys.stderr.write(f"[{label}] code_indexer = {rel}\n")
from code_indexer.cli import main  # noqa: E402

main()
