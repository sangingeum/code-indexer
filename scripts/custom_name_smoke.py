"""Live smoke for add_project(name=...): register with a custom name, wait
for idle, search by name, remove. Uses temp INDEX_ROOT + throwaway repo."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["INDEX_ROOT"] = tempfile.mkdtemp(prefix="mci-name-")

from mcp_code_indexer import server as srv  # type: ignore[import-not-found]  # noqa: E402  (module under test)
from e2e_helpers import make_repo  # noqa: E402

repo = tempfile.mkdtemp(prefix="mci-name-repo-")
make_repo(repo)

try:
    out = srv.add_project(repo, name="smoke_custom-1")
    print("add:", out)
    assert "registered" in out and "smoke_custom-1" in out

    # Wait for the background index to finish.
    for _ in range(60):
        st = srv.index_status(repo)
        if "state=idle" in st and "last_pass" in st:
            break
        time.sleep(5)
    else:
        raise SystemExit(f"TIMEOUT waiting for idle: {st}")

    # Search by custom name (semantic_search project lookup).
    res = srv.semantic_search("database connection pool", project="smoke_custom-1")
    print("search by name ok:", res.splitlines()[0])
    assert "no results" not in res

    # Collision error path.
    out2 = srv.add_project(tempfile.mkdtemp(prefix="mci-name-clash-"), name="smoke_custom-1")
    assert out2.startswith("error:") and "collision" in out2, out2
    print("collision path ok:", out2)

    # Sanitization: '/' etc. collapse.
    out3 = srv.add_project(tempfile.mkdtemp(prefix="mci-name-san-"), name="a/b c")
    assert "a_b_c" in out3, out3
    print("sanitize path ok:", out3)

    # Cleanup.
    print(srv.remove_project(repo))
    print("NAME-SMOKE PASS")
finally:
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(os.environ["INDEX_ROOT"], ignore_errors=True)
