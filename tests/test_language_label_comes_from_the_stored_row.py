"""A hit labels its language from the stored row, not from the file's name.

Two surfaces still asked the extension after the index started deciding a
`.h` header's grammar from its contents: the lexical-search hit's `lang` and
`find-symbol --tree`'s dominant language for a directory. A C header the
index read with the C grammar is stored `c`, so both surfaces answered `cpp`
-- one on a header that is not C++, the other naming a C directory cpp (and
so telling a reader the project has C++ in it that does not exist).

Offline: real chunker, stub embedder and store, no network.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

runner = CliRunner()

POINT_HEADER = """\
#ifndef POINT_H
#define POINT_H

struct Point {
    int x;
};

#endif
"""

OTHER_HEADER = """\
#ifndef EXTRA_H
#define EXTRA_H

struct Extra {
    int y;
};

#endif
"""

MAIN_SOURCE = """\
#include "point.h"

int origin(void) {
    struct Point p;
    return p.x;
}
"""


@pytest.fixture()
def c_header_project(tmp_path, monkeypatch):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (project / "point.h").write_text(POINT_HEADER, encoding="utf-8")
    (project / "extra.h").write_text(OTHER_HEADER, encoding="utf-8")
    (project / "main.c").write_text(MAIN_SOURCE, encoding="utf-8")
    core = Core(cfg, skip_stale_check=True)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

        def query_instruction_text(self, q):
            return q

        def embed_with_errors(self, texts):
            return [[0.1] * 4 for _ in texts], []

        def close(self):
            pass

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def swap_collection(self, tmp, target):
            pass

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name="cheaders")
    core.registry = reg
    core.run_index(entry.slug, str(project))
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    yield core, entry, project
    reg.close()


def test_lexical_search_labels_a_c_header_c(c_header_project):
    """The file row says `c`; a lexical hit over it said `cpp`, because the
    label came from the extension map instead of the stored row."""
    core, entry, _project = c_header_project
    result = core.search("Point", project=entry.path, mode="lexical")
    hits = [h for h in result["hits"] if h["file"] == "point.h"]
    assert hits, result["hits"]
    assert {h["lang"] for h in hits} == {"c"}, hits


def test_a_c_header_does_not_make_a_directory_dominant_cpp(c_header_project):
    """Two `.h` headers and one `.c` file, all stored `c`: the directory's
    dominant language is `c`, and the name-only map said `cpp`."""
    core, entry, project = c_header_project
    result = runner.invoke(app, ["skeleton", "--project", str(project),
                                 "--tree", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    dirs = {d["dir"]: d for d in payload["dirs"]}
    assert dirs["."]["dominant"] == "c", payload
    assert dirs["."]["langs"] == {"c": 3}, payload
