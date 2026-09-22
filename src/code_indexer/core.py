"""Core operations for the code-indexer (design ruling §3).

All indexing/search/registry logic lives here; ``server.py`` (MCP adapters)
and ``cli.py`` (typer app) are thin surfaces over these functions, and every
tool/subcommand maps 1:1 to an op here. One-shot process model: no daemon,
no watcher on the default path — staleness is enforced via STALE_TTL probes
on search/status, guarded by the per-project flock.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import time
from typing import Any

from .config import Config, load_config
from .embedder import Embedder
from .indexer import Indexer
from .locks import project_lock
from .manifest import SCHEMA_VERSION, Manifest, SymbolRow
from .registry import ProjectEntry, Registry
from .store import Store

logger = logging.getLogger("code-indexer.core")

STALE_TTL_DEFAULT = 60


class Core:
    """Stateful core: owns config, registry, embedder, store, indexer.

    One instance per process, shared by the MCP adapter and the CLI. All
    mutating passes run under the per-project flock, so concurrent one-shot
    invocations (4 parallel CLI searches, server + CLI together) serialize:
    exactly one indexer pass runs, others report 'indexing in progress'.
    """

    def __init__(self, cfg: Config | None = None, skip_stale_check: bool = False):
        self.cfg = cfg or load_config()
        os.makedirs(self.cfg.index_root, exist_ok=True)
        self.skip_stale_check = skip_stale_check
        self.registry = Registry(os.path.join(self.cfg.index_root, "registry.db"))
        self.embedder = Embedder(self.cfg.ollama_url, self.cfg.embed_model,
                                 batch_size=self.cfg.embed_batch)
        self.store = Store(self.cfg.qdrant_url, upsert_batch=self.cfg.upsert_batch)
        self.indexer = Indexer(self.cfg, self.embedder, self.store)
        # Per-process staleness cache: slug -> last-scan monotonic time.
        self._last_scan: dict[str, float] = {}
        # Indexing state per slug (state, error, last_result) for status.
        self._index_state: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # paths / manifests / state
    # ------------------------------------------------------------------

    def manifest_for(self, slug: str) -> Manifest:
        d = os.path.join(self.cfg.index_root, slug)
        os.makedirs(d, exist_ok=True)
        return Manifest(os.path.join(d, "manifest.db"))

    def _lock_path(self, slug: str) -> str:
        return os.path.join(self.cfg.index_root, f"{slug}.lock")

    def _set_state(self, slug: str, **kw: Any) -> None:
        self._index_state.setdefault(slug, {}).update(kw)

    def state_for(self, slug: str) -> dict[str, Any]:
        return dict(self._index_state.get(slug, {}))

    # ------------------------------------------------------------------
    # indexing
    # ------------------------------------------------------------------

    def run_index(self, slug: str, project_path: str, force: bool = False,
                  verbose: bool = False) -> dict[str, Any]:
        """Run one indexing pass under the per-project lock.

        Returns {'state': 'idle', 'result': IndexResult-dict} on success,
        {'state': 'indexing', 'detail': 'indexing in progress'} when another
        process holds the lock, or {'state': 'error', 'error': ...}.
        ``verbose=True`` (CLI --refresh) surfaces the pass summary; the
        default query-path pass is silent.
        """
        with project_lock(self._lock_path(slug)) as acquired:
            if not acquired:
                return {"state": "indexing", "detail": "indexing in progress"}
            self._set_state(slug, state="indexing", error=None)
            manifest = self.manifest_for(slug)
            try:
                result = self.indexer.index_project(
                    project_path, slug, manifest, force_full=force)
                self._last_scan[slug] = time.monotonic()
                self._set_state(slug, state="idle", last_result=vars(result))
                if verbose:
                    logger.info("index pass %s: %s", slug, vars(result))
                return {"state": "idle", "result": vars(result)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("indexing failed for %s", project_path)
                self._set_state(slug, state="error", error=str(exc))
                return {"state": "error", "error": str(exc)}
            finally:
                manifest.close()

    def watch_pass(self, slug: str, project_path: str) -> dict[str, Any]:
        """One watcher pass: staleness probe + incremental index under the flock.

        Same core op as maybe_refresh's terminal call (run_index with
        force=False), but unconditioned by the STALE_TTL cache — the watch
        loop calls this every tick. Cheap when nothing changed (hash diff
        only; embeddings happen only for real changes).
        """
        return self.run_index(slug, project_path, force=False)

    def maybe_refresh(self, slug: str, project_path: str,
                      force: bool = False) -> dict[str, Any]:
        """Staleness probe: incremental re-index if last check > stale_ttl ago.

        Default (quiet) mode: within TTL, or on a flock miss, this returns
        without indexing — a query-path re-index costs latency and noise.
        The pass is silent (no logging at INFO) and only runs when actually
        stale. Callers that want the pass regardless of TTL pass force=True
        (CLI --refresh). Skipped entirely when the core was built with
        skip_stale_check=True (--skip-stale-check on the CLI; the vetoed
        daemon is NOT used).
        """
        if self.skip_stale_check:
            return {"state": "fresh"}
        last = self._last_scan.get(slug, 0.0)
        if not force and time.monotonic() - last < self.cfg.stale_ttl:
            return {"state": "fresh"}
        entry = self.registry.get_by_slug(slug)
        if entry is None or not os.path.isdir(entry.path):
            return {"state": "idle"}
        return self.run_index(slug, entry.path, force=False, verbose=force)

    # ------------------------------------------------------------------
    # project resolution / summaries
    # ------------------------------------------------------------------

    def _containing_entry(self, target: str) -> ProjectEntry | None:
        """Most-specific registered project whose path contains ``target``."""
        candidates = [
            e for e in self.registry.list_projects()
            if target == e.path or target.startswith(e.path.rstrip("/") + "/")
        ]
        return max(candidates, key=lambda e: len(e.path)) if candidates else None

    def resolve_entry(self, project: str | None,
                      hint: str | None = None) -> tuple[ProjectEntry | None, str]:
        """Resolve an optional project arg (path, slug, or custom name).

        With ``project`` unset, try to infer the project from ``hint`` (e.g. a
        positional path prefix) and then the current working directory: the
        most-specific registered ancestor wins. Falls back to the legacy
        single-project/ambiguity behavior when nothing matches.
        """
        if project is None:
            for target in (hint, os.getcwd()):
                if not target:
                    continue
                apath = os.path.abspath(os.path.expanduser(target))
                entry = self._containing_entry(apath)
                if entry is not None:
                    return entry, ""
            entries = self.registry.list_projects()
            if not entries:
                return None, "error: no projects registered"
            if len(entries) > 1:
                return None, ("error: multiple projects registered — pass project "
                              "(path, slug, or name): "
                              + ", ".join(e.path for e in entries))
            return entries[0], ""
        apath = os.path.abspath(os.path.expanduser(project))
        entry = (self.registry.get_by_path(apath) or self.registry.get_by_slug(project)
                 or self.registry.get_by_name(project))
        if entry is None:
            real = os.path.realpath(apath)
            if real != apath:
                entry = self.registry.get_by_path(real)
        if entry is None:
            return None, f"error: project not registered: {project}"
        return entry, ""

    def status_summary(self, entry: ProjectEntry) -> str:
        """Human-readable per-project summary (path, slug, counts, times)."""
        file_count = chunk_count = 0
        last_indexed = None
        mdir = os.path.join(self.cfg.index_root, entry.slug, "manifest.db")
        if os.path.isfile(mdir):
            m = Manifest(mdir)
            try:
                rows = m.all_files()
                file_count = len(rows)
                chunk_count = sum(r.chunk_count for r in rows.values())
                li = m.get_meta("last_indexed")
                if li:
                    last_indexed = time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(float(li)))
            finally:
                m.close()
        st = self.state_for(entry.slug).get("state", "idle")
        return (f"path={entry.path} slug={entry.slug} state={st} "
                f"files={file_count} chunks={chunk_count} "
                f"last_indexed={last_indexed or 'never'}")

    def schema_migrated(self, entry: ProjectEntry) -> bool:
        """True when this manifest was just upgraded from a pre-symbol schema."""
        m = self.manifest_for(entry.slug)
        try:
            return getattr(m, "_migrated_from", SCHEMA_VERSION) not in (None, SCHEMA_VERSION)
        finally:
            m.close()

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def search_one(self, entry: ProjectEntry, query: str, limit: int,
                   file_filter: str | None) -> list[dict[str, Any]]:
        collection = f"idx_{entry.slug}"
        vector = self.embedder.embed([query])[0]
        hits = self.store.search(collection, vector, limit=limit, file_filter=file_filter)
        out = []
        for h in hits:
            p = h.payload or {}
            out.append({
                "project": entry.path,
                "file": p.get("file"),
                "score": round(h.score, 4),
                "symbol": p.get("symbol"),
                "symbol_type": p.get("symbol_type"),
                "start_line": p.get("start_line"),
                "end_line": p.get("end_line"),
                "snippet": (p.get("snippet") or "")[:500],
            })
        return out

    def search(self, query: str, project: str | None = None, limit: int = 8,
               file_filter: str | None = None,
               skip_refresh: bool = False) -> list[dict[str, Any]]:
        """Semantic search across one or all registered projects.

        Runs the staleness probe per project first (unless skipped). Raises
        ValueError with a user-facing message on resolution/search errors —
        the adapters turn that into their error surface.
        """
        if project:
            entry, err = self.resolve_entry(project)
            if entry is None:
                raise ValueError(err)
            entries = [entry]
        else:
            entries = self.registry.list_projects()
            if not entries:
                raise ValueError("error: no projects registered")
        all_hits: list[dict[str, Any]] = []
        for entry in entries:
            if not skip_refresh:
                self.maybe_refresh(entry.slug, entry.path)
            try:
                all_hits.extend(self.search_one(entry, query, limit, file_filter))
            except Exception as exc:  # noqa: BLE001
                logger.exception("search failed for %s", entry.path)
                raise ValueError(f"error: search failed on {entry.path}: {exc}") from exc
        all_hits.sort(key=lambda h: h["score"], reverse=True)
        return all_hits[:max(1, limit)]

    def format_hits(self, hits: list[dict[str, Any]], fmt: str = "text") -> str:
        """Format search hits as text lines or JSON (stable field contract)."""
        if not hits:
            return "no results"
        if fmt == "json":
            return json.dumps(hits, ensure_ascii=False, indent=2)
        lines = ["search results (score desc):"]
        for h in hits:
            sym = f" ({h['symbol']}" if h["symbol"] else ""
            if sym:
                sym += f", {h['symbol_type']})" if h.get("symbol_type") else ")"
            lines.append(
                f"- [{h['score']:.4f}] {h['project']}::{h['file']}"
                f":{h['start_line']}-{h['end_line']}{sym}"
            )
            lines.append(f"    {h['snippet'][:200]}")
        return "\n".join(lines)

    def search_for_display(self, query: str, project: str | None = None,
                           limit: int = 8, file_filter: str | None = None,
                           fmt: str = "text",
                           skip_refresh: bool = False) -> str:
        try:
            hits = self.search(query, project=project, limit=limit,
                               file_filter=file_filter, skip_refresh=skip_refresh)
        except ValueError as exc:
            return str(exc)
        return self.format_hits(hits, fmt)

    # ------------------------------------------------------------------
    # token reduction: skeleton / outline (subcommand design §2.1/§2.2)
    # ------------------------------------------------------------------

    def skeleton(self, entry: ProjectEntry, prefix: str | None = None,
                 tree_mode: bool = False, limit: int | None = None,
                 include_signatures: bool = True) -> dict[str, Any]:
        """Whole-project or per-subtree structural map (design §2.1).

        Manifest only — files joined with symbols ordered by start_line.
        Zero re-parsing. Pre-v3 manifests simply have NULL signatures (and
        and the caller emits MIGRATION_HINT when a migration just happened).
        """
        if not self.skip_stale_check:
            self.maybe_refresh(entry.slug, entry.path)
        m = self.manifest_for(entry.slug)
        try:
            files, symbols = m.symbols_with_files()
        finally:
            m.close()
        if prefix:
            prefix = prefix.rstrip("/")
            symbols = [s for s in symbols if s.file == prefix
                       or s.file.startswith(prefix + "/")]
        files = {p: f for p, f in files.items()
                 if not prefix or p == prefix or p.startswith(prefix + "/")}
        by_file: dict[str, list[SymbolRow]] = {}
        for s in symbols:
            by_file.setdefault(s.file, []).append(s)
        if tree_mode:
            return self._tree_projection(entry, sorted(files), by_file)
        out_files: list[dict[str, Any]] = []
        for path in sorted(files):
            f = files[path]
            rows = by_file.get(path, [])
            if limit is not None:
                rows = rows[:limit]
            out_files.append({
                "file": path,
                "lang": _lang_from_path(path),
                "size": f.size,
                "total_symbols": len(by_file.get(path, [])),
                "symbols": [
                    {"name": s.name, "type": s.symbol_type,
                     "start_line": s.start_line, "end_line": s.end_line,
                     "signature": s.signature if include_signatures else None}
                    for s in rows
                ],
            })
        return {"project": entry.path, "files": out_files}

    @staticmethod
    def _tree_projection(
            entry: ProjectEntry,
            file_paths: list[str],
            by_file: dict[str, list[SymbolRow]]) -> dict[str, Any]:
        """`--tree` mode (design §2.1): directory tree with per-dir symbol
        count and dominant language. No symbols listed; --limit ignored."""
        dirs: dict[str, dict[str, Any]] = {}
        for path in file_paths:
            parent = os.path.dirname(path).replace(os.sep, "/") or "."
            d = dirs.setdefault(parent, {"symbols": 0, "langs": {}})
            d["symbols"] += len(by_file.get(path, []))
            lang = _lang_from_path(path)
            d["langs"][lang] = d["langs"].get(lang, 0) + 1
        out_dirs = [
            {"dir": path, "symbols": d["symbols"],
             "dominant": (max(sorted(d["langs"]),
                              key=lambda l: d["langs"][l])
                          if d["langs"] else None),
             "langs": dict(sorted(d["langs"].items()))}
            for path, d in sorted(dirs.items())]
        return {"project": entry.path, "dirs": out_dirs}

    def format_skeleton(self, data: dict[str, Any], fmt: str = "text") -> str:
        """Dense skeleton render (design §2.1): one file per group, one
        symbol per line, no prose, no blank lines. `--tree` renders the
        directory-tree projection (per-dir symbol count + dominant language)."""
        if fmt == "json":
            return json.dumps(data, ensure_ascii=False, indent=2)
        if "dirs" in data:
            lines = [f"{d['dir']}/  ({d['symbols']} symbols, "
                     f"{d['dominant'] or 'no code'})" for d in data["dirs"]]
            return "\n".join(lines) if lines else "(no files)"
        lines: list[str] = []
        for f in data["files"]:
            total = f.get("total_symbols", len(f["symbols"]))
            shown = len(f["symbols"])
            header = (f"{f['file']}  ({f['lang']}, {total} symbols)"
                      if shown == total else
                      f"{f['file']}  ({f['lang']}, {total} symbols, "
                      f"showing {shown})")
            lines.append(header)
            for s in f["symbols"]:
                sig = f"  {s['signature']}" if s.get("signature") else ""
                lines.append(
                    f"  {s['type']} {s['name']}:{s['start_line']}-"
                    f"{s['end_line']}{sig}")
        return "\n".join(lines) if lines else "(no files)"

    def outline(self, entry: ProjectEntry, file: str,
                include_docstrings: bool = False) -> dict[str, Any]:
        """One file: declarations, signatures, optional docstrings (§2.2).

        FILE is relative to the project root (manifest path space); absolute
        paths are normalized by stripping the project root prefix. Data
        source: Manifest.symbols_for_file + schema-v3 signature. --docstrings
        reads the declaration's first lines from disk — the only on-demand
        source read in the design.
        """
        rel = os.path.relpath(
            os.path.normpath(file if os.path.isabs(file)
                             else os.path.join(entry.path, file.lstrip("/"))),
            entry.path).replace(os.sep, "/")
        if rel.startswith(".."):
            raise ValueError(f"error: path outside registered project: {file}")
        if not self.skip_stale_check:
            self.maybe_refresh(entry.slug, entry.path)
        m = self.manifest_for(entry.slug)
        try:
            rows = m.symbols_for_file(rel)
        finally:
            m.close()
        if not rows and not self.registry_has_file(entry, rel):
            raise ValueError(f"error: file not indexed: {file}")
        src_lines: list[str] = []
        if include_docstrings:
            abs_path = os.path.join(entry.path, rel)
            if os.path.isfile(abs_path):
                # Bounded read: docstrings live within decl+3 lines, so
                # cap at the last declaration's window, not the whole file.
                max_line = max((r.end_line for r in rows), default=0) + 4
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    src_lines = []
                    for _ in range(max_line):
                        line = fh.readline()
                        if not line:
                            break
                        src_lines.append(line.rstrip("\n"))
        out = []
        for r in rows:
            d: dict[str, Any] = {
                "name": r.name, "type": r.symbol_type,
                "start_line": r.start_line, "end_line": r.end_line,
                "signature": r.signature,
            }
            if include_docstrings and src_lines:
                doc = self._first_docstring(src_lines, r.start_line)
                if doc:
                    d["doc"] = doc
            out.append(d)
        return {"file": rel, "declarations": out}

    @staticmethod
    def _first_docstring(lines: list[str], start_line: int) -> str | None:
        """First docstring/comment line from the decl line onward (bounded:
        decl line + 3), truncated to ~100 chars."""
        for i in range(start_line, min(start_line + 4, len(lines) + 1)):
            stripped = lines[i - 1].strip()
            if not stripped:
                continue
            if stripped.startswith(('"""', "'''", '"', "'", "#", "//", "/*")):
                cleaned = stripped.strip('"\'')
                cleaned = cleaned.lstrip("#/ ").strip()
                return cleaned[:100] or None
            if i == start_line:
                continue  # the declaration line itself
            return None  # body code before any docstring → none
        return None

    def registry_has_file(self, entry: ProjectEntry, rel: str) -> bool:
        m = self.manifest_for(entry.slug)
        try:
            return m.get_file(rel) is not None
        finally:
            m.close()

    def format_outline(self, data: dict[str, Any], fmt: str = "text") -> str:
        """Dense outline render (§2.2): one declaration per line."""
        if fmt == "json":
            return json.dumps(data, ensure_ascii=False, indent=2)
        lines: list[str] = []
        for d in data["declarations"]:
            sig = f"  {d['signature']}" if d.get("signature") else ""
            lines.append(
                f"{d['type']} {d['name']}:{d['start_line']}-{d['end_line']}{sig}")
            if d.get("doc"):
                lines.append(f"  {d['doc']}")
        return "\n".join(lines) if lines else "(no declarations)"


MIGRATION_HINT = (
    "note: project manifest was upgraded from an older schema — run "
    "reindex_project once to populate the symbol index"
)


# ---------------------------------------------------------------------------
# Token-reduction ops (subcommand design §2.1/§2.2): manifest-only, no parsing
# at query time. Each op has a format_* twin following format_hits' pattern.
# ---------------------------------------------------------------------------

def _lang_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "js": "javascript", "ts": "typescript",
        "rs": "rust", "go": "go", "java": "java", "c": "c", "cpp": "cpp",
        "cc": "cpp", "h": "c", "hpp": "cpp", "cs": "csharp", "rb": "ruby",
        "php": "php", "sh": "bash", "md": "markdown", "json": "json",
        "yaml": "yaml", "yml": "yaml", "toml": "toml",
    }.get(ext, ext or "text")
