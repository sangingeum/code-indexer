"""Measure the flag matrix through the CLI seam at a given source tree.

Usage: python measure_flag_matrix.py <tree> [label]

Builds one indexed project in a scratch dir with the suite's stub embedder and
store, then invokes each read command under the spellings the round is about.
Prints one TSV row per (command, spelling) with exit code, stdout bytes and
stdout sha256, so identity is a byte comparison and not a claim.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile

TREE = os.path.abspath(sys.argv[1])
LABEL = sys.argv[2] if len(sys.argv) > 2 else ""
sys.path.insert(0, os.path.join(TREE, "src"))

from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

MODULE = (
    "def helper(x):\n"
    '    """Add one."""\n'
    "    return x + 1\n"
    "\n"
    "\n"
    "def caller():\n"
    "    return helper(1)\n"
    "\n"
    "\n"
    "class Widget:\n"
    "    def render(self):\n"
    "        return helper(2)\n"
)
DUPLICATE = "def step():\n    return 1\n"


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
    # A pinned scratch keeps the project path identical across tree runs, so a
    # byte comparison of two payloads compares payload, not temp-dir names.
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
    for name, body in (("mod.py", MODULE), ("dup_a.py", DUPLICATE),
                       ("dup_b.py", DUPLICATE)):
        with open(os.path.join(project_dir, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    reg = Registry(os.path.join(index_root, "registry.db"))
    entry = reg.add(project_dir, name="proj")
    core.run_index(entry.slug, project_dir)
    cli._get_core = lambda skip: core  # type: ignore[assignment]

    runner = CliRunner()
    commands = [
        ("overview", []),
        ("find-symbol", ["helper"]),
        ("find-definition", ["helper"]),
        ("semantic-search", ["helper"]),
        ("skeleton", []),
        ("outline", ["mod.py"]),
        ("index-status", []),
    ]
    spellings = [
        ("none", []),
        ("json", ["--json"]),
        ("json+format-json", ["--json", "--format", "json"]),
        ("json+format-text", ["--json", "--format", "text"]),
        ("json+format-compact", ["--json", "--format", "compact"]),
        ("format-json", ["--format", "json"]),
        ("format-text", ["--format", "text"]),
        ("format-compact", ["--format", "compact"]),
    ]
    outdir = os.environ.get("OUTDIR")
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    print("label\tcommand\tspelling\texit\tstdout_bytes\tstdout_sha256\tstderr_first_line")
    for name, args in commands:
        for spelling, extra in spellings:
            result = runner.invoke(app, [name, *args, *extra,
                                         "--project", str(project_dir)])
            out = result.stdout or ""
            err = (result.stderr or "").strip().splitlines()
            if outdir:
                with open(os.path.join(outdir, f"{LABEL}-{name}-{spelling}.out"),
                          "w", encoding="utf-8") as fh:
                    fh.write(out)
                with open(os.path.join(outdir, f"{LABEL}-{name}-{spelling}.err"),
                          "w", encoding="utf-8") as fh:
                    fh.write(result.stderr or "")
            print("\t".join([
                LABEL, name, spelling, str(result.exit_code),
                str(len(out.encode())),
                hashlib.sha256(out.encode()).hexdigest()[:16] if out else "-",
                (err[0] if err else "-").replace("\t", " "),
            ]))
    reg.close()
    print(f"# scratch=<scratch> tree={TREE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
