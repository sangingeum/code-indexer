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
import time
from typing import Any, NoReturn

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


def _resolve(core: Core, project: str | None,
             hint: str | None = None) -> ProjectEntry:
    """Resolve an optional project arg (path, slug, name) or exit with error.

    ``hint`` (e.g. a positional path prefix) disambiguates when multiple
    projects are registered and no --project was given.
    """
    entry, err = core.resolve_entry(project, hint=hint)
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
RefreshOpt = typer.Option(
    False, "--refresh",
    help="Force a fresh staleness pass on this query (runs an incremental "
         "index now; default is to index only when actually stale, quietly).")


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
    symbol_type: str = typer.Option(None, "--symbol-type",
        help="Filter by symbol type (function|method|class|struct|enum|namespace)."),
    language: str = typer.Option(None, help="Filter by language (python|csharp|cpp|...)."),
    ranking: str = typer.Option("vector", "--ranking",
        help="Ranking mode: vector (default) | metadata | hybrid."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Semantic code search. The staleness pass runs only when the index is
    actually stale (quietly); --refresh forces it now, --skip-stale-check
    skips the probe entirely. --ranking metadata applies small metadata
    score adjustments (definition boost, test-path penalty); --ranking
    hybrid fuses the vector score with a lexical token-overlap score
    (weighted sum, fused = vector_score + 0.25 * lexical)."""
    core = _get_core(skip_stale_check)
    if project:
        entry, err = core.resolve_entry(project)
        if entry is None:
            _die(err)
        core.maybe_refresh(entry.slug, entry.path, force=refresh)
    else:
        for e in core.registry.list_projects():
            core.maybe_refresh(e.slug, e.path, force=refresh)
    typer.echo(core.search_for_display(
        query, project=project, limit=limit, file_filter=file_filter,
        symbol_type=symbol_type, language=language, ranking_mode=ranking,
        fmt="json" if json_output else "text", skip_refresh=True))


@app.command(name="index-status")
def index_status(
    path: str = typer.Argument(..., help="Registered project path."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Report indexing state (idle | indexing | stale | error). By default
    this is informational only — it does NOT trigger a re-index. --refresh
    runs the staleness pass now (output notes the pass)."""
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.get_by_path(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    refresh_result = (core.maybe_refresh(entry.slug, entry.path, force=True)
                      if refresh else {"state": "not-requested"})
    st = core.state_for(entry.slug)
    parts = [f"project={path}", f"state={st.get('state', 'idle')}"]
    if st.get("last_result"):
        parts.append(f"last_pass={st['last_result']}")
    if st.get("error"):
        parts.append(f"error={st['error']}")
    if refresh_result.get("state") == "indexing":
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


@app.command()
def watch(
    paths: list[str] = typer.Argument(
        None, help="Project paths (or slugs/names) to watch. Repeatable."),
    all_projects: bool = typer.Option(
        False, "--all", help="Watch every registered project."),
    duration: float = typer.Option(
        None, "--duration",
        help="Seconds to run before exiting 0. 0 or omitted = run forever."),
    background: bool = typer.Option(
        False, "--background",
        help="Daemonize (double-fork); PID file ~/.code-indexer/watch.pid, "
             "logs ~/.code-indexer/watch.log."),
    foreground: bool = typer.Option(
        True, "--foreground", hidden=True,
        help="Run in the foreground (default; inherited stdio)."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Optional long-lived watcher: inotify-based re-index daemon.

    Linux inotify via the watchdog Observer library (opt-in `watch`
    dependency-group): the project roots are watched recursively and file
    events schedule an incremental pass after a quiet period of
    WATCH_DEBOUNCE seconds (alias WATCH_QUIET_PERIOD, default 3 — the old
    poll tick is now the debounce). A burst of events coalesces into at
    most one pass per quiet period; an event burst that changes nothing
    costs a hash scan only — zero embedding, zero Qdrant traffic.

    Fallbacks/self-heal:

    - A full staleness pass for every watched project runs every
      WATCH_SWEEP_INTERVAL seconds (default 300) even with zero events, so
      missed/lost events self-heal.
    - If watchdog is not installed or inotify watch descriptors are
      exhausted (OSError scheduling the recursive watches), the watcher
      degrades to quiet-period polling (one hash scan per project per
      quiet tick) — it never loses correctness, only latency.

    Self-write suppression: events under the index root, .git paths, and
    editor temp files (.swp, ~, .tmp, ...) are filtered, so the watcher's
    own manifest/registry writes never trigger a pass.

    Lifecycle: --duration T bounds the watcher's life to T seconds (then
    exit 0); 0 or omitted runs forever. SIGINT/SIGTERM exit 0 cleanly.
    Multiple projects are served round-robin in argument order (--all:
    registry order). The actual index passes still take the per-project
    flock, so a watcher never collides with a one-shot command.

    --background daemonizes (double-fork + setsid), writes the daemon PID
    to <index_root>/watch.pid guarded by an flock on that file (a second
    watcher is refused while a live one holds it — the flock, not the
    pid, is the liveness test), redirects stdout/stderr to
    <index_root>/watch.log, and exits the parent after the daemon's
    startup handshake. A stopped watcher leaves no live lock or PID
    residue (the pidfile is unlinked only after the flock is released).
    --foreground is the default (inherited stdio, normal output) and does
    not take the pidfile.

    This is opt-in and never a prerequisite: one-shot commands work without
    any watcher running.
    """
    import signal
    import threading as _threading

    core = _get_core(skip_stale_check)

    if all_projects and paths:
        _die("error: pass project paths OR --all, not both")
    if all_projects:
        entries = core.registry.list_projects()
        if not entries:
            _die("error: no projects registered")
    else:
        entries = []
        for p in paths or []:
            entries.append(_resolve(core, p))
        if not entries:
            _die("error: give at least one project path (or --all)")

    targets = [(e.slug, e.path) for e in entries]
    pidfile_path = os.path.join(core.cfg.index_root, "watch.pid")
    log_path = os.path.join(core.cfg.index_root, "watch.log")

    if background:
        _watch_background(core, targets, duration, pidfile_path, log_path)
        return  # parent path returns; daemon path exits inside spawn

    # ----- foreground (default): current behavior -----------------------
    deadline: float | None = (
        time.monotonic() + duration if duration and duration > 0 else None)

    stop = {"flag": False}

    def _handle(_sig: int, _frm: object) -> None:
        stop["flag"] = True  # flock releases via run_index's context manager

    if _threading.current_thread() is _threading.main_thread():
        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
    # (Non-main thread: signal handlers can't be installed; the loop still
    # honors --duration and stays joinable — tests rely on this.)

    quiet = max(1, core.cfg.watch_debounce)
    sweep_interval = max(1, core.cfg.watch_sweep_interval)
    engine = _make_engine(
        core, targets, quiet=quiet, sweep_interval=sweep_interval)
    mode = "inotify" if engine.start_events() else "poll"
    typer.echo(
        f"watching {len(targets)} project(s), mode={mode}, "
        f"quiet={quiet}s, sweep={sweep_interval}s, "
        f"duration={'forever' if deadline is None else f'{duration}s'}")
    try:
        engine.run(duration, stop)
    finally:
        typer.echo("watch: exiting")
    raise typer.Exit(0)


def _make_engine(core: Core, targets: list[tuple[str, str]], *, quiet: float,
                 sweep_interval: float) -> Any:
    """Build the event engine, degrading gracefully without watchdog."""
    from .watcher import SWEEP_INTERVAL_DEFAULT, WatchEngine

    return WatchEngine(
        core, targets, quiet=quiet,
        sweep_interval=sweep_interval if sweep_interval else SWEEP_INTERVAL_DEFAULT,
        index_root=core.cfg.index_root, echo=typer.echo)


def _watch_background(core: Core, targets: list[tuple[str, str]],
                      duration: float | None, pidfile_path: str,
                      log_path: str) -> None:
    """Daemonize and run the watcher loop in the daemon process."""
    from .watcher import PidFileLock, spawn_background

    daemon_state: dict[str, Any] = {}

    def setup() -> int:
        """Daemon-side startup: pidfile flock, signal handlers. 0 == ready."""
        import signal

        lock = PidFileLock(pidfile_path)
        if not lock.acquire():
            print(f"watch: refusing to start — a live watcher already holds "
                  f"{pidfile_path}", flush=True)
            return 1
        daemon_state["lock"] = lock
        stop = {"flag": False}

        def _handle(_sig: int, _frm: object) -> None:
            stop["flag"] = True

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        daemon_state["stop"] = stop
        print(f"watch daemon: pid={os.getpid()}, "
              f"watching {len(targets)} project(s), pidfile={pidfile_path}, "
              f"log={log_path}", flush=True)
        return 0

    def run_loop() -> None:
        core = Core()  # fresh core in the daemon process
        quiet = max(1, core.cfg.watch_debounce)
        engine = _make_engine(core, targets, quiet=quiet,
                              sweep_interval=max(1, core.cfg.watch_sweep_interval))
        try:
            engine.start_events()
            engine.run(duration, daemon_state["stop"])
        finally:
            daemon_state["lock"].release(unlink=True)
            print("watch daemon: exiting", flush=True)

    try:
        daemon_pid = spawn_background(pidfile_path, log_path, setup, run_loop)
    except RuntimeError as exc:
        _die(f"error: {exc}")
    typer.echo(f"watch daemon started: pid={daemon_pid} pidfile={pidfile_path} "
               f"log={log_path}")


@app.command(name="skeleton")
@app.command(name="map", hidden=True)
def skeleton(
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    path_prefix: str = typer.Argument(
        None, help="Restrict to files under this project-relative path."),
    tree_mode: bool = typer.Option(
        False, "--tree", help="Directory-tree projection (no symbols listed)."),
    no_signatures: bool = typer.Option(
        False, "--no-signatures", help="Omit signature column (densest output)."),
    limit: int | None = typer.Option(
        None, help="Cap symbol lines per file (ignored in --tree mode)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Whole-project or per-subtree structural map from the manifest only
    (design §2.1): files with their symbol lines, one symbol per line,
    schema-v3 signature when stored."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project, hint=path_prefix)
    # Normalize the prefix: `.` or an absolute path under the project root
    # becomes project-relative; anything else stays as a literal prefix.
    if path_prefix and entry:
        apath = os.path.abspath(os.path.expanduser(path_prefix))
        root = entry.path.rstrip("/")
        if apath == root:
            path_prefix = None
        elif apath.startswith(root + "/"):
            path_prefix = apath[len(root) + 1:]
    data = core.skeleton(entry, prefix=path_prefix, tree_mode=tree_mode,
                         limit=limit,
                         include_signatures=not no_signatures)
    text = core.format_skeleton(data, "json" if json_output else "text")
    if core.schema_migrated(entry):
        text += "\n" + MIGRATION_HINT
    typer.echo(text)


@app.command(name="outline")
@app.command(name="file-outline", hidden=True)
def outline(
    file: str = typer.Argument(
        ..., help="File path relative to the project root."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    docstrings: bool = typer.Option(
        False, "--docstrings",
        help="Add one docstring line under each declaration (bounded read)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """One file: declarations, signatures, one-line docstrings (design §2.2)."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    try:
        data = core.outline(entry, file, include_docstrings=docstrings)
    except ValueError as exc:
        _die(str(exc))
    text = core.format_outline(data, "json" if json_output else "text")
    if core.schema_migrated(entry):
        text += "\n" + MIGRATION_HINT
    typer.echo(text)


@app.command(name="find-symbol")
def find_symbol(
    name: str = typer.Argument(
        None, help="Symbol name (optional: omit for browse mode with filters)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    symbol_type: str = typer.Option(
        None, "--type", "--symbol-type",
        help="function|method|class|struct|enum|namespace."),
    file_filter: str = typer.Option(
        None, "--file", help="Restrict to this file (project-relative)."),
    substring: bool = typer.Option(
        False, "--substring", help="Substring match (browse mode)."),
    limit: int = typer.Option(25, help="Max rows (browse-mode cap)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Look up symbols by name in the manifest symbol index (no semantic
    search). Exact AST-first, capped substring fallback. With NAME omitted,
    browse mode: --type/--file filters only, capped at --limit (design §2.3).
    Output gains the schema-v3 signature when stored."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    core.maybe_refresh(entry.slug, entry.path)
    m = core.manifest_for(entry.slug)
    try:
        if name:
            # Unchanged semantics: exact-first, capped substring fallback.
            rows = m.find_symbols(name, symbol_type=symbol_type, substring=False)
            match_label = False
            if not rows:
                rows = m.find_symbols(
                    name, symbol_type=symbol_type, substring=True, limit=limit)
                match_label = True
        else:
            # Browse mode (design §2.3): no name clause, filters only.
            rows = m.find_symbols(
                None, symbol_type=symbol_type, file=file_filter, limit=limit)
            match_label = False
        if not rows:
            msg = (f"no symbols matching {name!r}" if name
                   else "no symbols matching the given filters")
            if core.schema_migrated(entry):
                msg += "\n" + MIGRATION_HINT
            typer.echo(msg)
            return
        if json_output:
            typer.echo(json.dumps([
                {"file": r.file, "name": r.name, "type": r.symbol_type,
                 "start_line": r.start_line, "end_line": r.end_line,
                 "signature": r.signature}
                for r in rows], ensure_ascii=False, indent=2))
            return
        for r in rows:
            label = ""
            if match_label:
                label = ", match=exact" if r.name == name else ", match=substring"
            conf = "exact" if r.source == "ast" else "heuristic"
            sig = f"  {r.signature}" if r.signature else ""
            typer.echo(f"- {entry.path}::{r.file}:{r.start_line}-{r.end_line} "
                       f"{r.name} ({r.symbol_type}, confidence={conf}{label})"
                       f"{sig}")
    finally:
        m.close()


@app.command(name="find-definition")
def find_definition(
    name: str = typer.Argument(..., help="Symbol name (exact match only)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
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
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
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
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
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


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Verbose diagnostics (INFO) on stderr."),
) -> None:
    """Global options for the code-indexer CLI."""
    from .logsetup import configure_logging

    configure_logging(verbose=verbose)


def main() -> None:
    """Console-script entry point (``code-indexer``)."""
    app()


if __name__ == "__main__":
    main()
