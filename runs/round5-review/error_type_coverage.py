"""Which ErrorTypes the code declares and the failure reference never names.

A set difference between `errors.py`'s taxonomy and the table an agent is told
to look in when a command fails. Reads files; runs no command.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

errors = (ROOT / "src" / "code_indexer" / "errors.py").read_text(encoding="utf-8")
failures = (ROOT / "skill" / "references" / "failures.md").read_text(
    encoding="utf-8")

declared = sorted({m for m in re.findall(r'error_type\s*=\s*"([A-Za-z0-9_]+)"', errors)})
missing = [name for name in declared if name not in failures]

print(f"error types declared in src/code_indexer/errors.py : {len(declared)}")
print(f"of those named in skill/references/failures.md    : {len(declared) - len(missing)}")
for name in missing:
    print(f"  missing: {name}")
