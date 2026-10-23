# Round record — lifecycle selector surface (issue #29)

Follow-up to the selector unification landed for `index-status`. The four
remaining lifecycle commands now accept the same dual selector: a positional
path or a `--project` / `--name` flag, both resolved through the one registry
lookup that understands a path, a slug, or a custom name.

## Selector semantics (all four commands)

- The flag wins when both spellings are given.
- Path, slug, and custom name all resolve, exactly as for `index-status`.
- The positional form remains supported and stays the MCP call shape.
- No selector ever reaches a second resolver: every selector goes through
  `core.resolve_entry`, wrapped by `_resolve` for commands that hard-fail and
  called directly by the read-only check.

### `lookup-project` (read-only check)

- Accepts the positional path or `--project`/`--name`; flag wins.
- Reports the canonical registered path when found.
- An unregistered selector still prints `not registered: <path>` and exits 0
  — never a hard error.
- An empty call resolves by cwd like `index-status`, because the command is
  read-only.

### `reindex-project` and `remove-project` (mutating)

- Accept the positional path or `--project`/`--name`; flag wins.
- With neither, exit 2 with an `ArgumentError` naming the command and listing
  the registered projects. A mutating command never guesses a project from
  the working directory.
- An unregistered selector dies with the registry's `NotFoundError`.
- `remove-project` still drops the same collection, deletes the same
  manifest/index files and removes the same registry row for the same
  project. A test proves the drop and the file/row removal against a
  temporary registry/index root.

### `index-more` (scope-widening)

- Two selector spellings:
  - `index-more <project> <subpath>` (historical, still the docs/tests form)
  - `index-more --project <project> <subpath>` (the single positional is the
    subpath)
- `--project` plus two positionals is ambiguous and exits 2 with an
  `ArgumentError` rather than silently choosing one.
- With no selector at all, exits 2 listing the registered projects.
- An unregistered project dies with `NotFoundError`.
- The scope_include append and incremental pass behavior is unchanged.

## Implementation

- `src/code_indexer/cli.py`
  - Added `_usage_die` (single-line `ArgumentError`, exit 2) for usage errors
    distinct from the existing `_die` taxonomy seam.
  - Added `_require_selector`, the one explicit-selector gate for mutating
    commands: returns the flag or positional selector, or exits naming the
    registered projects.
  - `index_more`: dual selector declaration (`first`, `second`, `project`),
    ambiguity rejection, subpath requirement, `_resolve` through the selector.
  - `remove_project`: dual selector declaration, explicit-selector gate,
    `_resolve` to the canonical entry before the unchanged removal body.
  - `lookup_project`: dual selector declaration, `core.resolve_entry` for the
    read-only check, not-registered still prints and exits 0, empty call may
    resolve by cwd.
  - `reindex-project`: dual selector declaration, explicit-selector gate,
    `_resolve` to the canonical entry before the unchanged swap rebuild.

No query path, indexer, schema, manifest, or MCP tool signature changed. The
MCP server's positional call shapes (`lookup-project <path>`,
`remove-project <path>`, `reindex-project <path>`, `index-status <path>`)
still work and are pinned by the existing MCP tests.

## Tests added

`tests/test_lifecycle_selector.py` — 24 tests, one group per command:

- flag accepted; positional still works; flag wins when both are given;
  slug and name resolve; not-found semantics; mutating commands refuse to
  guess from cwd; `lookup-project` prints not-registered and exits 0; empty
  lookup resolves by cwd; `index-more` historical form, flag form, ambiguity
  rejection, missing-subpath rejection, and no-selector rejection; and the
  `remove-project` collection/manifest/registry-row removal proof against a
  temporary registry/index root.

The MCP server tests needed no change: the positional call shapes were
already pinned.

## Verification

- `uv run pytest tests/ -q -W error` — 561 passed, 2 deselected.
- `uvx ruff check src tests scripts` — all checks passed.
- `uv run python scripts/publishing_hygiene_lint.py` — scanned=302,
  skipped=0, violations=0.

## Deviations

None from the selector surface. The new tests exercise stubs and temporary
directories only; no real registered project was reindexed or removed.
