# Round record — `index-status` accepts the same project selector as `overview`

Issue #28. The selector surface is unified without changing the command's
informational semantics.

## Commits

| commit | subject |
|---|---|
| `a940a8f` | `fix(cli): accept --project/--name on index-status (#28)` |

The record commit follows the fix commit; its SHA is reported to the
dispatcher and is not self-referenced here.

## Design decision

`index-status` keeps its historical positional `PATH` argument and gains the
dual option `--project` / `--name` used by the other project-scoped commands.
Both spellings flow through the single resolver `_resolve(core, project or
path)`, so a path, a slug, or a registered custom name all resolve the same
way. **When both are given the flag wins**; the help text on the option and
on the positional argument states this. The positional form stays mandatory
for compatibility: the MCP server invokes `["index-status", path]`.

The output line now reports `project={entry.path}`, the resolved canonical
path, so a slug or name invocation still names the project path. The command
still performs no re-index by default; `--refresh` runs the staleness pass as
before.

## Changes

- `src/code_indexer/cli.py` — `index_status` signature and resolution.
  Positional `path` becomes optional; `--project`/`--name` added; resolution
  via `_resolve(core, project or path)`; `project=` prints `entry.path`.
- `tests/test_scoped_indexing.py` — four selector tests: flag form,
  positional form, slug/name via the flag, and both-given precedence with
  distinct projects.
- `README.md` — quick-start example and troubleshooting sentence now show the
  flag spelling and state that the positional form still works.
- `SKILL-CLI.md` — lifecycle command block and §Project selection now
  describe the dual spelling for `index-status` and the flag precedence.

## Verification

- Targeted selector suite: `uv run pytest tests/test_scoped_indexing.py -q -W error`
  → 12 passed in 2.56s.
- Full offline suite: `uv run pytest tests/ -q -W error`
  → 537 passed, 2 deselected in 75.15s.
- Documentation drift guard: `uv run pytest tests/test_docs.py -q -W error`
  → 13 passed in 0.77s.
- Lint: `uvx ruff check src tests scripts` → All checks passed.
- Publishing hygiene: `uv run python scripts/publishing_hygiene_lint.py`
  → scanned=298 skipped=0 violations=0.
- Live selector check against the registered repo:
  `code-indexer index-status --project code-indexer --skip-stale-check`,
  the same command with `--name code-indexer`, and the positional form all
  printed `project=<repo path> state=idle` and exited 0.

## Review flags

None. The change is additive: no query path, index pass, schema, or MCP tool
signature changed. The only observable difference is that `index-status`
accepts the flag spellings and prints the resolved canonical path.
