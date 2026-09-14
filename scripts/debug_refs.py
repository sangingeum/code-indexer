"""Debug: extract_refs on the test main.cpp fixture."""

from mcp_code_indexer import ts_chunker

text = """#include "transfer/session.hpp"
int run() {
  ft::FileTransferSession s;
  s.start();
  return 0;
}
"""
for r in ts_chunker.extract_refs("src/main.cpp", text):
    print(r)
