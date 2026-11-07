"""Did --format come back on any write command? Spot-check two.

Usage: python check_write_flags.py <tree>

Invokes `add-project` and `reindex-project` with `--format json` and prints the
exit code and first stderr line: the mention-free refusal is `No such option`,
exit 2. Read-only in effect: the option is refused before any work.
"""

from __future__ import annotations

import os
import sys

TREE = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(TREE, "src"))

import tempfile  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from code_indexer.cli import app  # noqa: E402

runner = CliRunner()
print("argv\texit\tstderr_first_line")
for argv in (["add-project", "x", "--format", "json"],
             ["reindex-project", "x", "--format", "json"],
             ["add-project", "x", "--format", "compact"],
             ["reindex-project", "x", "--format", "compact"]):
    result = runner.invoke(app, argv)
    err = (result.stderr or "").strip().splitlines()
    says = [line for line in err if "No such option" in line]
    print("\t".join([" ".join(argv), str(result.exit_code),
                     ((says[-1] if says else (err[-1] if err else "-"))
                      .replace("\u2502", "").strip())]))
