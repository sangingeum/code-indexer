"""Debug: run Indexer twice against an evolving repo (stub embedder/store).

Pass 1: hpp+py. Pass 2: modify hpp, add py2, delete hpp. Verify symbols.
"""

import os
import shutil
import tempfile

from mcp_code_indexer.config import Config
from mcp_code_indexer.indexer import Indexer
from mcp_code_indexer.manifest import Manifest


class StubEmbedder:
    def dimension(self):
        return 4

    def embed(self, texts):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class StubStore:
    def collection_exists(self, name):
        return True

    def create_collection(self, name, dim):
        pass

    def upsert_points(self, name, points):
        pass

    def purge_file_points(self, name, project, path, min_chunk_index=0):
        pass


HPP = """class FileTransferSession {
 public:
  void start();
};
"""
PY = "def brand_new_helper():\n    return 'fresh'\n"

root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "src"))
hpp_path = os.path.join(root, "src", "session.hpp")
with open(hpp_path, "w") as f:
    f.write(HPP)
with open(os.path.join(root, "src", "new_file.py"), "w") as f:
    f.write(PY)

cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
             index_root=tempfile.mkdtemp(), stale_ttl=60, embed_batch=48,
             upsert_batch=256, max_file_bytes=1048576, watch_debounce=3)
idx = Indexer(cfg, StubEmbedder(), StubStore())
m = Manifest(os.path.join(cfg.index_root, "m.db"))

print("PASS1", idx.index_project(root, "s", m))
print("  FileTransferSession:", m.find_symbols("FileTransferSession"))

# Pass 2: modify hpp (add stop), add py2, delete hpp
with open(hpp_path, "a") as f:
    f.write("\nvoid added_later() {}\n")
with open(os.path.join(root, "src", "py2.py"), "w") as f:
    f.write("def second_helper():\n    return 2\n")
os.remove(hpp_path)

print("PASS2", idx.index_project(root, "s", m))
print("  added_later:", m.find_symbols("added_later"))
print("  second_helper:", m.find_symbols("second_helper"))
print("  FileTransferSession (should be gone):", m.find_symbols("FileTransferSession"))
print("  refs second_helper:", m.find_refs("second_helper"))
m.close()
shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(cfg.index_root, ignore_errors=True)
