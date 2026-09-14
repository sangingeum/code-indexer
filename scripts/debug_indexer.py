"""Debug: end-to-end Indexer in-process against a fixture repo.

Bypasses the server so symbol/ref persistence can be checked directly.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
sys.path.insert(0, os.path.dirname(__file__))

from mcp_code_indexer import ts_chunker
from mcp_code_indexer.chunker import chunk

# Reproduce the fixture inline (same text e2e writes).
HPP = """#pragma once
namespace ft {

class FileTransferSession {
 public:
  void start();
  void stop();
 private:
  int state_;
};

enum class Mode { kPush, kPull };

}  // namespace ft
"""
PY = "def brand_new_helper():\n    return 'fresh'\n"

root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "src"))
with open(os.path.join(root, "src", "session.hpp"), "w") as f:
    f.write(HPP)
with open(os.path.join(root, "src", "new_file.py"), "w") as f:
    f.write(PY)

from mcp_code_indexer.indexer import _chunk_dispatch  # noqa: E402

for rel, text in (("src/session.hpp", HPP), ("src/new_file.py", PY)):
    chunks = _chunk_dispatch(rel, text)
    print("====", rel, "n_chunks:", len(chunks))
    for c in chunks:
        print(f"  {c.start_line}-{c.end_line} sym={c.symbol!r} type={c.symbol_type} src={c.source}")
    print("  extract_symbols:", ts_chunker.extract_symbols(rel, text, chunks))
    print("  extract_refs (first 5):", ts_chunker.extract_refs(rel, text)[:5])

shutil.rmtree(root, ignore_errors=True)
