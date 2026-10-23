# Two-axis review — lifecycle selector surface (issue #29)

## Verdict

**PASS-WITH-FOLDS.** The selector unification is correct: one resolver, the
flag genuinely wins where both spellings can be supplied, mutating commands
refuse cwd inference, and the failure exits match the taxonomy. No correctness
blocker. Two folds: a documentation overclaim about `index-more`, and a test
gap where the claimed hash-slug path is never exercised.

Reviewed commits: `7305c52` (code, tests, dev record, CHANGELOG), `f0b2ad5`
(docs), tip `ff81ffa` (gate record). HEAD equalled `origin/main` at review time.

## Axis 1 — correctness and risk

### 1.1 Selector path is single; flag precedence is real — PASS

All four commands reach `core.resolve_entry` exactly once: `reindex-project`,
`remove-project` and `index-more` through `_resolve` (`src/code_indexer/cli.py`
72-83), `lookup-project` directly at `cli.py:572`. `_require_selector`
(`cli.py:93-108`) only chooses between the two spellings and lists registered
projects on failure; it does not resolve. No second resolver exists.

Flag precedence: `_require_selector` computes `selector = project or path`
(`cli.py:102`); `lookup-project` computes `selector = project or path`
(`cli.py:571`). A supplied flag therefore replaces the positional outright —
there is no fallback to the positional when the flag fails to resolve. Verified
by reading `cli.py:469-481`, `cli.py:511-513`, `cli.py:571-572`,
`cli.py:875-876`.

### 1.2 Failure exits match the taxonomy — PASS

`_usage_die` raises `ArgumentError` (`errors.py:42-44`, `exit_code = EXIT_USAGE`
= 2); `_die` routes through `errors.classify`, which maps the resolver's
"project not registered" text to `NotFoundError` (`errors.py:88-93`) at exit 1.
Observed live at the tip:

- `index-more` with no selector → exit 2, `ArgumentError: ... needs an explicit
  project ... registered projects: ...`
- `remove-project` with no selector → exit 2, same shape.
- `index-more --project <slug>` without a subpath → exit 2, "needs a subpath".

### 1.3 Destructive path — correct, with one concurrency note

`remove-project` resolves to a `ProjectEntry`, then uses that entry's own
`path` and `slug` for the registry delete, collection drop and index-file
removal (`cli.py:511-528`). A positional path, a flag name, a flag slug and a
flag path all resolve to the same row and therefore the same artifacts. A
symlinked positional resolves through the `realpath` fallback in
`core.resolve_entry` (`core.py:282-285`) to the registered row; the removal
body never touches the source tree, only `index_root/<slug>*` and the
`index_root/<slug>` directory. It cannot delete the wrong tree.

**Note (C1):** resolve and remove are two statements with no lock between them
(`cli.py:512` and `cli.py:514`). If another process removes and re-registers
the same path in that window, `registry.remove(path)` returns the new row and
the command deletes the newly registered project's artifacts. Narrow race, no
wrong-tree deletion; the `if entry is None: _die` branch at `cli.py:515-516` is
only reachable through it. Not a blocker.

### 1.4 `index-more` argument matrix — PASS, one pre-existing scope note

Verified by reading `cli.py:469-481` and by live invocation:

| call | outcome |
|---|---|
| `<project> <subpath>` | resolves project, appends subpath |
| `--project <project> <subpath>` | resolves flag, appends first positional |
| `--project` + two positionals | exit 2, ambiguous (`cli.py:470-472`) |
| `--project <project>` (no subpath) | exit 2, "needs a subpath" |
| no selector | exit 2, lists registered projects |

No combination silently reinterprets one spelling as the other.

**Note (C3):** a subpath is appended to `scope_include` verbatim with no
containment check, so an absolute or `../` subpath widens scope outside the
project. That is pre-existing in the historical form; the flag form only makes
the mistaken-project reading more plausible. Out of scope for this change, but
worth a separate issue if containment matters.

### 1.5 `lookup-project` read-only — PASS

`Core.__init__` performs no staleness pass (`core.py:98-116`); the pass runs
only through explicit probe methods. `resolve_entry` reads the registry and
`status_summary(entry)` is called with `collections=None`, so no store probe.
Neither spelling, nor the empty call that resolves by cwd, triggers indexing or
a staleness pass. The only writes are `Core`'s pre-existing index-root
creation and registry open. "No indexing triggered" holds.

### 1.6 MCP compatibility — PASS

The MCP tools call the positional shape unchanged: `server.py:119`
`["lookup-project", path]`, `server.py:158` `["remove-project", path]`,
`server.py:241` `index_status`, `server.py:263` `["reindex-project", path]`.
There is no `index-more` MCP tool: the registered tool functions in
`server.py` are lookup, add, remove, list, semantic-search, index-status,
reindex, find-symbol, find-symbols, skeleton, outline, find-definition,
find-references, find-callers, find-callees, deps, changed-symbols and
get-code-context. The dev record's "index-more has no MCP tool" is true.

### 1.7 Test quality — FOLD (C6)

The file is not merely asserting exit codes: the four "accepts project flag"
tests fail if the flag is ignored (no positional is supplied and the rig's cwd
is not a registered project), and the three "flag wins when both given" tests
assert on the *flag* project's identity and the absence of the positional one
(`tests/test_lifecycle_selector.py:141-151`, `212-221`, `288-298`). Those are
genuine.

**Fold:** the three "resolves slug and name via flag" tests register with
`--name monoproj`; `sanitize_name("monoproj")` returns `monoproj`
(`registry.py:52-62`), so the slug equals the name and both loop iterations
pass the same string. The hash-slug branch (`slug_for` → 8-char sha256,
`registry.py:48-49`) is never exercised for these commands. Locations:
`tests/test_lifecycle_selector.py:130-138` (lookup), `201-209` (reindex),
`276-285` (remove). Fix: register one project without `--name` to obtain a
hash slug, or use a name whose sanitized form differs from the given name, and
assert both spellings against distinct slug and name values.

### 1.8 Regression risk outside the change — PASS

`registry.get_by_path` is unchanged and its other callers still use it
(`cli.py:408`, `cli.py:829`, `tests/test_watcher.py:98`,
`tests/test_sensitive_exclusion.py:255`). The `index_more` parameter rename
(`path, subpath` → `first, second`) has no Python callers: only the definition
exists (`rg 'index_more\\(' src/ tests/` → `cli.py:446`). Nothing breaks
silently.

### 1.9 Missing matrix variants — Note (C7)

No test covers `remove-project --project <full path>`, `index-more --project`
with no subpath, or `index-more --project <unregistered> <subpath>`. All three
are live-verified or covered by adjacent tests, but adding them to the same
file would close the matrix.

## Axis 2 — design and house style

### 2.1 `_usage_die` beside `_die` — justified; duplication noted (D2)

The split is necessary, not decorative: without it a "needs an explicit
project" message would fall through `errors.classify` to `InternalError` at
exit 1 instead of `ArgumentError` at exit 2. The taxonomy naming and exit codes
in `errors.py` are consistent. The duplication is the four-line body
(`cli.py:60-69` vs `86-90`): both import `fail`, call it and raise the
unreachable assertion. A single helper taking an `IndexerError` instance would
remove the drift risk. Note, not a blocker.

### 2.2 Over-engineering — nothing material (D4)

`_usage_die` and `_require_selector` are each used three times, so neither is
speculative. The only defensive branch that cannot normally be reached is the
concurrency branch in `remove-project` (C1). A pre-existing pattern is copied:
`index-status` declares the same selector option inline (`cli.py:688-691`), and
the four new commands repeat it; a module-level selector option would remove
five copies. Note only — the change follows the established pattern.

### 2.3 Terminology — note (D3)

No terminology lint exists in this repository (`scripts/` contains only the
publishing hygiene lint); the publishing lint reports zero violations. The new
names are natural language except the two `index-more` positional parameters,
`first` and `second`, whose meaning shifts between the two spellings and which
surface in help as `[first] [second]` (observed via `index-more --help`). This
is plumbing-shaped rather than cryptic; worth clearer help text in a later
round, not a blocker here.

### 2.4 Doc/code drift — FOLD (D1)

`README.md`, `SKILL-CLI.md` and the `CHANGELOG.md` entry say the lifecycle
commands accept the dual selector and "the flag wins when both are given".
That is false for `index-more`: combining the flag with two positionals exits 2
as ambiguous (`cli.py:470-472`). `SKILL-CLI.md` additionally says "the
positional forms stay MCP-compatible" for the group, but `index-more` has no
MCP tool. The dev record's "Selector semantics (all four commands)" header
(line 10) makes the same overclaim; its "positional form ... stays the MCP call
shape" (line 12) is likewise wrong for `index-more`. The gate's bar 2 already
scopes the claim correctly to "all three commands where both can be supplied",
so the gate's wording is the model to follow.

### 2.5 Process — notes only

- **Base-regression evidence.** The gate disclosed that its first base run left
  no transcript, and the closure in the gate record is also transcript-less.
  Re-run for this review in a detached worktree at base `1a7e70e` with the new
  test file copied in: **19 failed, 5 passed in 4.39s**, matching the gate's
  count; the five passing are the positional regression guards. Evidence is
  left as an untracked capture under `runs/issue29/review/`. Process note for
  later rounds: a gate that claims a bar should persist its transcript in the
  round's evidence directory.
- **Commit granularity.** Code, tests and the dev record landed together in
  `7305c52` after the tree went green; docs in `f0b2ad5`. The "commit when it
  builds" guard was not violated — the single authoring commit was made with a
  green tree. Noted only.

## Rulings — corrected wording

1. **"The flag wins when both are given" applies to three commands, not four.**
   Correct wording: "For `reindex-project`, `remove-project` and
   `lookup-project` the flag wins when both are given. `index-more` accepts
   `<project> <subpath>` or `--project <project> <subpath>` and rejects the
   flag plus two positionals as ambiguous."
2. **"Positional forms stay the MCP call shape" applies to three commands.**
   Correct wording: "The positional forms remain the MCP call shape for
   `lookup-project`, `remove-project`, `index-status` and `reindex-project`;
   `index-more` has no MCP tool."
3. **The gate's bar 4 correction stands.** The earlier capture tested a
   selector that resolves to nothing; the real hash-slug case was verified
   directly. The gate record's corrected wording is accurate and needs no
   further change. The new test file does not, however, pin the hash-slug path
   (see C6).

## Folds

1. **Docs (README.md, SKILL-CLI.md, CHANGELOG.md):** scope the "flag wins"
   and "MCP call shape" claims to the commands where both spellings can be
   supplied, and state `index-more`'s ambiguity rejection in the same sentence.
   Wording in Rulings 1 and 2.
2. **Tests (tests/test_lifecycle_selector.py:130-138, 201-209, 276-285):**
   replace the vacuous slug/name selector pair with distinct values — a hash
   slug from a project registered without `--name`, or a name whose sanitized
   form differs — so the `get_by_slug` branch is actually exercised for
   `lookup-project`, `reindex-project` and `remove-project`.

## Verification observed at the tip

- `uv run pytest tests/ -q -W error` → **561 passed, 2 deselected** in 81.15s,
  exit 0.
- `uv run pytest tests/test_lifecycle_selector.py -q -W error` → **24 passed**
  in 4.62s, exit 0.
- `uvx ruff check src tests scripts` → **All checks passed!**, exit 0.
- `uv run python scripts/publishing_hygiene_lint.py` → **scanned=305 skipped=0
  violations=0**, exit 0.
- Base regression re-run (detached worktree at `1a7e70e`, new test file copied
  in): **19 failed, 5 passed**, matching the gate's count.

```
$ git log --format='%h %G?'
ff81ffa G
f0b2ad5 G
7305c52 G
```

HEAD `ff81ffab6b8440193e9acca34367aaed532b4e4b` equalled `origin/main` at
review time. Working tree carried only known local evidence artifacts; no
tracked modifications.
