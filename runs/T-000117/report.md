# T-000117 — narrow re-verify of the R2b fold leg

Reviewed tip: `dea142d` (`git ls-remote origin refs/heads/main` = `dea142d39b61…`).
Fold range `b813871..dea142d`, four commits, nine files, every commit `%G? = G`:

```
0852762 G fix(query): name the class order in the text header
db2ca7e G fix(query): read the project's class ladder in semantic search
da1bcb1 G docs(query): state the real place of the class key, and the cases it does not close
dea142d G docs(round): record the folds and the verification at the fold tip
```

Out of scope by the card: the nine gate bars and everything outside the folded
files. Nothing here is a fix; a failing row is reported, not repaired.

## Probes

Two throwaway trees, indexed into a throwaway index root
(`$INDEX_ROOT=…/scratch/t000117/state`), never the live tree:

- `declared/` — identical content to `plain/`, but its `pyproject.toml` says
  `[tool.code-indexer.ordering]` / `tests = ["spec_e2e"]`;
- `plain/` — same files, no ordering table.

Both trees: `src/real.py` (`run_pipeline`, `shared_name`), `spec_e2e/a.py`
(`beta_pipeline_case`, `shared_name`). Backends: the configured Ollama and
Qdrant (doctor: reachable, dim probe 4096).

## Rows

### F1 — PASS (core.py:1355, tests/test_result_shaping.py:237)

`05-declared-dense.txt`, `semantic-search "beta pipeline case" --mode dense`
on the declaring tree, first line and full order:

```
search results (file class, then score desc):
- [0.6402] declared::src/real.py:4-11 (run_pipeline, shared_name, function)
- [0.3485] declared::pyproject.toml:1-6 ([project])
- [0.8396] declared::spec_e2e/a.py:4-11 (beta_pipeline_case, shared_name, function)
```

The top source hit scores **0.6402**, the tests-rung hit **0.8396**: the answer
crosses classes and the header names the order it produced, with no bare
`score desc`. The pin moved with the label:

```
tests/test_result_shaping.py:237:    assert out.startswith("search results (file class, then score desc):")
```

No other test pins the old string (`tests/test_vector_policy_matrix.py:231`
asserts only its absence).

### F2 — PASS (core.py:1200-1207, ranking.py:233-241, db2ca7e)

Same query, same chunk content, two trees; `06-plain-dense.txt` is the no-table
control:

```
plain   ::spec_e2e/a.py  0.8398      <- source rung, cosine order stands
plain   ::src/real.py   0.6425
plain   ::pyproject.toml 0.3424
```

So the identical `spec_e2e/` path is `source` without the table and the `tests`
rung with it, and the demotion happens through the real entry point — a CLI
`semantic-search`, not a unit test. `Core.search` reads the ladder at
`core.py:1200` (`names_by_project = {e.path: self.class_names(e.slug) …}`),
`self.class_names` (core.py:220-238) is `ordering.load_class_names(entry.path)`,
and `ranking.class_order` resolves it per hit at `ranking.py:233-241`
(`ladder(hit)` → `class_weight(file, ladder(hit))`).

The same class reaches the structural surface: `find-definition shared_name`
returns `src/real.py` first on `declared` and `spec_e2e/a.py` first on `plain`
(`07-…txt` / `08-…txt`) — with equal class the SQL tiebreak is `file`, and
`spec_e2e` < `src`, so only the ladder can swap those two rows. `overview --json`
agrees: `.tests.files = ["spec_e2e/a.py"]`, `count = 1` on `declared`
(`10-overview-declared.json`), `[] / 0` on `plain` (`12-overview-plain.json`).

`--all-projects` resolves the ladder **per hit**, not one global table —
`11-all-projects-dense.txt`, one fan-out over both trees:

```
plain   ::spec_e2e/a.py  0.8391   rank 1
…
declared::spec_e2e/a.py  0.8391   last
```

Two hits of the same path with the same cosine, split by the tree they sit in.
A single table cannot produce that split; `names_for(project_path)` is looked up
from `hit["project"]`, and `hit["project"]` is `entry.path` (core.py:1024),
the same key `names_by_project` is built from.

### F3 / F4 — PASS (query.md:189-193, CHANGELOG.md:10-12, core.py:1176-1215)

`query.md` (Ordering):

> Every one of those modes runs the class key after the scoring pass and the
> pure-data share cap, before the generic caps (`--per-file`,
> `--max-chars`/`--max-tokens`, `--limit`): it reorders and never removes, while
> they remove.

`CHANGELOG.md`:

> … after the scoring pass and before the generic output caps, so a
> near-duplicate test can no longer win a question about the implementation …

The code, in statement order inside `Core.search`: `cap_data_file_share`
(core.py:1176 single project / 1183 fan-out) → `merge_overlapping` →
`class_order` (1206) → `cap_per_file` (1208) → `truncate_snippets` +
`trim_to_budget` (1209-1211) → `all_hits[:max(1, limit)]` (1215). The two halves
the row asks about are exact: the class order runs **after** the pure-data share
cap and **before** every generic cap, and the caps do remove —
`ranking.cap_per_file` drops the overflow outright (ranking.py:382-401),
`trim_to_budget` counts dropped hits (ranking.py:404-430) and the `--limit`
slice cuts the tail (core.py:1215). Witness: `16-maxchars-drops.txt`,
`--max-chars 400` → "(1 lower-ranked hit(s) dropped by the output budget …)".

### F5 — PASS (ordering.py:104,181-232, .codeindexignore:9,16)

Both narrowed claims hold against the classifier and the ignore file:

```
14-class-probe.json  fixtures/a.py       -> source
14-class-probe.json  runs/x.err          -> source
14-class-probe.json  tests/fixtures/a.py -> tests
```

`_TEST_DIRS = {__tests__, spec, specs, test, tests}` (ordering.py:104), so a
top-level `fixtures/` tree is not a tests directory and `fixtures/a.py` stays
`source`; a fixture under `tests/` (or `spec/`) is demoted. A captured `.err`
under `runs/` is `source` to the classifier — the order leaves it where it was,
which is what the record now says. The transcript case is closed by
`.codeindexignore` (`/runs/` line 9, `/eval/` line 16), not by the order, and
the exclusion is effective in the index: the live project's `overview` lists
only `tests src docs . skill scripts .github .githooks`
(`13-overview-live.json`) — no `runs/` and no `eval/` directory is indexed.

### F6 — PASS, with a residue outside the folded files (reranker.py:11-14)

The docstring now advertises exactly what the class does — "symbol-name and
path-token overlap with the query, and a mild penalty for files already
represented >= 2 times" — and nothing else. The class matches it: symbol-token
boost (reranker.py:74, plus the fuzzy match at 76), path-token boost (80),
repeat-file penalty (87);
no path/class penalty exists, so the path term is the token boost.

**Residue (not a fold item, filed as an observation):** `cli.py:975` still
describes `--rerank heuristic` as "token-overlap boosts, path penalties, …".
That is the same class of false label the fold set exists to kill, one surface
further out; it is not in the folded file set and the gate's tip carries it. If
the card reads F6 as "no surface advertises the penalties", this row is a FAIL
and the fix is a one-line help string.

### F7 — PASS (query.md:192-193, core.py:1273-1276)

`query.md`: "The exact-symbol pin of `--mode hybrid` orders within its rung, so
a test-defined exact match can still sit below source." `_fuse_lexical_dense`
says the same (core.py:1272-1276). Probe, query equal to an identifier defined
in both trees (`09-declared-hybrid-pin.txt`, `--mode hybrid`):

```
declared::src/real.py:1-11       shared_name, run_pipeline   2.0000
declared::pyproject.toml:1-6     [project]                   0.2875
declared::spec_e2e/a.py:1-11     shared_name, beta_pipeline_case  2.0000
```

Both exact definitions are pinned at the same fused score, and the source one
returns first — the pin orders inside the rung and the class order decides the
rungs. The docs state exactly that.

### Regression — PASS

- `uv run pytest tests/ -q -W error` → `988 passed, 2 deselected in 104.00s`,
exit 0 (`15-pytest.txt`). The installed-copy digest test is green, as the
butler predicted after the mirror refresh.
- `uvx ruff check src tests scripts` → `All checks passed!`, exit 0 (`17-ruff.txt`).
- `uv run python scripts/publishing_hygiene_lint.py` → run last, after this
file was written (`18-hygiene.txt`).
- Every commit in `b813871..dea142d` signed, `%G? = G` (four commits).
- `skill/references/query.md` = 399 lines / 20982 bytes against the 400-line /
24 KiB budget.

### Row 8 — PASS (the no-eval claim is true)

`pyproject.toml` carries no `[tool.code-indexer.ordering]` table (rg over the
file finds no `[tool.code-indexer` section), and
`ordering.load_class_names('/home/keum/dev/athena/code-indexer') ==
ordering.DEFAULT_CLASS_NAMES` is `True` (`14-class-probe.json`,
`checkout_ladder.checkout.equals_default = true`). This checkout's ladder is the
built-in one, so no path changes class, F2 cannot move its figures and declining
to re-run the hybrid eval is honest. No FAIL 8.

## Verdict

Seven rows PASS, one row (8) PASS, regression PASS. One residue noted under F6
(`cli.py:975`) for routing, outside the folded file set and outside this card's
failing surface.

**VERDICT = PASS** for T-000117.
