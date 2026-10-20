"""Print the tree the package resolved to (relative to CI_SRC's parent).

    CI_SRC=/abs/tree/src python which_tree.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.environ["CI_SRC"]))
import code_indexer  # noqa: E402

print("code_indexer =", os.path.relpath(
    os.path.abspath(code_indexer.__file__),
    os.path.dirname(os.path.abspath(os.environ["CI_SRC"]))))
