"""Debug: ts_chunker symbol extraction on a C++ fixture."""

import os
import tempfile

from mcp_code_indexer import ts_chunker

root = tempfile.mkdtemp()
hpp = os.path.join(root, "x.hpp")
with open(hpp, "w") as f:
    f.write(
        "#pragma once\n"
        "namespace ft {\n\n"
        "class FileTransferSession {\n"
        " public:\n"
        "  void start();\n"
        "  void stop();\n"
        " private:\n"
        "  int state_;\n"
        "};\n\n"
        "enum class Mode { kPush, kPull };\n\n"
        "}  // namespace ft\n"
    )
cpp = os.path.join(root, "y.cpp")
with open(cpp, "w") as f:
    f.write(
        '#include "transfer/session.hpp"\n'
        "namespace ft {\n\n"
        "void FileTransferSession::start() {\n"
        "  state_ = 1;\n"
        "}\n\n"
        "void FileTransferSession::stop() {\n"
        "  state_ = 0;\n"
        "}\n\n"
        "}  // namespace ft\n"
    )

for path, text in ((hpp, open(hpp).read()), (cpp, open(cpp).read())):
    print("====", path)
    for c in ts_chunker.chunk_text(path, text):
        print(f"  {c.start_line}-{c.end_line} sym={c.symbol!r} "
              f"type={c.symbol_type} src={c.source}")
    print("  symbols:", ts_chunker.extract_symbols(path, text, ts_chunker.chunk_text(path, text)))
