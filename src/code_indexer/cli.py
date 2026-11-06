"""code-indexer CLI: thin typer app over code_indexer.core (design ruling §3).

Every subcommand maps 1:1 to a core op / MCP tool of the same name. One-shot
process: no daemon, no server RPC, no 'start server on first call'. Reads
never refresh: an answer describes the index as of ``meta.last_indexed`` and
reports ``fresh``, ``stale`` or ``unknown``. ``--skip-stale-check`` is
accepted and inert; ``--refresh`` is the only flag that makes a read write,
and it takes the per-project flock.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, NoReturn

import typer

logger = logging.getLogger("code-indexer.cli")

from .errors import fail
from .errors import ArgumentError, classify, IndexerError, RebuildRequiredError
from .errors import ExtensionUnavailableError
from .extensions import SEMANTIC_EXTENSION_MESSAGE, semantic_available
from .core import NEEDS_REINDEX, NEVER_INDEXED, UNVERIFIED, Core
from . import staleness
from . import vectors
from . import identity
from .doctor import run_doctor
from . import graph
from . import overview as overview_mod
from . import changed as changed_mod
from .evalharness import (EVAL_MODES, compare_reports, format_eval_report,
                          load_report, run_eval)
from .manifest import Manifest
from .registry import ProjectEntry

PROJECT_OPT = typer.Option(None, help="Project path, slug, or name.")

# Browse-mode row cap for `find-symbol` when no name is given (and for its
# capped substring fallback). Exact-name mode is uncapped unless --limit is
# passed: an exact name is an answer, not a browse (issue #22).
_BROWSE_CAP = 25

app = typer.Typer(
    name="code-indexer",
    help="Code intelligence for agents: index a repository into SQLite and "
         "answer structural, lexical and optional semantic queries (one-shot "
         "CLI; no daemon).",
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
    """Emit exactly one taxonomy line (output-contract work item).

    Message classification is mechanical: existing strings map onto the
    shared taxonomy (errors.py classify) so no call site changes content.
    """
    from .errors import fail
    exc = ValueError(msg)
    fail(exc)
    raise AssertionError("unreachable")  # pragma: no cover


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


def _check_vectors_flag(vectors: str) -> None:
    """Reject an unknown --vectors value as a usage error (exit 2)."""
    if vectors not in ("auto", "on", "off"):
        _usage_die(f"unknown --vectors value: {vectors} "
                   "(expected auto|on|off)")


def _require_semantic() -> None:
    """Fail with the one extension line before a command touches anything.

    A pass, a query or a watch needs the embedding and vector clients. When
    the extra is absent the command stops on one actionable line and exit 3
    instead of half-registering a project or printing a success line first
    (ADR-0001 section 1).
    """
    if not semantic_available():
        fail(ExtensionUnavailableError(SEMANTIC_EXTENSION_MESSAGE))


def _usage_die(msg: str) -> NoReturn:
    """Emit one ArgumentError line (usage/argument error, exit code 2)."""
    from .errors import ArgumentError, fail
    fail(ArgumentError(msg))
    raise AssertionError("unreachable")  # pragma: no cover


def _require_selector(core: Core, project: str | None, path: str | None,
                      command: str) -> str:
    """Return an explicit selector for a mutating command, or exit.

    Mutating commands never infer a project from the working directory: with
    neither a flag nor a positional selector they name what is registered and
    stop, so a destructive or scope-widening call can never widen to
    "whatever cwd happens to be in".
    """
    selector = project or path
    if selector:
        return selector
    entries = core.registry.list_projects()
    listing = ", ".join(e.path for e in entries) or "none"
    _usage_die(f"{command} needs an explicit project — pass a path, slug, or "
               f"name (positional or --project/--name); registered projects: {listing}")


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
    help="Accepted on every command and INERT: reads never index, so there "
         "is nothing to skip. Reporting staleness without repairing it is "
         "the default; use --refresh to repair.")
VectorsOpt = typer.Option(
    "auto", "--vectors",
    help="Vector side: auto (the project's recorded mode) | on (embed and "
         "upsert to Qdrant) | off (build a complete rows-only index with no "
         "service). A NEW project records off."
)
RefreshOpt = typer.Option(
    False, "--refresh",
    help="Run an incremental index pass first (one per project for "
         "--all-projects / doctor), then answer from the result. This is the "
         "ONLY flag that turns a read command into a write; a pass held by "
         "another process fails with LockedError (exit 5).")
FreshOpt = typer.Option(
    False, "--fresh",
    help="Read the live file and ignore cached symbol rows (this command "
         "only). Writes nothing.")


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Health-check the installation: runtime versions, Ollama (reachable /
    model present / dimension probe), Qdrant (reachable / version / client
    compatibility / collections vs registry / dims), INDEX_ROOT writability
    and free disk, SQLite integrity of registry + manifests, watch pidfile
    and lock liveness, and per-project fingerprint status. One line per
    check: ok|warn|fail <name>: <detail>. Exit 1 when any check fails
    (warnings do not fail). Dev/ops surface: deliberately CLI-only, no MCP
    tool — agents use index-status for per-project state. Network calls go
    only to the configured Ollama/Qdrant URLs.

    The per-project fingerprint verdict is reached from the manifests alone
    (the same comparison every read command makes), so it dials nothing; its
    embedding-dimension half is reported unverified because the manifest
    cannot answer it. With the semantic extra absent, the Ollama and Qdrant
    sections report that absence as unverified instead of dialling or
    crashing, so doctor still works on a core-only install.
    """
    core = _get_core(skip_stale_check)
    if refresh:
        # Doctor answers no index query, but its per-project fingerprint
        # checks inspect the index: --refresh refreshes what it inspects.
        for registered in core.registry.list_projects():
            core.refresh(registered)
    # The semantic extra is optional: with it absent, doctor still answers
    # every local check and reports each service section as unverified
    # instead of constructing a client library this install does not have.
    if semantic_available():
        embedder = core.embedder
        store = core.store
    else:
        embedder = store = None
    # Reuse the core embedder's own Ollama client when it carries one (keeps
    # doctor on the same configured URL and lets test stubs inject).
    client = getattr(embedder, "client", None)
    checks = run_doctor(core.cfg, core.registry, embedder, store,
                        ollama_client=client, semantic=semantic_available())
    if json_output:
        typer.echo(json.dumps(
            {"checks": [c.to_dict() for c in checks],
             "healthy": not any(c.status == "fail" for c in checks)},
            ensure_ascii=False, indent=2))
    else:
        for c in checks:
            typer.echo(c.line())
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)


@app.command()
def overview(
    project: str = PROJECT_OPT,
    path_prefix: str = typer.Option(None, "--path-prefix",
        help="Restrict to paths starting with this prefix."),
    max_lines: int = typer.Option(60, "--max-lines", help="Output line cap."),
    json_output: bool = typer.Option(False, "--json"),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Project-level map from manifest data: languages, directories, entry
    points, tests, config, hotspots (orientation surface).

    Answers from the index as of meta.last_indexed and reports whether that
    index is fresh, stale or unknown; --refresh is the only flag that makes
    this read write.

    CLI-only by design: this is a rendered orientation text surface; MCP
    agents already have skeleton/outline/search and the graph tools, so an
    MCP wrapper adds surface area without new capability.
    """
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    verdict_block, _ = _read_staleness(core, entry, refresh=False,
                                       json_output=json_output)
    m = core.read_manifest(entry.slug)
    try:
        data = overview_mod.build_overview(m, entry.path,
                                           path_prefix=path_prefix or "")
    finally:
        m.close()
    if json_output:
        data.update(verdict_block)
        typer.echo(json.dumps(data))
    else:
        typer.echo(overview_mod.format_overview(data, max_lines=max_lines))


@app.command()
def changed_symbols(
    project: str = PROJECT_OPT,
    base: str = typer.Option("HEAD", "--base", help="Diff base ref."),
    head: str = typer.Option(None, "--head",
                             help="Diff head ref (default: working tree)."),
    staged: bool = typer.Option(False, "--staged",
                                help="Diff staged changes only."),
    include_untracked: bool = typer.Option(
        False, "--include-untracked", help="Also report untracked files."),
    impact: bool = typer.Option(False, "--impact",
        help="Attach callers + candidate tests for changed symbols."),
    depth: int = typer.Option(1, "--depth", help="Impact traversal depth."),
    max_nodes: int = typer.Option(40, "--max-nodes", help="Impact node cap."),
    json_output: bool = typer.Option(False, "--json"),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """What changed in symbols: parse NEW/OLD contents from the git diff
    (never trusts the possibly-stale manifest). Post-edit read-only loop:
    run this, then search/get-code-context only for what actually changed."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    verdict_block, _ = _read_staleness(core, entry, refresh=False,
                                       json_output=json_output)
    try:
        changed = changed_mod.changed_symbols(
            entry.path, base=base, head=head, staged=staged,
            include_untracked=include_untracked)
    except changed_mod.NotAGitRepo as exc:
        _die(f"error: {exc}")
        return
    if impact:
        impact_data = changed_mod.impact(core, entry.slug, changed,
                                         depth=depth, max_nodes=max_nodes)
    else:
        impact_data = None
    if json_output:
        payload = dict(changed, schema=1)
        if impact_data is not None:
            payload["impact"] = impact_data
        payload.update(verdict_block)
        typer.echo(json.dumps(payload))
        return
    typer.echo(changed_mod.render(changed, impact_data))


def _core_entry(project: str | None, skip_stale_check: bool = False):
    core = _get_core(skip_stale_check)
    return core, _resolve(core, project)


def _refuse_obsolete_rows(core: Core, entry: ProjectEntry, *,
                          refresh: bool = False) -> None:
    """Refuse a row-reading surface on a needs-reindex project (exit 4).

    Called before the staleness verdict is rendered, so a refusal is exactly
    one stderr line with empty stdout. ``--refresh`` keeps its meaning: the
    pass runs FIRST, and a successful pass makes the rows current, so the
    command then answers normally.

    Replaces the former stderr warning. A warning was the wrong shape once the
    extraction-version bump made every project needs-reindex: an answer built
    from obsolete rows reads as a fact about the code, not as a caveat.
    """
    if refresh:
        try:
            core.refresh(entry)
        except IndexerError as exc:
            fail(exc)
    try:
        core.assert_rows_acceptable(entry)
    except RebuildRequiredError as exc:
        fail(exc)


def _read_staleness(core: Core, entry: ProjectEntry, *, refresh: bool,
                    json_output: bool) -> tuple[dict[str, Any], str | None]:
    """Refresh when asked, then report the index's verdict for one project.

    Text mode prints at most ONE stderr line (the record's shape) and nothing
    on stdout. JSON mode adds the block to the answer instead, so a machine
    consumer branches on fields rather than parsing prose. A read without
    ``--refresh`` never writes.

    Returns ``(block, warning line or None)``. The line comes from the ONE
    verdict this call computed: a caller that answers in JSON and still wants
    the line on stderr must not walk the tree a second time to rebuild it.
    """
    if refresh:
        try:
            core.refresh(entry)
        except IndexerError as exc:
            fail(exc)
    verdict = core.staleness(entry)
    line = staleness.warning_line(verdict, entry.path)
    if line and not json_output:
        typer.echo(line, err=True)
    return staleness.block(verdict), line


def _read_staleness_many(core: Core, entries: list[ProjectEntry], *,
                         refresh: bool,
                         json_output: bool) -> tuple[dict[str, Any],
                                                     list[dict[str, Any]],
                                                     list[str]]:
    """The aggregate block, one per-project block, and the warning lines.

    The verdict is computed ONCE per project: the line a caller prints and
    the block it answers with are the same walk.
    """
    verdicts: dict[str, staleness.StalenessVerdict] = {}
    blocks: list[dict[str, Any]] = []
    lines: list[str] = []
    for entry in entries:
        if refresh:
            try:
                core.refresh(entry)
            except IndexerError as exc:
                fail(exc)
        verdict = core.staleness(entry)
        verdicts[entry.path] = verdict
        blocks.append(dict(staleness.block(verdict), project=entry.path))
        line = staleness.warning_line(verdict, entry.path)
        if line:
            lines.append(line)
            if not json_output:
                typer.echo(line, err=True)
    return staleness.aggregate(verdicts), blocks, lines


def _json_failure(exc: BaseException, json_output: bool) -> NoReturn:
    """Failure reporting for a command that has a JSON mode.

    With --json the object carries ``ErrorType`` (ADR-0003 section 7) and the
    taxonomy's exit code; without it the one-line ``ErrorType: message``
    lands on stderr. Never a traceback either way.
    """
    if not json_output:
        fail(exc)
    err = classify(exc)
    typer.echo(json.dumps({"schema": 1, "ErrorType": err.error_type,
                           "error": str(err)}, ensure_ascii=False))
    raise typer.Exit(err.exit_code)


@app.command()
def find_callers(
    name: str = typer.Argument(..., help="Symbol name to find callers of."),
    project: str = PROJECT_OPT,
    depth: int = typer.Option(2, "--depth", help="Traversal depth."),
    max_nodes: int = typer.Option(40, "--max-nodes", help="Node cap."),
    json_output: bool = typer.Option(False, "--json"),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Who references this symbol: fan-in BFS over the single reference store
    (tree-sitter `call`/`type`/`base_class`/`member` rows, confidence `ast`,
    for python/typescript/tsx/javascript/c/cpp/go/csharp, plus the textual
    `identifier` / `includes` rows, confidence `heuristic`, for every other
    language, where comments and strings DO match) and import-based edges.
    The graph is TEXTUAL AND UNBOUND by design: names are matched as text,
    there is no compiler resolution, so a heuristic row is a navigation aid,
    not a resolved call. NAME may be dotted (`Type.Member`): the qualifier
    resolves against the symbol rows and the fan-in is matched on the
    trailing member name; the subject is reported once on the `note:` lines,
    and a direct hit carries `receiver=<expr>` only when the store actually
    holds a receiver for that row (a member access records the receiver
    expression as written) — transitive nodes never carry one. When the name
    has no definition row (symbols cover
    method|class|function|enum|interface|struct; properties and fields are not
    extracted), the search seeds from the reference rows and says so on
    `note:` lines instead of printing a bare `no results`. Every CALL SITE is
    its own node (two calls to the same target inside one function are two
    hits, each with its own line and receiver), and an answer the depth or
    node cap left incomplete says so: the JSON payload carries `truncated`
    and `omitted` beside `roots`, and the tree gains a `note:` line naming
    the cap. For every reference TO a name use `find-references`."""
    core, entry = _core_entry(project, skip_stale_check)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    verdict_block, _ = _read_staleness(core, entry, refresh=False,
                                       json_output=json_output)
    result = graph.find_callers(core, entry.slug, name, depth=depth,
                                max_nodes=max_nodes)
    _emit_graph(result, json_output, verdict_block)


@app.command()
def find_callees(
    name: str = typer.Argument(..., help="Symbol name to find callees of."),
    project: str = PROJECT_OPT,
    depth: int = typer.Option(2, "--depth", help="Traversal depth."),
    max_nodes: int = typer.Option(40, "--max-nodes", help="Node cap."),
    json_output: bool = typer.Option(False, "--json"),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """What this symbol references: fan-out BFS over the `refs` table
    (same store and confidence rules as `find-callers`; the graph is textual
    and unbound by design). A callee is identified by its TARGET, not by the
    call site — this walk answers "what does this symbol reach", so one target
    reached twice is one callee. The opposite of `find-callers`, deliberately.
    An answer the depth or node cap left incomplete reports `truncated` /
    `omitted` in JSON and a `note:` line in the tree. For references TO a
    symbol across both stores use `find-references`.
    """
    core, entry = _core_entry(project, skip_stale_check)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    verdict_block, _ = _read_staleness(core, entry, refresh=False,
                                       json_output=json_output)
    result = graph.find_callees(core, entry.slug, name, depth=depth,
                                max_nodes=max_nodes)
    _emit_graph(result, json_output, verdict_block)


def _emit_graph(result: graph.GraphResult, json_output: bool,
                staleness_block: dict[str, Any] | None = None) -> None:
    """Render a graph result; `notes` explain an empty or near-empty answer so
    it is never a bare `no results` (an empty caller list must not read as
    "nothing calls this"). A result the caps left incomplete also carries a
    `note:` line naming the cap, printed WITH the answer (stdout) — an
    incomplete answer must say so where the answer is read. The explanatory
    notes of a complete, non-empty answer stay on stderr (result-only stdout).
    The stream is chosen once for the whole note block, from `truncated`: when
    an answer is incomplete every note follows the answer on stdout, including
    an explanatory note that is not itself about the cap.
    """
    if json_output:
        payload = graph.result_to_json(result)
        payload.update(staleness_block or {})
        typer.echo(json.dumps(payload))
        return
    typer.echo(graph.render_tree(result.nodes))
    if not result.nodes:
        # Nothing at all: keep the legacy shape but never bare — the notes
        # carry the explanation on stdout.
        for note in result.notes:
            typer.echo(f"note: {note}")
        return
    for note in result.notes:
        typer.echo(f"note: {note}", err=not result.truncated)


@app.command()
def deps(
    path: str = typer.Argument(..., help="Project-relative file path."),
    project: str = PROJECT_OPT,
    direction: str = typer.Option("both", "--direction",
                                  help="in | out | both"),
    depth: int = typer.Option(2, "--depth"),
    max_nodes: int = typer.Option(60, "--max-nodes"),
    graph_format: str = typer.Option("tree", "--format",
                                     help="tree | edges | json"),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Import graph around one file (resolved edges only). The payload keeps
    its per-level `in` / `out` dicts and gains `truncated` and `omitted`; a
    result the depth or node cap left incomplete also prints a `note:` line
    naming the cap."""
    core, entry = _core_entry(project, skip_stale_check)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    verdict_block, _ = _read_staleness(core, entry, refresh=False,
                                       json_output=graph_format == "json")
    result = graph.deps_for_file(core, entry.slug, path,
                                 direction=direction, depth=depth,
                                 max_nodes=max_nodes)
    if graph_format == "json":
        result.update(verdict_block)
        typer.echo(json.dumps(result))
        return
    lines: list[str] = []
    for dir_key in ("in", "out"):
        levels = result.get(dir_key) or {}
        if not levels:
            continue
        for lvl, files in sorted(levels.items()):
            for f in files:
                indent = "  " * (lvl - 1)
                lines.append(f"{dir_key} L{lvl}: {indent}{f}")
    typer.echo("\n".join(lines) if lines else "no resolved dependencies")
    # An incomplete answer says so, on the same stream as the answer.
    for note in result.get("notes") or []:
        typer.echo(f"note: {note}")


@app.command()
def add_project(
    path: str = typer.Argument(..., help="Absolute project directory to register."),
    name: str = typer.Option(None, help="Custom collection name (idx_<name>)."),
    allow_sensitive: bool = typer.Option(
        False, "--allow-sensitive",
        help="Index secret-bearing files too (.env*, keys, credentials*, and "
             "files whose content matches high-confidence secret patterns). "
             "Stored per project; the default skips them."),
    include: list[str] = typer.Option(
        None, "--include",
        help="Scoped indexing: only index paths matching these "
             "gitignore-style globs, e.g. 'src/**' (repeatable; stored in "
             "the manifest)."),
    priority: list[str] = typer.Option(
        None, "--priority",
        help="Globs indexed FIRST within the pass (repeatable)."),
    vectors: str = VectorsOpt,
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Register a project directory for semantic indexing (idempotent) and
    run the initial index pass.

    ``--vectors auto`` (the default) records ``off`` for a NEW project and
    runs a service-free pass; an existing project keeps its recorded mode.
    """
    _check_vectors_flag(vectors)
    # An explicit vectors-on registration needs the extension before anything
    # is registered: a half-registered project is not a good failure mode.
    if vectors == "on":
        _require_semantic()
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        _die(f"error: path does not exist: {path}")
    entry = core.registry.get_by_path(path)
    if entry is not None:
        typer.echo(f"already registered — {core.status_summary(entry)}")
        if not core.has_successful_pass(entry.slug):
            typer.echo(
                "note: no indexing pass has completed for this project — run "
                f"reindex-project {path} (or remove-project and add it again) "
                "to build the index")
        return
    try:
        entry = core.registry.add(path, name=name)
    except ValueError as exc:
        _die(f"error: {exc}")
    typer.echo(f"registered {entry.path} (slug {entry.slug})")
    # A NEW project records its mode before the first pass: ``auto`` records
    # off, so a fresh install never starts embedding because nobody said no.
    resolved_vectors = "off" if vectors == "auto" else vectors
    m = core.manifest_for(entry.slug, writable=True)
    try:
        m.set_meta("vectors", resolved_vectors)
    finally:
        m.close()
    if resolved_vectors == "on":
        _require_semantic()
    else:
        typer.echo("note: vectors=off — rows, symbols, references and chunks "
                   "are indexed with no service; run reindex-project "
                   "--vectors on later to embed them")
    if allow_sensitive:
        m = core.manifest_for(entry.slug, writable=True)
        try:
            m.set_meta("allow_sensitive", "1")
        finally:
            m.close()
        typer.echo("note: --allow-sensitive set — secret-bearing files will "
                   "be indexed for this project")
    if include or priority:
        m = core.manifest_for(entry.slug, writable=True)
        try:
            if include:
                m.set_meta("scope_include", "\n".join(include))
            if priority:
                m.set_meta("scope_priority", "\n".join(priority))
        finally:
            m.close()
        typer.echo(f"note: scoped indexing — include={', '.join(include or [])}"
                   + (f"; priority={', '.join(priority or [])}"
                      if priority else ""))
    _echo_index_result(entry.slug, core.run_index(entry.slug, entry.path,
                                                  vectors=vectors))


@app.command()
def index_more(
    first: str = typer.Argument(
        None, help="Registered project path (or the subpath when --project "
                   "is given)."),
    second: str = typer.Argument(
        None, help="Subtree to add to the scope (historical two-argument "
                   "form only)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name. Takes precedence over the "
             "positional selector when both are given."),
    vectors: str = VectorsOpt,
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Extend a project's indexed scope with another subtree.

    The new glob is appended to the manifest's scope_include and an
    incremental pass indexes the newly-covered files. ``--vectors`` follows
    the same auto|on|off rule as add-project; turning vectors on for a project
    kept off embeds its stored chunks as well.

    Two selector spellings are supported: ``index-more <project> <subpath>``
    (historical) and ``index-more --project <project> <subpath>``. Combining
    the flag with two positionals is ambiguous and rejected.
    """
    _check_vectors_flag(vectors)
    core = _get_core(skip_stale_check)
    if project:
        if second is not None:
            _usage_die("index-more got --project plus two positionals — use "
                 "--project <project> <subpath> or <project> <subpath>, not both")
        selector, subpath = project, first
    else:
        selector, subpath = first, second
    selector = _require_selector(core, selector, None, "index-more")
    if not subpath:
        _usage_die("index-more needs a subpath to add — "
             "index-more <project> <subpath> or "
             "index-more --project <project> <subpath>")
    entry = _resolve(core, selector)
    if core.resolve_vectors(entry.slug, vectors) == "on":
        _require_semantic()
    m = core.manifest_for(entry.slug, writable=True)
    try:
        current = [ln for ln in (m.get_meta("scope_include") or "")
                   .splitlines() if ln.strip()]
        if subpath not in current:
            current.append(subpath)
            m.set_meta("scope_include", "\n".join(current))
    finally:
        m.close()
    typer.echo(f"scope extended: {', '.join(current)}")
    _echo_index_result(entry.slug, core.run_index(entry.slug, entry.path,
                                                  vectors=vectors,
                                                  rescan=True))


@app.command()
def remove_project(
    path: str = typer.Argument(
        None, help="Registered project path (optional; --project wins when "
                   "both are given)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name. Takes precedence over the "
             "positional PATH when both are given."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Deregister a project and DELETE its collection + manifest + registry
    entry entirely."""
    import glob as _glob
    import shutil as _shutil
    core = _get_core(skip_stale_check)
    selector = _require_selector(core, project, path, "remove-project")
    entry = _resolve(core, selector)
    path = entry.path
    entry = core.registry.remove(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    # The slug is served under the ``idx_<slug>`` alias; the physical
    # collections behind it carry the embedding identity, and a collection left
    # from an earlier identity belongs to the same project. Claim all of them,
    # and a pre-identity plain-slug collection too. The alias goes first, so it
    # can never outlive the collection it points at.
    alias = vectors.alias_for(entry.slug)
    targets: list[str] = []
    physical = core.store.physical_name(alias)
    if physical is not None:
        targets.append(physical)
    for name in sorted(core.store.collection_names()):
        if (name == alias or name.startswith(f"{alias}__")) \
                and name not in targets:
            targets.append(name)
    alias_dropped = core.store.drop_alias(alias)
    for name in targets:
        core.store.drop_collection(name)
    if targets:
        noun = "collection" if len(targets) == 1 else "collections"
        what = f"{noun} {', '.join(targets)} dropped"
    elif alias_dropped:
        what = f"alias {alias} dropped (no physical collection)"
    else:
        what = "no vector collection to drop"
    typer.echo(f"removed {path}: {what}, registry entry deleted")
    for f in _glob.glob(os.path.join(core.cfg.index_root, f"{entry.slug}*")):
        try:
            os.remove(f)
        except OSError:
            pass
    manifest_dir = os.path.join(core.cfg.index_root, entry.slug)
    if os.path.isdir(manifest_dir):
        _shutil.rmtree(manifest_dir, ignore_errors=True)


@app.command(name="list-projects")
def list_projects(json_output: bool = typer.Option(False, "--json",
                                                   help="JSON output."),
                  skip_stale_check: bool = SkipOpt,
                  refresh: bool = RefreshOpt) -> None:
    """List registered projects with per-project index summary.

    A read: it answers from the registry and the manifests as of their last
    passes and reports a staleness verdict per project. No service is
    contacted — not even a collection listing: whether a registered
    project's collection still exists is a service verdict the manifest does
    not answer, and ``doctor``'s ``qdrant-missing`` check owns it.

    With --json the answer is one object: `projects` holds the fields the
    text lines print plus each project's staleness block, and the top-level
    staleness fields aggregate them (a fan-out answer must not hide a stale
    project).
    """
    core = _get_core(skip_stale_check)
    entries = core.registry.list_projects()
    if not entries:
        if json_output:
            typer.echo(json.dumps({"schema": 1, "projects": [],
                                   **staleness.block(staleness.unknown(
                                       "no projects registered"))}))
        else:
            typer.echo("no projects registered")
        return
    aggregate, blocks, _ = _read_staleness_many(
        core, entries, refresh=refresh, json_output=json_output)
    if json_output:
        projects = [dict(core.status_fields(e), **b)
                    for e, b in zip(entries, blocks)]
        typer.echo(json.dumps({"schema": 1, "projects": projects, **aggregate},
                              ensure_ascii=False))
        return
    for e in entries:
        typer.echo(f"- {core.status_summary(e)}")


@app.command(name="lookup-project")
def lookup_project(
    path: str = typer.Argument(
        None, help="Registered project path (optional; --project wins when "
                   "both are given)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name. Takes precedence over the "
             "positional PATH when both are given."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Check whether a project is registered (no indexing triggered).

    A check, not an error path: an unregistered selector prints
    ``not registered: <path>`` and exits 0. With no selector at all the
    read-only cwd resolution used by ``index-status`` applies. The JSON
    answer carries the fields the text line prints, the collection name, and
    the staleness block (an unregistered project's verdict is ``unknown``).
    """
    core = _get_core(skip_stale_check)
    selector = project or path
    entry, _err = core.resolve_entry(selector)
    if entry is None:
        raw = os.path.abspath(os.path.expanduser(selector or os.getcwd()))
        if json_output:
            typer.echo(json.dumps({
                "schema": 1, "registered": False, "path": raw,
                **staleness.block(staleness.unknown("project not registered")),
            }, ensure_ascii=False))
            return
        typer.echo(f"not registered: {raw}")
        return
    block, _ = _read_staleness(core, entry, refresh=refresh,
                               json_output=json_output)
    if json_output:
        payload = dict(core.status_fields(entry), schema=1, registered=True,
                       collection=f"idx_{entry.slug}", **block)
        typer.echo(json.dumps(payload, ensure_ascii=False))
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
    per_file: int = typer.Option(0, "--per-file",
        help="Max hits per file (0 = no generic per-file cap; the pure-data "
             "share cap always applies)."),
    max_chars: int = typer.Option(None, "--max-chars",
        help="Output budget in characters: lowest-ranked hits are trimmed."),
    max_tokens: int = typer.Option(None, "--max-tokens",
        help="Output budget in approximate tokens (chars/4)."),
    context_lines: int = typer.Option(
        0, "--context-lines",
        help="Lines of snippet shown per hit in text mode (0 = default 200-char "
             "snippets; compact mode is unaffected)."),
    output_format: str = typer.Option(
        "text", "--format",
        help="Output format: text (verbose, default) | compact (one line per "
             "hit: path:start-end  symbol  score) | json."),
    rerank: str = typer.Option(
        None, "--rerank",
        help="Rerank mode: heuristic (token-overlap boosts, path penalties, "
             "repeat-file diversification) | none. Default none — the "
             "heuristic reranker is opt-in pending an eval win."),
    no_rerank: bool = typer.Option(
        False, "--no-rerank", help="Explicitly disable reranking."),
    search_mode: str = typer.Option(
        "hybrid", "--mode",
        help="Retrieval mode: hybrid (dense + lexical RRF fusion, default — "
             "eval win: recall@1 +0.17, MRR +0.12, 7 improved / 0 regressed) | "
             "dense (embeddings only) | lexical (FTS5 bm25, no Ollama call)."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
    all_projects: bool = typer.Option(
        False, "--all-projects",
        help="Search every registered project. The default resolves ONE "
             "project (the containing project inferred from the invocation "
             "path); fan-out is bounded by the 30 s wall."),
) -> None:
    """Semantic code search over the index as of meta.last_indexed.

    A read: it never indexes. The default resolves ONE project — the
    containing project inferred from the invocation path — and fan-out
    requires --all-projects. The query makes ONE embedding attempt with a
    short timeout (no retry chain); the fan-out stops at a 30 s wall and
    reports the projects it did not reach as skipped with reason=budget.

    --refresh runs one incremental pass first and answers from the result
    (the only flag that makes this read write; a held flock is LockedError,
    exit 5); --fresh and --skip-stale-check are accepted and inert. The
    answer carries the staleness block, and text mode prints the record's
    one stderr line when the index is stale or the check did not run.

    --ranking metadata applies small metadata
    score adjustments (definition boost, test-path penalty); --ranking
    hybrid fuses the vector score with a lexical token-overlap score
    (weighted sum, fused = vector_score + 0.25 * lexical). In every mode
    pure-data chunks (.json/.yaml/.toml) are down-weighted and capped to at
    most 40% of the top-k window, so data files cannot crowd code out of it
    (waived when the query names a data format). Same-file overlapping/adjacent
    hits are merged into their union range; --per-file diversifies across
    files; --max-chars/--max-tokens trim the lowest-ranked hits (one trailing
    dropped-count line in text/compact output). A --all-projects answer fuses
    per-collection lists with Reciprocal Rank Fusion instead of comparing raw
    cross-collection cosine."""
    _require_semantic()
    core = _get_core(skip_stale_check)
    if all_projects and project:
        _usage_die("--all-projects and --project are mutually exclusive; "
                   "omit --project to fan out, or pass one project")
    fmt = "json" if json_output else output_format
    if all_projects:
        entries = core.registry.list_projects()
        if not entries:
            _usage_die("no projects registered")
        entry = None
        aggregate, blocks, stale_lines = _read_staleness_many(
            core, entries, refresh=refresh, json_output=True)
        verdict_block: dict[str, Any] = dict(aggregate, staleness=blocks)
    else:
        if project:
            entry, err = core.resolve_entry(project)
            if entry is None:
                _die(err)
        else:
            entry, err = core.resolve_entry(None)
            if entry is None:
                _usage_die(err)
        verdict_block, stale_line = _read_staleness(core, entry, refresh=refresh,
                                                    json_output=True)
        stale_lines = [stale_line] if stale_line else []
    rerank_mode = "none" if no_rerank else rerank
    try:
        output = core.search_for_display(
            query, project=(entry.path if entry else None), limit=limit,
            file_filter=file_filter, symbol_type=symbol_type,
            language=language, ranking_mode=ranking, fmt=fmt,
            skip_refresh=True, per_file=per_file, max_chars=max_chars,
            max_tokens=max_tokens, context_lines=context_lines,
            rerank=rerank_mode, mode=search_mode,
            all_projects=all_projects, staleness_block=verdict_block)
    except Exception as exc:  # noqa: BLE001 — the shared failure seam
        # Search is the one surface that REFUSES a fingerprint-mismatched
        # index (the graph surfaces warn and answer) — that refusal must
        # render exactly like every other failure in this CLI: one
        # taxonomy line (`<ErrorType>: <message>`), the taxonomy's exit
        # code, and no rich traceback. The traceback stays available
        # under --verbose. A total backend failure exits 3 with stdout
        # empty, so it is never mistaken for an empty result.
        logger.info("semantic-search failed", exc_info=True)
        fail(exc)
    typer.echo(output)
    if fmt != "json":
        for line in stale_lines:
            if line:
                typer.echo(line, err=True)
    # Scoped indexing note: when the index covers only part of the
    # project, say so instead of letting out-of-scope paths look empty.
    if entry is not None:
        m = core.read_manifest(entry.slug)
        try:
            scope = [ln for ln in (m.get_meta("scope_include") or "")
                     .splitlines() if ln.strip()]
        finally:
            m.close()
        if scope:
            typer.echo(f"note: this index is scoped to {', '.join(scope)} — "
                       "paths outside it are not indexed (index-more extends "
                       "the scope)", err=True)


@app.command(name="index-status")
def index_status(
    path: str = typer.Argument(
        None, help="Registered project path (optional; --project wins when "
                   "both are given)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name. Takes precedence over the "
             "positional PATH when both are given."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Report indexing state (idle | indexing | never-indexed | error). By
    default this is a read: it answers from the manifest and does NOT trigger
    a re-index. --refresh runs one incremental pass first (the only flag that
    makes this read write; a held flock is LockedError, exit 5). A
    --json answer is one object carrying the fields the text line prints,
    plus the staleness block.
    registered project with no completed indexing pass reports
    state=never-indexed rather than a misleading idle.

    The state above is the in-process PASS state. Index freshness has a
    SECOND vocabulary: the fingerprint state, which says whether the index
    was built by the current configuration. When it was not, a
    `fingerprint=needs-reindex reason=...` field is printed alongside the
    pass state — the same verdict `list-projects`/`doctor` give — so a
    project that owes a rebuild never reads as plainly idle here, and the
    two surfaces can never contradict each other.

    That verdict is reached without dialling a service: the keys this process
    can state from configuration alone are compared with the recorded ones.
    The recorded embedding dimension has no configured counterpart to compare
    against without a probe, so a project whose other keys all match reports
    `fingerprint=unverified reason=embedding-dimension-not-compared`. "Cannot
    verify" is its own value here — never folded into the pass state and never
    silently omitted, so a missing `fingerprint=` field means the manifest is
    legacy and backfills on the next pass.
    """
    core = _get_core(skip_stale_check)
    # Both spellings reach the same resolver: a path, a slug, or a custom name.
    # The --project/--name flag wins when both are given; the positional PATH
    # is the historical form the MCP server still uses.
    entry = _resolve(core, project or path)
    if refresh:
        core.refresh(entry)
    st = core.state_for(entry.slug)
    state = core.effective_state(entry.slug, st.get("state", "idle"))
    parts = [f"project={entry.path}", f"state={state}"]
    fields: dict[str, Any] = {"project": entry.path, "state": state}
    # The pass state says nothing about whether the index still matches the
    # current configuration. Query paths refuse on a mismatch (ConfigError)
    # and list-projects/doctor already call it needs-reindex; index-status
    # must give the same verdict rather than a plain idle.
    fp = core.fingerprint_status(entry)
    vs = core.vectors_status(entry)
    parts.append(f"rows={vs['rows']}")
    parts.append(f"vectors={vs['vectors']}")
    parts.append(f"vectors_state={vs['vectors_state']}")
    fields["rows"] = vs["rows"]
    fields["vectors"] = vs["vectors"]
    fields["vectors_state"] = vs["vectors_state"]
    # The manifest's RECORDED schema revision. A read never migrates the file,
    # so this is what the CALLER needs to know: a value below this build's
    # revision means the rows will be refused until a pass runs. The JSON
    # envelope's own "schema" field is the payload version, so this one is
    # spelled ``manifest_schema`` there.
    recorded_schema = core.recorded_schema_version(entry.slug)
    parts.append("schema=" + ("none" if recorded_schema is None
                              else str(recorded_schema)))
    fields["manifest_schema"] = recorded_schema
    if fp["state"] == NEEDS_REINDEX:
        parts.append(f"fingerprint=needs-reindex reason={fp['reason']}")
        fields["fingerprint"] = "needs-reindex"
        fields["reason"] = fp["reason"]
    elif fp["state"] == UNVERIFIED:
        # Verdict unavailable (the embedder could not be probed). Say which
        # it is rather than omitting the field: a bare pass state would read
        # exactly like a verified match.
        parts.append(f"fingerprint=unverified reason={fp['reason']}")
        fields["fingerprint"] = "unverified"
        fields["reason"] = fp["reason"]
    if st.get("last_result"):
        parts.append(f"last_pass={st['last_result']}")
        fields["last_pass"] = st["last_result"]
    if st.get("progress"):
        p = st["progress"]
        parts.append(
            f"progress=files {p['files_done']}/{p['files_total']} "
            f"chunks {p['chunks_done']}/{p['chunks_total']} "
            f"{p['docs_per_s']} chunks/s eta={p['eta_s']}s")
        fields["progress"] = p
    if st.get("last_result") and st["last_result"].get("sensitive_skipped"):
        parts.append(
            f"sensitive={st['last_result']['sensitive_skipped']} files skipped "
            "(privacy; override with --allow-sensitive on add-project)")
        fields["sensitive_skipped"] = st["last_result"]["sensitive_skipped"]
    if st.get("error"):
        parts.append(f"error={st['error']}")
        fields["error"] = st["error"]
    # Languages mapped to a grammar but without AST references queries
    # (heuristic/textual refs only) — orientation honesty.
    from .langmatrix import languages_without_ast
    languages_without_ast_rows = languages_without_ast()
    parts.append("languages_without_ast=[" + ", ".join(
        languages_without_ast_rows) + "]")
    fields["languages_without_ast"] = languages_without_ast_rows
    # Scoped-indexing coverage: per top-level dir, indexed or not.
    try:
        m = Manifest(os.path.join(core.cfg.index_root, entry.slug,
                                  "manifest.db"), writable=False)
        try:
            scope = [ln for ln in (m.get_meta("scope_include") or "")
                     .splitlines() if ln.strip()]
            if scope:
                indexed_dirs = {p.split("/", 1)[0]
                                for p in m.all_files() if "/" in p}
                import pathspec as _ps
                spec = _ps.GitIgnoreSpec.from_lines(scope)
                from .scanner import _matches_include
                covered = {d for d in indexed_dirs
                           if _matches_include(spec, d)}
                on_disk = {f.name for f in os.scandir(entry.path)
                           if f.is_dir() and not f.name.startswith(".")}
                not_indexed = sorted(on_disk - covered)
                parts.append(f"scope={', '.join(scope)}")
                fields["scope"] = scope
                if not_indexed:
                    parts.append("not indexed: " + ", ".join(not_indexed[:6]))
                    fields["not_indexed"] = not_indexed
        finally:
            m.close()
    except Exception as exc:  # noqa: BLE001 — status must never fail
        logger.debug("scope coverage skipped: %s", exc)
    # Last 3 recorded embed errors (poisoned-chunk reports).
    mdir = os.path.join(core.cfg.index_root, entry.slug, "manifest.db")
    if os.path.isfile(mdir):
        m = Manifest(mdir, writable=False)
        try:
            errs = m.last_index_errors(3)
            if errs:
                parts.append("last_errors=" + " | ".join(
                    f"{e['file']}#{e['chunk_index']}: {e['error'][:60]}"
                    for e in errs))
                fields["last_errors"] = errs
            # Per-file parse verdicts (ADR-0004 section 7): the parser's own
            # result is counted here, never the chunker's fallback. All three
            # keys are always present, so a zero never reads as "not counted".
            verdicts = m.parse_verdict_counts()
            parts.append("parse=" + ",".join(
                f"{kind}:{n}" for kind, n in verdicts.items()))
            fields.update(verdicts)
        except Exception as exc:  # noqa: BLE001 — status must never fail
            logger.debug("recorded errors skipped: %s", exc)
        finally:
            m.close()
    if state == NEVER_INDEXED:
        parts.append("note=registered but no indexing pass has completed; "
                     "run reindex-project to build the index")
        fields["note"] = ("registered but no indexing pass has completed; "
                          "run reindex-project to build the index")
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    if json_output:
        fields.update(block)
        fields["schema"] = 1
        typer.echo(json.dumps(fields, ensure_ascii=False))
        return
    typer.echo(" ".join(parts))


@app.command(name="sweep-orphans")
def sweep_orphans(
    path: str = typer.Argument(..., help="Registered project path."),
    apply: bool = typer.Option(
        False, "--apply",
        help="Actually delete the orphan rows. Default is a dry run that "
             "prints what it would clear and changes nothing."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Remove residue rows for files that are gone from the project tree.

    A row in `refs`, `imports` or `symbols` whose `file` has NO `files` row
    and whose file is absent from the working tree is residue from a deletion
    that did not clean up after itself (the delete path historically skipped
    `imports`). This sweep reports those rows and — only with `--apply` —
    removes them in one transaction, reporting the count removed.

    Dry run by default: it prints the per-table counts and the file list it
    would clear and does nothing else. The tree check is a guard ("no files
    row" and "the file is gone" are different predicates); the real safety
    property is the dry run plus the reported count. Reads the manifest
    directly and never triggers an index pass. CLI-only index-maintenance
    surface (no MCP tool)."""
    core = _get_core(skip_stale_check)
    path = os.path.abspath(os.path.expanduser(path))
    entry = core.registry.get_by_path(path)
    if entry is None:
        _die(f"error: not registered: {path}")
    block: dict[str, Any] = {}
    if not apply:
        block, _ = _read_staleness(core, entry, refresh=refresh,
                                   json_output=json_output)
    result = core.sweep_orphans(entry, entry.path, apply=apply)
    if json_output:
        payload = {"project": entry.path, **result}
        if block:
            payload["schema"] = 1
            payload.update(block)
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    counts = result["per_table"]
    counts_line = " ".join(
        f"{table}={counts[table]}" for table in ("refs", "imports", "symbols"))
    if apply:
        typer.echo(f"project={entry.path} applied files={len(result['files'])} "
                   f"removed={result['removed']} {counts_line}")
    else:
        typer.echo(f"project={entry.path} dry-run files={len(result['files'])} "
                   f"total={result['total']} {counts_line}")
    for f in result["files"]:
        typer.echo(f"- {f}")
    if result["present_on_disk_kept"]:
        typer.echo(
            f"note: {len(result['present_on_disk_kept'])} path(s) with rows but "
            "no files row are still present on disk and were left alone: "
            + ", ".join(result["present_on_disk_kept"]), err=True)
    if not apply and result["files"]:
        typer.echo("note: dry run — nothing was deleted; pass --apply to "
                   "remove these rows", err=True)


@app.command(name="reindex-project")
def reindex_project(
    path: str = typer.Argument(
        None, help="Registered project path (optional; --project wins when "
                   "both are given)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name. Takes precedence over the "
             "positional PATH when both are given."),
    vectors: str = VectorsOpt,
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Force a full rebuild of a project's index (runs foreground in the CLI).

    A vectors-on rebuild whose rows are already current embeds from the
    stored chunks and swaps in a collection for the current identity — it
    reparses nothing. ``--vectors off`` rebuilds the rows with no service and
    leaves any existing collection untouched.
    """
    _check_vectors_flag(vectors)
    core = _get_core(skip_stale_check)
    selector = _require_selector(core, project, path, "reindex-project")
    entry = _resolve(core, selector)
    if core.resolve_vectors(entry.slug, vectors) == "on":
        _require_semantic()
    _echo_index_result(entry.slug, core.reindex_with_swap(
        entry.slug, entry.path, vectors=vectors))


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

    Linux inotify via the watchdog Observer library (a main dependency): the
    project roots are watched recursively and file
    events schedule an incremental pass after a quiet period of
    WATCH_DEBOUNCE seconds (alias WATCH_QUIET_PERIOD, default 3 — the old
    poll tick is now the debounce). A burst of events coalesces into at
    most one pass per quiet period; an event burst that changes nothing
    costs a hash scan only — zero embedding, zero Qdrant traffic.

    Fallbacks/self-heal:

    - A full staleness pass for every watched project runs every
      WATCH_SWEEP_INTERVAL seconds (default 300) even with zero events, so
      missed/lost events self-heal.
    - A registered project whose root is missing or unreadable is skipped
      with a single warning naming it; the remaining projects are still
      served, and the watcher reports the projects it actually covers. It
      exits nonzero only when it covers none.
    - If watchdog is not installed or inotify watch descriptors are
      exhausted (OSError installing the recursive watches — raised from
      either the schedule call or the observer's start), the watcher
      degrades to quiet-period polling (one hash scan per project per
      quiet tick); it never exits for that reason, losing latency only.

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
    startup handshake (the daemon holds the pidfile flock and has installed
    the event source by then). The parent then re-confirms the daemon is
    alive and still holding the pidfile before printing "watch daemon
    started" — a daemon that died on entry is reported as a failure with a
    nonzero exit, never as a start. A stopped watcher leaves no live lock or
    PID residue (the pidfile is unlinked only after the flock is released).
    --foreground is the default (inherited stdio, normal output) and takes
    the SAME pidfile, so one watcher per index root holds in both modes
    and `unwatch` can stop either.

    Stop it with `code-indexer unwatch [PATH...|--all]`.

    Version bumps (EXTRACTION_VERSION, CHUNKER_VERSION, EMBED_FORMAT): a
    manifest's recorded fingerprint describes the code that built it, and a
    watcher keeps re-stamping its OWN fingerprint on every sweep — so a
    watcher left running across a version bump silently reverts the
    needs-reindex flag and can re-embed with its older extraction code.
    After upgrading the code, stop every watcher first, rebuild, then start
    the watchers again on the new code:
    `code-indexer unwatch --all` -> `code-indexer reindex-project <path>` ->
    `code-indexer watch --all --background`. `doctor` names the running
    watcher when a fingerprint mismatch is present.

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
    # A missing or unreadable project root must never be scheduled: the
    # inotify add-watch for it aborts the whole watcher. Drop it with ONE
    # warning naming it and keep serving the rest; exit nonzero only when
    # nothing remains to watch.
    targets = _select_watchable(targets)
    pidfile_path = os.path.join(core.cfg.index_root, "watch.pid")
    log_path = os.path.join(core.cfg.index_root, "watch.log")

    if background:
        _watch_background(core, targets, duration, pidfile_path, log_path)
        return  # parent path returns; daemon path exits inside spawn

    # ----- foreground (default) -----------------------------------------
    # Foreground takes the same pidfile as --background: it is the
    # single-instance gate in both modes (the flock, not the pid, is the
    # liveness test) and the handle `unwatch` signals.
    from .watcher import PidFileLock, read_pid

    lock = PidFileLock(pidfile_path)
    if not lock.acquire():
        typer.echo(f"watch: already running (pid={read_pid(pidfile_path)}, "
                   f"pidfile={pidfile_path})", err=True)
        raise typer.Exit(3)

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
    try:
        engine = _make_engine(
            core, targets, quiet=quiet, sweep_interval=sweep_interval)
        mode = "inotify" if engine.start_events() else "poll"
        typer.echo(
            f"watching {len(targets)} project(s) "
            f"({', '.join(slug for slug, _ in targets)}), mode={mode}, "
            f"quiet={quiet}s, sweep={sweep_interval}s, "
            f"duration={'forever' if deadline is None else f'{duration}s'}")
        engine.run(duration, stop)
    finally:
        typer.echo("watch: exiting")
        lock.release(unlink=True)
    raise typer.Exit(0)


@app.command()
def unwatch(
    paths: list[str] = typer.Argument(
        None, help="Project paths (or slugs/names) the watcher covers. Repeatable."),
    all_projects: bool = typer.Option(
        False, "--all", help="Stop the watcher covering every registered project."),
    timeout: float = typer.Option(
        10.0, "--timeout",
        help="Seconds to wait for the watcher to release the pidfile flock."),
    skip_stale_check: bool = SkipOpt,
) -> None:
    """Stop the watcher started by `watch` — the reverse of `watch`.

    SIGTERMs the live watcher holding this index root's pidfile
    (<index_root>/watch.pid) and waits for it to release the flock. The
    flock — never pid existence — is the liveness test, so a stale pidfile
    left by a killed watcher is cleaned up silently. Works for both a
    `--background` daemon and a foreground watcher (they share the pidfile).

    Arguments mirror `watch`: repeatable project paths (or slugs/names), or
    `--all`; never both, and never neither. The holder's watched projects
    are reported, with a note when they do not cover everything requested.

    No watcher running is a clean no-op: a one-line message and exit 0.
    Exit 1 when a live holder could not be stopped (`--timeout` elapsed, or
    its pid is unreadable), or when a pidfile that exists could not be probed
    (liveness unknown — absence is not claimed).
    """
    from .watcher import STOP_TIMEOUT_DEFAULT, stop_watcher

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
    wait = STOP_TIMEOUT_DEFAULT if timeout is None else max(0.0, timeout)
    result = stop_watcher(pidfile_path, timeout=wait)
    state = result["state"]

    if state == "absent":
        typer.echo(f"unwatch: no watcher running (pidfile={pidfile_path})")
        raise typer.Exit(0)
    if state == "unknown":
        # The pidfile exists but could not be probed: do NOT claim no watcher.
        typer.echo(
            f"unwatch: cannot tell whether a watcher is running: "
            f"{pidfile_path} exists but could not be opened; not claiming no "
            f"watcher — check its permissions or stop it manually", err=True)
        raise typer.Exit(1)
    if state == "unresolvable":
        typer.echo(
            f"unwatch: a live watcher holds {pidfile_path} but its pid is "
            f"unreadable; stop it manually", err=True)
        raise typer.Exit(1)
    if state == "timeout":
        typer.echo(
            f"unwatch: sent SIGTERM to pid={result['pid']} but it still "
            f"holds {pidfile_path} after {wait}s", err=True)
        raise typer.Exit(1)

    typer.echo(f"unwatch: stopped watcher pid={result['pid']} "
               f"(pidfile={pidfile_path})")
    roots = result["roots"]
    if roots is None:
        typer.echo("unwatch: its watched projects could not be determined")
    else:
        typer.echo(f"unwatch: it was watching: {', '.join(roots)}")
        if not _covers(roots, targets):
            typer.echo("unwatch: note: it did not cover every requested "
                       "project", err=True)
    raise typer.Exit(0)


def _select_watchable(
        targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop targets whose root is missing or unreadable, warning once each.

    A registration whose directory is gone (or unreadable) must not be handed
    to the inotify scheduler — watchdog raises while installing the watch and
    the failure takes down the whole watcher. Each dropped target is named on
    stderr exactly once; the remaining projects are served. Exits nonzero only
    when nothing remains to watch, so an operator is told the difference
    between "some projects are gone" and "there is nothing to watch".
    """
    live: list[tuple[str, str]] = []
    for slug, path in targets:
        root = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(root) and os.access(root, os.R_OK | os.X_OK):
            live.append((slug, path))
        else:
            typer.echo(
                f"watch: skipping project {slug} ({path}): path is missing "
                f"or unreadable", err=True)
    if not live:
        _die("error: no watchable project roots (every requested path is "
             "missing or unreadable)")
    return live


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
    from .watcher import (BUSY_EXIT, PidFileLock, AlreadyRunning,
                          probe_watcher_holder, read_pid, spawn_background)

    # Parent-side pre-check: if a live watcher already holds the pidfile,
    # this is an idempotent re-invocation, not a failure. Compare the
    # requested targets against what the holder actually watches (recovered
    # from /proc). Only the daemon-side flock below is authoritative — this
    # check is advisory and the race path re-probes after daemonization.
    alive, holder_pid, holder_roots = probe_watcher_holder(pidfile_path)
    if alive:
        _report_already_running(targets, holder_pid, holder_roots,
                                pidfile_path)
        raise typer.Exit(0 if holder_roots is not None
                         and _covers(holder_roots, targets) else 3)

    daemon_state: dict[str, Any] = {}

    def setup() -> int:
        """Daemon-side startup. 0 == ready (the parent confirms liveness).

        The daemon holds the pidfile flock, builds the event engine AND
        installs the event source here, before the handshake — so a failure
        to start watching reaches the parent as a startup failure instead of
        a printed success over a daemon that is already gone.
        """
        import signal

        lock = PidFileLock(pidfile_path)
        if not lock.acquire():
            holder_pid = read_pid(pidfile_path)
            print(f"watch: refusing to start — a live watcher already holds "
                  f"{pidfile_path} (pid={holder_pid})", flush=True)
            return BUSY_EXIT
        daemon_state["lock"] = lock
        stop = {"flag": False}

        def _handle(_sig: int, _frm: object) -> None:
            stop["flag"] = True

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
        daemon_state["stop"] = stop
        try:
            core = Core()  # fresh core in the daemon process
            quiet = max(1, core.cfg.watch_debounce)
            engine = _make_engine(
                core, targets, quiet=quiet,
                sweep_interval=max(1, core.cfg.watch_sweep_interval))
            mode = "inotify" if engine.start_events() else "poll"
        except Exception as exc:  # noqa: BLE001 — any startup failure
            print(f"watch daemon: failed to start the watcher: {exc}",
                  flush=True)
            lock.release(unlink=True)
            return 1
        daemon_state["engine"] = engine
        print(f"watch daemon: pid={os.getpid()}, "
              f"watching {len(targets)} project(s) "
              f"({', '.join(slug for slug, _ in targets)}), mode={mode}, "
              f"pidfile={pidfile_path}, log={log_path}", flush=True)
        return 0

    def run_loop() -> None:
        engine = daemon_state["engine"]
        try:
            engine.run(duration, daemon_state["stop"])
        finally:
            daemon_state["lock"].release(unlink=True)
            print("watch daemon: exiting", flush=True)

    try:
        daemon_pid = spawn_background(pidfile_path, log_path, setup, run_loop)
    except AlreadyRunning as exc:
        # Lost the race between the pre-check and the daemon's flock
        # acquire: same idempotent case, reported cleanly, never as
        # "failed to start".
        holder_roots = _holder_roots_safe(exc.holder_pid)
        _report_already_running(targets, exc.holder_pid, holder_roots,
                                pidfile_path)
        raise typer.Exit(0 if holder_roots is not None
                         and _covers(holder_roots, targets) else 3) from exc
    except RuntimeError as exc:
        _die(f"error: {exc}")
    # The handshake says the daemon reached "ready"; confirm it is still
    # alive and holding the pidfile before claiming a start, so a daemon that
    # died the moment it entered its loop is reported as a failure with a
    # nonzero exit instead of a false "watch daemon started".
    if not _daemon_confirmed(daemon_pid, pidfile_path):
        _die(f"error: watch daemon exited immediately after startup "
             f"(pid={daemon_pid}); see {log_path}")
    typer.echo(f"watch daemon started: pid={daemon_pid} pidfile={pidfile_path} "
               f"log={log_path}")


def _daemon_confirmed(daemon_pid: int, pidfile_path: str) -> bool:
    """True when the daemon is alive AND still holds the pidfile flock.

    The flock — never pid existence alone — is the authoritative liveness
    signal (``watcher.PidFileLock``): a daemon that has exited has released
    it, so a free flock means the startup order is reporting a dead process.
    """
    from .watcher import pidfile_liveness

    # The bar is "alive AND still holds the pidfile flock": require the flock
    # to read as held. An unopenable pidfile is not proven held (unknown), so
    # the daemon is not confirmed either.
    if pidfile_liveness(pidfile_path) != "held":
        return False
    try:
        os.kill(daemon_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _holder_roots_safe(pid: int | None) -> list[str] | None:
    """Holder's watched roots from /proc, or None when not recoverable."""
    from .watcher import _holder_roots

    if not pid or pid <= 0:
        return None
    try:
        return _holder_roots(pid)
    except OSError:
        return None


def _covers(holder_roots: list[str], targets: list[tuple[str, str]]) -> bool:
    """True when the live holder watches every requested target.

    A root covers a target when the target path is the root itself or lies
    beneath it (a watcher on a parent directory serves subprojects).
    """
    for _slug, target in targets:
        target_abs = os.path.abspath(target)
        if not any(
            target_abs == root or target_abs.startswith(root + os.sep)
            for root in holder_roots
        ):
            return False
    return True


def _report_already_running(
    targets: list[tuple[str, str]],
    holder_pid: int | None,
    holder_roots: list[str] | None,
    pidfile_path: str,
) -> None:
    """Print the already-running verdict to the caller's terminal."""
    typer.echo(f"watch: already running "
               f"(pid={holder_pid}, pidfile={pidfile_path})")
    if holder_roots is None:
        typer.echo(
            "watch: holder's watched projects could not be determined "
            "(not on this machine or /proc unavailable); the existing "
            "watcher was left in charge", err=True)
        return
    typer.echo(f"watch: live watcher is watching: {', '.join(holder_roots)}")
    if _covers(holder_roots, targets):
        typer.echo("watch: requested project(s) already covered; nothing to do")
    else:
        typer.echo(
            "watch: requested project(s) are NOT covered by the live "
            "watcher (stop the live watcher (kill its pid or SIGTERM it) "
            "before starting a different set)", err=True)


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
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """Whole-project or per-subtree structural map from the manifest only
    (design §2.1): files with their symbol lines, one symbol per line,
    schema-v3 signature when stored. A read: answers from the index and
    reports its staleness; --refresh is the only flag that makes it write.
    --fresh is accepted and inert."""
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
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    data = core.skeleton(entry, prefix=path_prefix, tree_mode=tree_mode,
                         limit=limit,
                         include_signatures=not no_signatures)
    if json_output:
        data.update(block)
    text = core.format_skeleton(data, "json" if json_output else "text")
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
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """One file: declarations, signatures, one-line docstrings (design §2.2).
    A read; --refresh is the only flag that makes it write, --fresh is inert."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    try:
        data = core.outline(entry, file, include_docstrings=docstrings)
    except ValueError as exc:
        _json_failure(exc, json_output)
    if json_output:
        data.update(block)
    text = core.format_outline(data, "json" if json_output else "text")
    typer.echo(text)


@app.command(name="find-symbol")
def find_symbol(
    name: str = typer.Argument(
        None, help="Symbol name (optional: omit for browse mode with filters)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    symbol_type: str = typer.Option(
        None, "--kind", "--type", "--symbol-type",
        help="function|method|class|struct|enum|namespace."),
    file_filter: str = typer.Option(
        None, "--file", help="Restrict to this file (project-relative)."),
    name_pattern: str = typer.Option(
        None, "--name-pattern",
        help="Glob over the name, case-sensitive (`*Manager`, `get_*`)."),
    container: str = typer.Option(
        None, "--container",
        help="Parent qualified name (exact), e.g. `UserManager`."),
    visibility: str = typer.Option(
        None, "--visibility",
        help="public|private (a NULL visibility reads as public)."),
    language: str = typer.Option(
        None, "--language", help="File language, e.g. python."),
    inherits: str = typer.Option(
        None, "--inherits",
        help="Base class name from an attributed `base_class` reference."),
    has_call: str = typer.Option(
        None, "--has-call",
        help="Callee name from an attributed `call` reference."),
    substring: bool = typer.Option(
        False, "--substring", help="Substring match (browse mode)."),
    limit: int | None = typer.Option(
        None, "--limit",
        help="Max rows. Exact-name mode: every definition unless given. "
             "Browse mode and the substring fallback: default cap 25."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """Look up symbols by name in the manifest symbol index (no semantic
    search). Exact AST-first, capped substring fallback. With NAME omitted,
    browse mode: filters only, capped at --limit (design §2.3).

    The structural filters (--kind, --name-pattern, --file, --container,
    --visibility, --language, --inherits, --has-call) compose as AND in one
    SQL read. --inherits/--has-call answer from attributed syntax only, so
    an empty answer there is "not attributed", never "no" — the payload
    labels every row and names the unattributed files.

    --limit keeps its meaning and its default, and a capped answer says it
    was capped: JSON carries match_count and truncated, text mode prints
    how many of how many were shown.

    A read: --refresh is the only flag that makes it write, --fresh is inert,
    and the JSON object carries the staleness block."""
    core = _get_core(skip_stale_check)
    entry, err = core.resolve_entry(project)
    if entry is None:
        _json_failure(ValueError(err), json_output)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    if visibility is not None and visibility not in ("public", "private"):
        _json_failure(ArgumentError(
            f"--visibility must be 'public' or 'private', "
            f"not {visibility!r}"), json_output)
    filters: dict[str, Any] = dict(
        symbol_type=symbol_type, file=file_filter,
        name_pattern=name_pattern, container=container,
        visibility=visibility, language=language,
        inherits=inherits, has_call=has_call)
    structural = any(filters[k] for k in (
        "name_pattern", "container", "visibility", "language",
        "inherits", "has_call"))
    m = core.read_manifest(entry.slug)
    try:
        if name:
            # Exact-name mode answers with EVERY definition; --limit is
            # honoured when the caller asks for a bound (issue #22).
            rows = m.find_symbols(name, substring=False, limit=limit,
                                  **filters)
            match_label = False
            if not rows and not structural:
                # The substring tier stays a fallback for a bare name. With
                # a structural filter the caller asked for an attributed
                # answer, and a fuzzy substitute would answer a different
                # question than the one asked.
                rows = m.find_symbols(
                    name, substring=True,
                    limit=limit if limit is not None else _BROWSE_CAP,
                    **filters)
                match_label = True
            total = m.count_symbols(name, substring=match_label, **filters)
        else:
            # Browse mode (design §2.3): no name clause, filters only.
            rows = m.find_symbols(
                None, limit=limit if limit is not None else _BROWSE_CAP,
                **filters)
            match_label = False
            total = m.count_symbols(None, **filters)
        truncated = total > len(rows)
        payload_rows = [
            {"file": r.file, "name": r.name, "type": r.symbol_type,
             "start_line": r.start_line, "end_line": r.end_line,
             "signature": r.signature,
             # Identity, beside the staleness block's ``index_revision``: the
             # surrogate id means nothing outside that revision (ADR-0002
             # section 2), and decl_hash is the durable half.
             "qualified_name": r.qualified_name,
             "container": r.container,
             "language": r.language,
             "decl_hash": r.decl_hash,
             "symbol_id": r.symbol_id,
             # Where the answer stands: attributed syntax, a textual scan,
             # or a file nothing was attributed for. Never absent, so a
             # caller can tell `unknown` from a negative.
             "evidence": identity.evidence_of(r)}
            for r in rows]
        # A zero from an attributed-syntax filter is reported, not converted
        # into a negative: the files with no AST extractor were not examined.
        evidence_note = None
        if (inherits or has_call) and not rows:
            evidence_note = identity.unattributed_note(
                inherits or has_call, m.symbol_files_without_ast())
        if json_output:
            typer.echo(json.dumps({
                "schema": 1, "symbols": payload_rows,
                "match": ("browse" if not name else
                          "exact" if not match_label else "substring"),
                "match_count": total, "truncated": truncated,
                **({"evidence_note": evidence_note} if evidence_note else {}),
                **block}, ensure_ascii=False, indent=2))
            return
        if not rows:
            msg = (f"no symbols matching {name!r}" if name
                   else "no symbols matching the given filters")
            typer.echo(msg)
            if evidence_note:
                typer.echo(evidence_note)
            return
        for r in rows:
            label = ""
            if match_label:
                label = ", match=exact" if r.name == name else ", match=substring"
            conf = "exact" if r.source == "ast" else "heuristic"
            sig = f"  {r.signature}" if r.signature else ""
            typer.echo(f"- {entry.path}::{r.file}:{r.start_line}-{r.end_line} "
                       f"{r.name} ({r.symbol_type}, confidence={conf}{label}, "
                       f"evidence={identity.evidence_of(r)})"
                       f"{sig}")
        if truncated:
            cap = limit if limit is not None else _BROWSE_CAP
            typer.echo(f"... {len(rows)} of {total} matches shown "
                       f"(--limit {cap} reached; raise it to see the rest)")
    finally:
        m.close()


@app.command(name="find-definition")
def find_definition(
    name: str = typer.Argument(..., help="Symbol name (exact match only)."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """Find where a symbol is declared (exact name match only). A read:
    --refresh is the only flag that makes it write, --fresh is inert, and
    the JSON object carries the staleness block."""
    core = _get_core(skip_stale_check)
    entry, err = core.resolve_entry(project)
    if entry is None:
        _json_failure(ValueError(err), json_output)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    m = core.read_manifest(entry.slug)
    try:
        rows = m.definitions_of(name)
        definitions = [
            {"file": r.file, "name": r.name, "type": r.symbol_type,
             "start_line": r.start_line, "end_line": r.end_line,
             "confidence": "exact" if r.source == "ast" else "heuristic",
             "qualified_name": r.qualified_name,
             "container": r.container,
             "language": r.language,
             "decl_hash": r.decl_hash,
             "symbol_id": r.symbol_id}
            for r in rows]
        if json_output:
            typer.echo(json.dumps({"schema": 1, "name": name,
                                   "project": entry.path,
                                   "definitions": definitions, **block},
                                  ensure_ascii=False, indent=2))
            return
        if not rows:
            msg = f"no exact-match symbols named {name!r}"
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
    json_output: bool = typer.Option(False, "--json", help="JSON output (carries stale/re_resolved)."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """Retrieve ONLY the relevant source lines instead of whole files.

    Stale-safe: when the file changed since indexing (or --fresh is passed),
    symbol mode re-resolves the symbol against the live file (fresh lines);
    line mode returns the requested lines but emits one stderr warning line
    ('Warning: file changed since last index; line numbers may be shifted')
    and sets stale=true in JSON — the documented exception to
    stderr-empty-on-success (exit stays 0).

    A read: --fresh means "read the live file, ignore cached symbol rows" and
    writes nothing; --refresh runs one incremental pass first (the only flag
    that makes this read write). The JSON answer carries the index staleness
    block, while its `stale` field keeps its file-level meaning."""
    core = _get_core(skip_stale_check)
    entry = _resolve(core, project)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False, json_output=True)
    try:
        data = core.code_context(
            entry, file, start_line=start_line, end_line=end_line,
            symbol=symbol, context_lines=context_lines, live=fresh)
    except ValueError as exc:
        _json_failure(exc, json_output)
    if json_output:
        payload = {**block, **data, "schema": 1}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not data.get("stale"):
        line = staleness.warning_line(core.staleness(entry), entry.path)
        if line:
            typer.echo(line, err=True)
    for seg in data["segments"]:
        typer.echo(f"--- {data['file']}:{seg['start_line']}-{seg['end_line']} ---")
        if seg.get("error"):
            typer.echo(seg["error"])
            continue
        for line in seg["lines"]:
            typer.echo(line)


@app.command(name="find-references")
def find_references(
    name: str = typer.Argument(..., help="Target symbol name."),
    project: str = typer.Option(
        None, "--project", "--name",
        help="Project path, slug, or name (--project X or --name X)."),
    relationship: str = typer.Option(
        None, help="calls|includes|inherits|references — a filter over the "
                   "relationship each row actually carries."),
    limit: int = typer.Option(25, help="Max rows."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
    fresh: bool = FreshOpt,
) -> None:
    """Find references TO a symbol in the single reference store. Every row
    carries the relationship it was extracted with and its confidence, so a
    heuristic `identifier` hit is never mistaken for a resolved call:
    tree-sitter rows (`call`/`type`/`base_class`/`member`/`includes`,
    confidence `ast`) for python/typescript/tsx/javascript/c/cpp/go/csharp —
    `member` is a member access read such as a C# property/field, `includes`
    is the import/include target, and `member`/`call` rows record the receiver
    expression as written when it is a plain identifier — plus a textual scan
    (confidence `heuristic`) for every other language: an identifier followed
    IMMEDIATELY by `(` is a `call`, any other identifier is an `identifier`,
    and an import/include line is an `includes`. Comment-leading lines
    (`#`, `//`, `*`, `--`) are skipped there, but trailing comments and
    strings DO match. A filtered report that comes back empty never reads as
    "the name is absent": when rows exist under another relationship, the line
    names the unfiltered count and the relationships that do carry it (one
    aggregate query, not a second report). A bare `no references to X` is
    printed only when the store has none at all.

    This answers a DIFFERENT question than `find-callers`, which walks fan-in
    edges from the same store. The reference graph is textual and unbound by
    design: there is no compiler resolution, names are matched as text."""
    core = _get_core(skip_stale_check)
    entry, err = core.resolve_entry(project)
    if entry is None:
        _json_failure(ValueError(err), json_output)
    _refuse_obsolete_rows(core, entry, refresh=refresh)
    block, _ = _read_staleness(core, entry, refresh=False,
                               json_output=json_output)
    try:
        rows = core.find_references(entry, name, relationship=relationship,
                                    limit=limit)
    except ValueError as exc:
        _json_failure(exc, json_output)
    known = core.all_symbol_names(entry)
    references = [
        dict(r, project=entry.path,
             target_confidence=("exact" if r["target"] in known
                                else "heuristic"))
        for r in rows]
    if json_output:
        payload: dict[str, Any] = {
            "schema": 1, "name": name, "project": entry.path,
            "relationship": relationship, "references": references,
            "total": len(references), **block}
        if not rows and relationship is not None:
            counts = core.reference_relationship_counts(entry, name)
            if counts:
                payload["unfiltered_count"] = sum(n for _rel, n in counts)
                payload["unfiltered_relationships"] = [rel for rel, _n in counts]
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not rows:
        # A filtered-empty result must never read as "the name is absent":
        # one cheap aggregate query tells the two cases apart.
        if relationship is not None:
            counts = core.reference_relationship_counts(entry, name)
            if counts:
                total = sum(n for _rel, n in counts)
                kinds = ", ".join(rel for rel, _n in counts)
                plural = "reference" if total == 1 else "references"
                verb = "exists" if total == 1 else "exist"
                typer.echo(
                    f"no {relationship} references to {name!r}; {total} "
                    f"{plural} {verb} unfiltered ({kinds})")
                return
        typer.echo(f"no references to {name!r}")
        return
    typer.echo(f"references to {name!r} (confidence=how the row was "
               "extracted, receiver=the receiver expression as written, "
               "target_confidence=whether the name has a definition row):")
    for r in rows:
        exact = "exact" if r["target"] in known else "heuristic"
        src = f" in {r['from_symbol']}" if r["from_symbol"] else ""
        recv = f", receiver={r['receiver']}" if r.get("receiver") else ""
        typer.echo(f"- {entry.path}::{r['file']}:{r['line']}{src} "
                   f"{r['relationship']} {r['target']} "
                   f"(confidence={r['confidence']}{recv}, "
                   f"target_confidence={exact})")


@app.callback()
def _main(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Verbose diagnostics (INFO) on stderr."),
) -> None:
    """Global options for the code-indexer CLI."""
    from .logsetup import configure_logging

    configure_logging(verbose=verbose)


def main() -> None:
    """Console-script entry point (``code-indexer``).

    The last-line seam: any exception a command did not handle becomes one
    taxonomy line on stderr and the taxonomy's exit code, never a traceback
    (ADR-0003's output contract).
    """
    try:
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except BaseException as exc:  # noqa: BLE001 — the shared failure seam
        logger.info("command failed", exc_info=True)
        fail(exc)


# ---------------------------------------------------------------------------
# eval harness (retrieval-quality gate): CLI-only dev utility, no MCP surface
# ---------------------------------------------------------------------------

def _parse_ks(raw: str) -> list[int]:
    try:
        ks = sorted({int(part) for part in raw.split(",") if part.strip()})
    except ValueError:
        _die("error: --k must be a comma-separated list of integers, e.g. 1,3,5,10")
    if not ks or any(k < 1 for k in ks):
        _die("error: --k values must be positive integers")
    return ks


@app.command(name="eval")
def eval_command(
    project: str = typer.Option(..., help="Project path, slug, or name."),
    queries: str = typer.Option(..., help="Path to queries.jsonl."),
    k: str = typer.Option("1,3,5,10", "--k",
        help="Comma-separated k values for recall@k."),
    mode: str = typer.Option("dense", help=f"Eval mode: {EVAL_MODES}."),
    rerank: str = typer.Option(
        None, "--rerank", help="Rerank mode: heuristic | none (default off)."),
    out: str = typer.Option(None, "--out", help="Write the JSON report to this path."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Run a labeled query set against a project's index and report ranking
    metrics (recall@k, MRR, nDCG@10, latency, approx output tokens). Dev
    utility for retrieval-quality gates; deliberately CLI-only (no MCP
    surface). The 'hybrid' mode is accepted for forward compatibility but
    currently aliases the dense pipeline (the lexical index is not built
    yet)."""
    core = _get_core(skip_stale_check)
    if refresh:
        entry, _err = core.resolve_entry(project)
        if entry is None:
            _die(f"error: project not registered: {project}")
        core.refresh(entry)
    ks = _parse_ks(k)
    if mode not in EVAL_MODES:
        _die(f"error: unknown eval mode {mode!r} (expected one of {EVAL_MODES})")
    try:
        report = run_eval(core, project, queries, ks=ks, mode=mode, rerank=rerank)
    except ValueError as exc:
        _die(str(exc))
    if out:
        out_path = os.path.abspath(out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        typer.echo(f"wrote {out_path}", err=True)
    typer.echo(format_eval_report(report))


@app.command(name="eval-compare")
def eval_compare(
    a: str = typer.Argument(..., help="Baseline report path (JSON)."),
    b: str = typer.Argument(..., help="Candidate report path (JSON)."),
    skip_stale_check: bool = SkipOpt,
    refresh: bool = RefreshOpt,
) -> None:
    """Compare two eval reports: metric delta table + per-query win/loss.
    b is the candidate, a the baseline. --skip-stale-check and --refresh are
    accepted and inert: this utility reads report files, never an index."""
    for path in (a, b):
        if not os.path.isfile(path):
            _die(f"error: not found: {path}")
    try:
        ra, rb = load_report(a), load_report(b)
    except json.JSONDecodeError as exc:
        _die(f"error: bad report JSON: {exc}")
    typer.echo(compare_reports(ra, rb))


if __name__ == "__main__":
    main()
