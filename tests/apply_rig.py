"""The floor the apply tests stand on: a project, a plan and a stub keeper.

Nothing here touches the real checkout. Each rig is a throwaway registered
project under the test's own tmp_path, with its own plan dir, its own
filekeeper journal and a stub ``keeper`` first on PATH, so an apply test can
only ever mutate the fixture.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import keeper_stub  # noqa: E402  (tests/ is on sys.path under pytest)

from code_indexer import plans, ts_chunker  # noqa: E402
from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

PROJECT_NAME = "applysample"


def config_for(tmp_path) -> Config:
    return Config(ollama_url="http://stub", qdrant_url="http://stub",
                  embed_model="stub", index_root=str(tmp_path / "state"),
                  embed_batch=48, upsert_batch=256,
                  max_file_bytes=1048576, watch_debounce=1,
                  watch_sweep_interval=3600)


@dataclass
class Rig:
    """One throwaway project, wired to a stub keeper."""

    root: str
    plan_dir: str
    bin_dir: str
    keeper_home: str
    entry: object
    core: object
    registry: Registry

    def path(self, rel_path: str) -> str:
        return os.path.join(self.root, rel_path)

    def read(self, rel_path: str) -> str:
        with open(self.path(rel_path), "rb") as handle:
            return handle.read().decode("utf-8")

    def write(self, rel_path: str, text: str) -> None:
        with open(self.path(rel_path), "wb") as handle:
            handle.write(text.encode("utf-8"))

    def bytes_of(self, rel_path: str) -> bytes:
        with open(self.path(rel_path), "rb") as handle:
            return handle.read()

    def journal(self) -> list[dict]:
        """The stub's own record: what keeper was actually asked to do."""
        path = os.path.join(self.keeper_home, "journal.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def mutations(self) -> list[dict]:
        return [item for item in self.journal()
                if item.get("kind") == "mutation"]


def build(tmp_path, monkeypatch, files: dict[str, str]) -> Rig:
    """Register a project holding ``files`` and put the stub keeper on PATH."""
    import code_indexer.cli as cli

    monkeypatch.setenv("INDEX_ROOT", str(tmp_path / "state"))
    cli._core = None
    cli._core_skip = False
    cfg = config_for(tmp_path)
    core = Core(cfg)
    project = tmp_path / "proj"
    project.mkdir()
    for rel_path, text in files.items():
        target = project / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    registry = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = registry.add(str(project), name=PROJECT_NAME)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    bin_dir = str(tmp_path / "bin")
    keeper_stub.install(bin_dir)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep
                       + os.environ.get("PATH", ""))
    keeper_home = str(tmp_path / "keeper-home")
    monkeypatch.setenv("STUB_KEEPER_HOME", keeper_home)
    return Rig(root=str(project),
               plan_dir=os.path.join(str(project), plans.DEFAULT_PLAN_DIR),
               bin_dir=bin_dir, keeper_home=keeper_home, entry=entry,
               core=core, registry=registry)


def declaration(tf: plans.TextFile, symbol: str):
    """The one declaration in a file whose short name is ``symbol``."""
    derived, _verdict = plans.declarations(tf.rel_path, tf.text)
    for decl in derived:
        if decl.qualified_name.rsplit(".", 1)[-1] == symbol:
            return decl
    raise AssertionError(f"no declaration {symbol!r} in {tf.rel_path}")


def language_of(rel_path: str) -> str:
    return ts_chunker.EXT_LANG.get(rel_path.rsplit(".", 1)[-1].lower(),
                                   "unknown")


def plan_replacing(rig: Rig, targets: list[tuple[str, str, str]]) -> str:
    """A sealed plan that replaces one body per target, written to the plan dir.

    ``targets`` is ``(rel_path, symbol, new_body_text)``, and the plan format's
    own ordering decides the application order, so the caller states neither.
    """
    files: list[dict] = []
    edits: list[dict] = []
    seen: set[str] = set()
    target_record: dict | None = None
    for rel_path, symbol, new_text in targets:
        tf = plans.read_text_file(rig.root, rel_path)
        decl = declaration(tf, symbol)
        if rel_path not in seen:
            seen.add(rel_path)
            files.append(plans.file_record(tf, language_of(rel_path)))
        edits.append(plans.replace_body_edit(tf, decl, new_text))
        if target_record is None:
            target_record = plans.locator(tf, decl, language_of(rel_path))
    document = plans.build_document(
        operation="replace_body",
        repository={"project": PROJECT_NAME, "index_revision": "rev-1"},
        files=files, target=target_record, edits=edits,
        created_at="2026-11-07T00:00:00Z")
    return plans.write_plan(document, rig.plan_dir)


def plan_replacing_one(rig: Rig, rel_path: str, symbol: str,
                       new_text: str) -> str:
    return plan_replacing(rig, [(rel_path, symbol, new_text)])
