"""Debug: class name node in cpp grammar."""

import tree_sitter_language_pack

src = b"""class FileTransferSession {
 public:
  void start();
  int state_;
};
"""
parser = tree_sitter_language_pack.get_parser("cpp")
tree = parser.parse(src)


def dump(node, depth=0):
    print("  " * depth + f"{node.type} [{node.start_point}-{node.end_point}]")
    for c in node.children:
        dump(c, depth + 1)


dump(tree.root_node)
