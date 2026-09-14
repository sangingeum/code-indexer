"""Debug: why cpp function_definition name is not found."""

import tree_sitter_language_pack

src = b"""#include "transfer/session.hpp"
namespace ft {

void FileTransferSession::start() {
  state_ = 1;
}

void FileTransferSession::stop() {
  state_ = 0;
}
}
"""
parser = tree_sitter_language_pack.get_parser("cpp")
tree = parser.parse(src)


def dump(node, depth=0):
    print("  " * depth + f"{node.type} [{node.start_point}-{node.end_point}] "
          f"named_children={[c.type for c in node.children]}")
    for c in node.children:
        dump(c, depth + 1)


dump(tree.root_node)
