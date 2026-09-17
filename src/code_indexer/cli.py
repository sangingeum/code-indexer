"""code-indexer CLI: thin typer app over code_indexer.core (design ruling §3).

Every subcommand maps 1:1 to a core op / MCP tool of the same name. One-shot
process: no daemon, no server RPC, no 'start server on first call'. The
--skip-stale-check flag covers startup-cost-sensitive calls; staleness is
normally enforced by the core's STALE_TTL probe guarded by the per-project
flock (concurrent invocations serialize; exactly one indexer pass runs).
"""

from __future__ import annotations

import json
import os
from typing import NoReturn

import typer

from .core import Core, MIGRATION_HINT
from .registry import ProjectEntry

app = typer.Typer(
    name="code-indexer",
    help="Semantic code index via Ollama + Qdrant (one-shot CLI; no daemon).",
    no_args_is_help=True,
)

_core: Core | None = None
_core_skip: bool = False


def _get_core(skip_stale_check: bool) -> Core:
    """Process-wide core; rebuilt only if the staleness flag differs."""
    global _core, _core_skip
    if _core is None or _core_skip != skip_stale_check:
        _core = Core(skip_stale_check=skip_stale_check)
        _core_skip = skip_stale_check
    return _core


def _die(msg: str) -> NoReturn:
    typer.echo(msg, err=True)
    raise typer.Exit(1)


def _resolve(core: Core, project: str | None) -> ProjectEntry:
    """Resolve an optional project arg (path, slug, name) or exit with error."""
    entry, err = core.resolve_entry(project)
    if entry is None:
        _die(err)
    assert entry is not None
    return entry


def _echo_index_result(slug: str, result: dict) -> None:
    """Render one run_index outcome; 'indexing' means another process held
    the flock — that process performs the pass (exactly-one-pass rule)."""
    if result["state"] == "indexing":
        typer.echo(f"{slug}: {result['detail']}")
    elif result["state"] == "error":
        _die(f"{slug}: indexing error: {result['error']}")
    else:
        typer.echo(f"{slug}: {json.dumps(result['result'], ensure_ascii=False)}")


SkipOpt = typer.Option(
    False, "--skip-stale-check",
    help="Skip the staleness probe / incremental index pass on this invocation.")


@app.command()
def add_project(
    path: str = typer.Argument(..., help="Absolute project directory to register."),
    name: str = typer.Option(None, help="Custom collection name (idx_<name>)."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Register a project directory for semantic indexing (idempotent) and
    run the initial index pass."""
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        _die(f"error: path does not exist: {path}")
    entry = core.registry.get_by_path(path)
    if entry is not None:
        typer.echo(f"already registered — {core.status_summary(entry)}")
        return
    try:
        entry = core.registry.add(path, name=name)
    except ValueError as exc:
        _die(f"error: {exc}")
    typer.echo(f"registered {entry.path} (slug {entry.slug})")
    _echo_index_result(entry.slug, core.run_index(entry.slug, entry.path))


@app.command()
def remove_project(
    path: str = typer.Argument(..., help="Project path (or slug/name)."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Deregister a project and DELETE its collection + manifest + registry
    entry entirely."""
    import glob as _glob
    import shutil as _shutil
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.remove(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    collection = f"idx_{entry.slug}"
    if core.store.collection_exists(collection):
        core.store.drop_collection(collection)
    for f in _glob.glob(os.path.join(core.cfg.index_root, f"{entry.slug}*")):
        try:
            os.remove(f)
        except OSError:
            pass
    manifest_dir = os.path.join(core.cfg.index_root, entry.slug)
    if os.path.isdir(manifest_dir):
        _shutil.rmtree(manifest_dir, ignore_errors=True)
    typer.echo(f"removed {path}: collection {collection} dropped, registry entry deleted")


@app.command(name="list-projects")
def list_projects(skip_stale_check: bool = SkipOpt) -> None:
    """List registered projects with per-project index summary."""
    core = _get_core(skip_stale_check)
    entries = core.registry.list_projects()
    if not entries:
        typer.echo("no projects registered")
        return
    for e in entries:
        typer.echo(f"- {core.status_summary(e)}")


@app.command(name="lookup-project")
def lookup_project(
    path: str = typer.Argument(..., help="Project path to check."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Check whether a project is registered (no indexing triggered)."""
    core = _get_core(skip_stale_check)
    raw = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.get_by_path(raw)
    if entry is None:
        real = os.path.realpath(raw)
        if real != raw:
            entry = core.registry.get_by_path(real)
    if entry is None:
        typer.echo(f"not registered: {raw}")
        return
    typer.echo(f"registered {core.status_summary(entry)} collection=idx_{entry.slug}")


@app.command(name="semantic-search")
def semantic_search(
    query: str = typer.Argument(..., help="Natural-language query."),
    project: str = typer.Option(None, help="Project path, slug, or name."),
    limit: int = typer.Option(8, help="Max hits."),
    file_filter: str = typer.Option(None, help="Substring/glob on file path, e.g. '*.py'."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Semantic code search. Staleness check + incremental indexing run
    first unless --skip-stale-check is given."""
    core = _get_core(skip_stale_check)
    typer.echo(core.search_for_display(
        query, project=project, limit=limit, file_filter=file_filter,
        fmt="json" if json_output else "text"))


@app.command(name="index-status")
def index_status(
    path: str = typer.Argument(..., help="Registered project path."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Report indexing state (idle | indexing | stale | error) and trigger
    the staleness check unless skipped."""
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.get_by_path(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    refresh = core.maybe_refresh(entry.slug, entry.path)
    st = core.state_for(entry.slug)
    parts = [f"project={path}", f"state={st.get('state', 'idle')}"]
    if st.get("last_result"):
        parts.append(f"last_pass={st['last_result']}")
    if st.get("error"):
        parts.append(f"error={st['error']}")
    if refresh.get("state") == "indexing":
        parts.append("note=incremental pass ran/was held by another process")
    typer.echo(" ".join(parts))


@app.command(name="reindex-project")
def reindex_project(
    path: str = typer.Argument(..., help="Registered project path."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Force a full rebuild of a project's index (runs foreground in the CLI)."""
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.get_by_path(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    _echo_index_result(entry.slug, core.run_index(entry.slug, entry.path, force=True))


@app.command(name="find-symbol")
def find_symbol(
    name: str = typer.Argument(..., help="Symbol name."),
    project: str = typer.Option(None, help="Project path, slug, or name."),
    symbol_type: str = typer.Option(None, help="function|method|class|struct|enum|namespace"),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Look up symbols by name in the manifest symbol index (no semantic
    search). Exact AST-first, capped substring fallback."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    core.maybe_refresh(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(name, symbol_type=symbol_type, substring=False)
        match_label = False
        if not rows:
            rows = m.find_symbols(name, symbol_type=symbol_type, substring=True)
            for r in rows:
                r._substring = True  # type: ignore[attr-defined]
            match_label = True
        if not rows:
            msg = f"no symbols matching {name!r}"
            if core.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            typer.echo(msg)
            return
        for r in rows:
            label = ""
            if match_label:
                label = ", match=exact" if not getattr(r, "_substring", False) \
                    else ", match=substring"
            conf = "exact" if r.source == "ast" else "heuristic"
            typer.echo(f"- {entry.path}::{r.file}:{r.start_line}-{r.end_line} "
                       f"{r.name} ({r.symbol_type}, confidence={conf}{label})")
    finally:
        m.close()


@app.command(name="find-definition")
def find_definition(
    name: str = typer.Argument(..., help="Symbol name (exact match only)."),
    project: str = typer.Option(None, help="Project path, slug, or name."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Find where a symbol is declared (exact name match only)."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    core.maybe_refresh(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        rows = m.find_symbols(name, substring=False)
        if not rows:
            msg = f"no exact-match symbols named {name!r}"
            if core.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            typer.echo(msg)
            return
        for r in rows:
            conf = "exact" if r.source == "ast" else "heuristic"
            typer.echo(f"- {entry.path}::{r.file}:{r.start_line}-{r.end_line} "
                       f"{r.name} ({r.symbol_type}, confidence={conf})")
    finally:
        m.close()


@app.command(name="get-code-context")
def get_code_context(
    file: str = typer.Argument(..., help="File path relative to the project root."),
    project: str = typer.Option(None, help="Project path, slug, or name."),
    start_line: int = typer.Option(None, help="Start line (with --end-line)."),
    end_line: int = typer.Option(None, help="End line."),
    symbol: str = typer.Option(None, help="Resolve range(s) via this symbol."),
    context_lines: int = typer.Option(0, help="Pad each range by N lines."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Retrieve ONLY the relevant source lines instead of whole files."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    rel = file.lstrip("/")
    abs_path = os.path.normpath(os.path.join(entry.path, rel))
    if not abs_path.startswith(os.path.normpath(entry.path) + os.sep):
        _die(f"error: path outside registered project: {file}")
    if not os.path.isfile(abs_path):
        _die(f"error: file not found: {abs_path}")

    ranges: list[tuple[int, int]] = []
    if symbol:
        core.maybe_refresh(entry.slug, entry.path)
        m = core.manifest_for(entry.slug)
        try:
            rows = [r for r in m.find_symbols(symbol, substring=False)
                    if r.file == rel]
        finally:
            m.close()
        if not rows:
            _die(f"error: symbol {symbol!r} not indexed in {rel}")
        for r in rows[:5]:
            ranges.append((max(1, r.start_line - context_lines),
                           r.end_line + context_lines))
    elif start_line is not None:
        end = end_line if end_line is not None else start_line
        ranges.append((max(1, start_line - context_lines), end + context_lines))
    else:
        _die("error: provide --start-line/--end-line or --symbol")

    with open(abs_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    for s, e in ranges:
        typer.echo(f"--- {rel}:{s}-{e} ---")
        s2, e2 = max(1, s), min(total, e)
        if s > total:
            typer.echo(f"error: start_line {s} beyond end of file ({total} lines)")
            continue
        for i in range(s2, e2 + 1):
            typer.echo(f"{i:>5}| {lines[i - 1].rstrip()}")


@app.command(name="find-references")
def find_references(
    name: str = typer.Argument(..., help="Target symbol name."),
    project: str = typer.Option(None, help="Project path, slug, or name."),
    relationship: str = typer.Option(None, help="calls|inherits|includes|references"),
    limit: int = typer.Option(25, help="Max rows."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Find textual references TO a symbol (all confidence=heuristic)."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    core.maybe_refresh(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        rows = m.find_refs(name, relationship=relationship, limit=limit)
        if not rows:
            typer.echo(f"no references to {name!r}")
            return
        known = m.all_symbol_names()
        typer.echo(f"references to {name!r} (all confidence=heuristic):")
        for r in rows:
            exact = "exact" if r.target in known else "heuristic"
            src = f" in {r.src_symbol}" if r.src_symbol else ""
            typer.echo(f"- {entry.path}::{r.file}:{r.line}{src} "
                       f"{r.relationship} {r.target} (target_confidence={exact})")
    finally:
        m.close()


def main() -> None:
    """Console-script entry point (``code-indexer``)."""
    app()


if __name__ == "__main__":
    main()
