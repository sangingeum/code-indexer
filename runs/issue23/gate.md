# Verification gate — issue #23 (get-code-context --symbol file-scope)

Verdict: **PASS**

- Under test: `81f0c8c` (fix) + `c5d60fb` (record), tip `c5d60fb`, base `f9e819b`.
- Commits: both `%G? = G` (signed), `origin/main` = `c5d60fb` (local `HEAD` equals it).
- Verifier: the QA gate. Gate run on the live index of this checkout, read-only
  (`--skip-stale-check` on every inspection). No source or test was edited.
- Issue #23 remains **open**; board card T-000014 was read-only for this leg.

## Evidence files (this directory)

| file | what it holds |
|---|---|
| `gate-bar1.txt` | the three filed commands at tip, with rc |
| `init-files.txt`, `find-symbol-init.json` | distinct files behind the 40 `__init__` rows |
| `gate-bar2.txt` | all 26 distinct files, per-file rc and first range line |
| `gate-bar3-base-cli.txt` | the same three commands at a base `f9e819b` build |
| `gate-bar3-base-pytest.txt` | the new pin file run against base source (5 failed, 1 passed) |
| `gate-bar3-tip-pytest.txt` | the same pin file at tip (6 passed) |
| `gate-bar6-pytest.txt`, `gate-bar6-ruff.txt` | full suite + lint, run once |
| `gate-bar8-shape.txt` | line-mode and symbol-mode JSON key sets, run not read |
| `bar5-callers.json` | `find-callers find_symbols` output used for the surface sweep |

## Bar-by-bar

### Bar 1 — the filed defect is gone on the live index — PASS

```
$ code-indexer get-code-context src/code_indexer/registry.py --symbol __init__ --skip-stale-check
--- src/code_indexer/registry.py:66-78 ---     rc=0
$ code-indexer get-code-context src/code_indexer/doctor.py --symbol __init__ --skip-stale-check
--- src/code_indexer/doctor.py:398-399 ---      rc=0
$ code-indexer get-code-context src/code_indexer/embedder.py --symbol __init__ --skip-stale-check
--- src/code_indexer/embedder.py:39-56 ---      rc=0
```

Ranges named, not merely "no error": `registry.py` row 26 of 40 and `doctor.py`
row 40 of 40 — exactly the two rows the issue reported as denied — now resolve,
and the in-cap `embedder.py` row 11 still resolves to the same range as before.

### Bar 2 — no sibling file is still denied — PASS

`find-symbol __init__ --json --skip-stale-check` → 40 rows across **26 distinct
files** (`jq length` = 40, `[.[].file]|unique|length` = 26). Every distinct file
was run through `get-code-context <file> --symbol __init__`: **checked 26,
failures 0** (`gate-bar2.txt`). This includes all 15 rows that sort past position
25 (`registry.py` 26, `core.py` 27, `test_search_isolation.py` 28, `fts.py` 29,
`test_unwatch.py` 30, `watcher.py` 31/35/39, `test_embed_cost.py` 32/34,
`manifest.py` 33, `test_result_shaping.py` 36, `test_concurrency.py` 37,
`test_watcher.py` 38, `doctor.py` 40) — 12 distinct files, not the 9 the issue
estimated; all of them pass now.

### Bar 3 — the pre-fix failure is real, not asserted — PASS

Genuine base build: `git worktree add --detach <base worktree at f9e819b> f9e819b`,
`uv sync --frozen`, `uv run python -c "import code_indexer; print(code_indexer.__file__)"`
→ `<base worktree at f9e819b>/src/code_indexer/__init__.py`
(its own venv, its own source — not the editable install of the tip checkout).
The base build was pointed at the same live index with `--project`.

Same three commands at base (`gate-bar3-base-cli.txt`):

```
registry.py -> InternalError: symbol '__init__' not indexed in src/code_indexer/registry.py   rc=1
doctor.py   -> InternalError: symbol '__init__' not indexed in src/code_indexer/doctor.py     rc=1
embedder.py -> --- src/code_indexer/embedder.py:39-56 ---                                    rc=0
```

That is the issue's repro verbatim: the two past-the-cap files are denied, the
in-cap file works.

Pin file at base: `tests/test_symbol_resolution.py` copied into the worktree and
run against base source → **5 failed, 1 passed** (`gate-bar3-base-pytest.txt`).
The single pass is the fixture-sanity test
(`test_the_name_population_outgrows_the_default_cap`), which must pass on both
sides by design. The five that fail at base and pass at tip:

- `test_symbol_mode_resolves_a_row_past_the_cap` — `ValueError: 'prepare' not indexed in late_definition.py`
- `test_cli_symbol_mode_does_not_deny_an_indexed_row` — `InternalError`, exit 1
- `test_eval_symbol_range_resolves_a_row_past_the_cap` — `assert None == (5, 6)`
- `test_symbol_mode_returns_every_definition_in_the_file` — `assert 5 == 6`
- `test_re_resolved_symbol_mode_returns_every_definition` — `assert 5 == 6`

At tip the same file → **6 passed** (`gate-bar3-tip-pytest.txt`).

Worktree removed afterwards (`git worktree remove --force`, `git worktree prune`);
`git worktree list` now shows only the main checkout, the directory is gone, and
the tracked tree is clean.

### Bar 4 — the fixtures actually pin the defect — PASS

`tests/test_symbol_resolution.py` covers all three required shapes, and bar 3 is
what proves they are pins rather than descriptions:

- (a) manifest path with a row sorting past 25 — `wide_rig` = 25 early files plus
  `late_definition.py`; `test_the_name_population_outgrows_the_default_cap`
  asserts the capped read really is 25 rows and really excludes the late file,
  then `test_symbol_mode_resolves_a_row_past_the_cap` and the CLI test resolve it.
- (b) changed-file re-resolve path — `test_re_resolved_symbol_mode_returns_every_definition`
  edits the file after indexing, asserts `re_resolved is True`, `stale is False`
  and all six ranges.
- (c) more than five definitions of one name in one file — `repeated_rig` with six;
  `test_symbol_mode_returns_every_definition_in_the_file` asserts six segments in
  ascending order (fails at base with 5).

### Bar 5 — `_symbol_range` fixed and pinned; no other post-cut filter — PASS

`_symbol_range` (src/code_indexer/evalharness.py:109) now reads
`find_symbols(symbol, substring=False, file=rel_file, limit=None)` — both filters
in SQL, no cap — and is pinned by
`test_eval_symbol_range_resolves_a_row_past_the_cap` (None at base, `(5, 6)` at tip).

Surface sweep of every `find_symbols(` call in `src/` (6 sites; `rg` for the exact
token after `find-callers find_symbols`), plus a sweep for the post-cut filter
shape itself (`if r.file ==` / `if r.name ==` / `for … in …find_symbols`). What
remains, and why each is safe:

| site | shape | why it is safe |
|---|---|---|
| `core.py:782` (`_fuse_lexical_dense`) | exact-name read at the default 25, then `exact[:3]` | No file or name filter after the cut — the answer is a **prefix** of a total order (`(ast first, name, start_line, file)`), so first 3 of 25 equals first 3 of all. Claim checked against the code *and* the comment now in the tree (lines 776–781 name issue #23 and state the contrast correctly). |
| `cli.py:1364` (`find-symbol` exact mode) | `limit=limit` (default `None`) | Uncapped by default, no post-filter. |
| `cli.py:1368` (substring fallback) | `_BROWSE_CAP` (25) applied in SQL | No post-filter; the cap is the documented answer (`--limit` help: "Browse mode and the substring fallback: default cap 25"). |
| `cli.py:1374` (browse mode) | `_BROWSE_CAP` (25), `--file`/`--type` in SQL | No post-filter; design §2.3 cap, announced in help text. |
| `manifest.py:342` (`definitions_of`) | delegates with `limit=None` | Whole read by contract (issue #22). |
| `core.py:1226` (`code_context`) | `file=rel, limit=None` | This fix. |
| `evalharness.py:109` (`_symbol_range`) | `file=rel_file, limit=None` | This fix. |

Two further Python-side file filters exist and are **not** the defect shape,
because no cap sits in front of them: `core.py:981` (skeleton prefix filter over
`symbols_with_files()`) and `overview.py:94/105` (directory counts / entry points
over `all_symbols()`). Both read uncapped `SELECT`s — `symbols_with_files()`
(manifest.py:345-355) and `all_symbols()` carry no `LIMIT`.

### Bar 6 — suite and lint reproduce — PASS

```
$ uv run pytest tests/ -q -W error
506 passed, 2 deselected in 73.81s (0:01:13)        rc=0
$ uvx ruff check src tests
All checks passed!                                  rc=0
```

Run once, captured to `gate-bar6-pytest.txt` and `gate-bar6-ruff.txt`.

The repo's doc-drift test runs inside that suite (CHANGELOG/SKILL-CLI edits are
covered): green.

### Bar 7 — no reindex is owed — PASS

- `src/code_indexer/graph_extract.py:95` → `EXTRACTION_VERSION = 4` (unchanged;
  base f9e819b also `4`, and the constant is not in the diff).
- `src/code_indexer/ts_chunker.py:27` → `CHUNKER_VERSION = 1`;
  `src/code_indexer/embed_text.py:31` → `EMBED_FORMAT = "contextual-header-v1"` —
  both identical at base.
- `git diff --name-only f9e819b..c5d60fb -- src/` → exactly
  `src/code_indexer/core.py`, `src/code_indexer/evalharness.py`.
- `git diff f9e819b..c5d60fb | rg "schema|chunker|fingerprint|EMBED_FORMAT"` → no
  matches: the diff touches no schema, no chunker, no embed fingerprint.

Query-time only; no index generation is affected, so nothing is owed a reindex.

### Bar 8 — the answer shape is unchanged — PASS

Run, not read (`gate-bar8-shape.txt`), against the live index:

- Line mode, a stale file (`manifest.py --start-line 292 --end-line 294 --json`):
  rc=0, stderr carries `WARNING file changed since last index; line numbers may be shifted`,
  JSON `stale: true`, `re_resolved: false`, one segment, lines present.
- Symbol mode, `registry.py --symbol __init__ --json`: `stale: false`,
  `re_resolved: false`, one segment.
- Symbol mode, `watcher.py --symbol __init__ --json` (three definitions in one
  file): three segments at starts 94, 147, 391 — multi-segment answer intact.
- Key set at tip: `{file, re_resolved, schema, segments, stale}`; segment keys
  `{start_line, end_line, lines}`. The base build `f9e819b` returns the **same**
  key set for both line and symbol mode — so `schema` is pre-existing, and
  removing the five-range cap added no key.

## What remains capped, and why

1. `find_symbols`' own default `limit=25` — deliberately unchanged; callers that
   want the whole read say `limit=None` (`definitions_of`, `code_context`,
   `_symbol_range`, `find-symbol` exact mode).
2. `cli.py:1368` substring fallback and `cli.py:1374` browse mode — `_BROWSE_CAP`
   (25), design §2.3, announced in `--limit` help and in the command docstring.
3. `core.py:782` fused lexical/dense pin — takes 3 of a 25-row exact read; a
   prefix of a total order, so the cap cannot change which three are pinned.

## Observations (outside the bars, not defects of this fix)

- The live index is **stale relative to the working tree**: line-mode reads warn
  `file changed since last index` (seen on `manifest.py`; `core.py` itself is
  stale because this fix moved its line numbers). `index-status` reports
  `state=idle`. No default-mode command was run, so no automatic refresh fired
  during this gate; the index was left untouched (reindex is not mine to run).
  Bar 1/2 are unaffected because every symbol row exercised still matches the
  file's current content at the cited lines.
- The brief describes the JSON object as exactly `file`/`stale`/`re_resolved`/`segments`;
  measured it is those four plus a pre-existing `schema` key. Identical at base,
  so it is not a change from this fix — noted only so the brief and the tool
  agree.
- `index-status` takes a path argument and offers neither `--project` nor
  `--json`; used the path form. No tool defect observed.

## Repro commands (copy-pasteable)

```bash
cd <checkout>
code-indexer get-code-context src/code_indexer/registry.py --symbol __init__ --skip-stale-check; echo rc=$?
code-indexer get-code-context src/code_indexer/doctor.py   --symbol __init__ --skip-stale-check; echo rc=$?
code-indexer get-code-context src/code_indexer/embedder.py --symbol __init__ --skip-stale-check; echo rc=$?
while read -r f; do code-indexer get-code-context "$f" --symbol __init__ --skip-stale-check >/dev/null || echo "FAIL $f"; done < runs/issue23/init-files.txt
uv run pytest tests/test_symbol_resolution.py -q -W error
uv run pytest tests/ -q -W error && uvx ruff check src tests
```
