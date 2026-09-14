"""Debug: scanner exclusions against the e2e fixture names."""

import os

from mcp_code_indexer import scanner

for name in ("new_file.py", "session.hpp", "another.cpp"):
    ext = os.path.splitext(name)[1].lower()
    print(name, "ext:", ext, "binary-ext:", ext in scanner.BINARY_EXTENSIONS)
