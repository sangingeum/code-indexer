"""The three roster members with no payload path here: what they answer.

Usage: python probe_refusal_paths.py <tree>

Builds the same stub rig and prints, for `--json` and `--format json`, the
exit code, the stdout bytes and the first stderr line. A roster member is not
skipped: when there is no payload the two spellings must agree on the refusal
(stdout, stderr, exit), and this prints that agreement instead of naming it.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile

TREE = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.join(TREE, "src"))

import shutil  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

MODULE = "def helper(x):\n    return x + 1\n"


class StubEmbedder:
    def dimension(self) -> int:
        return 4

    def embed(self, texts):
        return [[0.1] * 4 for _ in texts]

    def embed_with_errors(self, texts):
        return [[0.1] * 4 for _ in texts], []

    def query_instruction_text(self, query: str) -> str:
        return query

    def close(self) -> None:
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

    def count_points(self, name):
        return 0

    def search(self, *a, **kw):
        return []

    def swap_collection(self, *a, **kw):
        return None


def main() -> int:
    pinned = os.environ.get("RIG_SCRATCH")
    if pinned:
        shutil.rmtree(pinned, ignore_errors=True)
        os.makedirs(pinned)
        scratch = pinned
    else:
        scratch = tempfile.mkdtemp(prefix="flagmatrix-")
    index_root = os.path.join(scratch, "state")
    os.environ["INDEX_ROOT"] = index_root
    cfg = Config(ollama_url="http://stub", qdrant_url="http://stub",
                 embed_model="stub", index_root=index_root,
                 embed_batch=48, upsert_batch=256,
                 max_file_bytes=1048576, watch_debounce=1,
                 watch_sweep_interval=3600)
    core = Core(cfg)
    core.embedder = StubEmbedder()
    core.store = StubStore()
    core.indexer.embedder = core.embedder
    core.indexer.store = core.store
    project_dir = os.path.join(scratch, "proj")
    os.makedirs(project_dir)
    with open(os.path.join(project_dir, "mod.py"), "w", encoding="utf-8") as fh:
        fh.write(MODULE)
    reg = Registry(os.path.join(index_root, "registry.db"))
    entry = reg.add(project_dir, name="proj")
    core.run_index(entry.slug, project_dir)
    cli._get_core = lambda skip: core  # type: ignore[assignment]

    runner = CliRunner()
    rows = [("plan-show", ()), ("plan-validate", ()),
            ("plan-show", ("no-such-plan",)),
            ("plan-validate", ("no-such-plan",)),
            ("semantic-search", ("helper",))]
    print("command\tspelling\texit\tstdout_bytes\tstderr_first_line\tagree")
    for name, args in rows:
        seen = []
        for spelling, extra in (("--json", ("--json",)),
                                ("--format json", ("--format", "json"))):
            result = runner.invoke(app, [name, *args, *extra,
                                         "--project", str(project_dir)])
            out = result.stdout or ""
            err = (result.stderr or "").strip().splitlines()
            seen.append((out, result.stderr or "", result.exit_code))
            body = [line for line in err
                    if line[:1] not in {"\u256d", "\u2570", "\u2502", "\u2500", "\u2577"}
                    and line.strip()]
            print("\t".join([
                f"{name} {' '.join(args)}".strip(), spelling, str(result.exit_code),
                str(len(out.encode())),
                (body[-1] if body else (err[-1] if err else "-"))[:120],
                "-",
            ]))
        agree = seen[0] == seen[1]
        print(f"# {name} {' '.join(args)}: two spellings agree on stdout+stderr+exit = {agree}")
    reg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
