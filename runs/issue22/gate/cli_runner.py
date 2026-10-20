"""Run the code-indexer CLI from whatever package is first on sys.path.

Usage: <python> cli_runner.py <args...>   (PYTHONPATH selects the source tree)
"""

import sys

import code_indexer

from code_indexer.cli import main

if "--where" in sys.argv:
    sys.stderr.write(f"package={code_indexer.__file__}\n")
    sys.argv.remove("--where")

main()
