"""Count the read commands from the panel `code-indexer --help` prints.

Usage: python parse_help.py <help capture>

Independent of the suite and of the typer registration: the help panel is what
a caller reads. Names are the first column of the Commands panel, so option
rows (they start with `-`) are excluded by construction.
"""

from __future__ import annotations

import re
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
pattern = re.compile(r"^\u2502 ([a-z][a-z0-9-]+)\s{2,}\S")
names = [m.group(1) for line in lines if (m := pattern.match(line))]
print(f"help_listed_commands={len(names)}")
print(" ".join(names))
