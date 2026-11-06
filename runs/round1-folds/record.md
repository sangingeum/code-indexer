# Round 1 folds

- Base tip: `4e0c616`; this leg applies the nine corrections the round-1 two-axis review tabulated,
  and nothing else.
- Carried by three commits: `2669a47` (core read contract, the skill's exit-code correction, the
  unknown-verdict fields), `73be58f` (the CLI surfaces and the MCP mode relay), and the packaging
  commit that lands this record (`pyproject.toml`, `uv.lock`, `runs/round1-folds/`).

Every `file:line` in the review's table was checked against the tree at `4e0c616` before editing. All
nine still pointed at the described code; none had moved, so no change was applied at a different
site than the table names.

## Per-fold disposition

| fold | disposition | before | after | commit |
|---|---|---|---|---|
| FOLD-1 | applied | `cli.py` module docstring: "staleness is normally enforced by the core's `STALE_TTL` probe guarded by the per-project flock (concurrent invocations serialize; exactly one indexer pass runs)" | "Reads never refresh: an answer describes the index as of `meta.last_indexed` and reports `fresh`, `stale` or `unknown`. `--skip-stale-check` is accepted and inert; `--refresh` is the only flag that makes a read write, and it takes the per-project flock." | `73be58f` |
| FOLD-2 | applied | `core.py` module docstring: "no watcher on the default path — staleness is enforced via STALE_TTL probes on search/status, guarded by the per-project flock"; plus `STALE_TTL_DEFAULT = 60` at `core.py:42` | docstring states the read contract ("Reads detect staleness, never repair it … `unknown`, the last when the check cannot run. Only an explicit refresh writes."); the constant is deleted — it had no reference in `src/` | `2669a47` |
| FOLD-3 | applied | app tagline: "Semantic code index via Ollama + Qdrant (one-shot CLI; no daemon)." | "Code intelligence for agents: index a repository into SQLite and answer structural, lexical and optional semantic queries (one-shot CLI; no daemon)." — true of a core install | `73be58f` |
| FOLD-4 | applied (blocking, cleared) | `SKILL-CLI.md:477`: "`ConfigError` naming the difference (one line on stderr, exit 1)" | "`RebuildRequiredError` naming the difference (one line on stderr, exit 4)" | `2669a47` |
| FOLD-5 | applied | `_read_staleness` returned only the JSON block and printed the line itself; `semantic-search` then rebuilt the same line from a second `core.staleness()` per project (`cli.py:845-846` and `:858` at the base) | both helpers return the block(s) **and** the warning line(s) they were built from (single: `(block, line)`; fan-out: `(aggregate, blocks, lines)`); `semantic-search` prints the returned lines. One walk per project on every path | `73be58f` |
| FOLD-6 | applied | `server.py:304`: `if mode != "dense":` — a client asking for `dense` silently got the CLI's hybrid default | `if mode:` — `--mode dense` reaches argv | `73be58f` |
| FOLD-7 | applied | `RefreshOpt` help: "Run one incremental index pass first, then answer from the result." | "Run an incremental index pass first (one per project for `--all-projects` / `doctor`), then answer from the result." (the flock and single-writer sentences are unchanged) | `73be58f` |
| FOLD-8 | applied | `Core.staleness` returned at the path-absent check before opening the manifest, so `index-status --json` reported `last_indexed: null` / `index_revision: null` for a project whose manifest held both | the two meta fields are read first (when a manifest exists) and passed to every `unknown` verdict this path builds: path absent, no manifest, no completed pass, scan budget, unreadable tree | `2669a47` |
| FOLD-9 | applied (blocking, cleared) | `semantic` extra listed `ollama` and `qdrant-client` only; the comment said numpy was deliberately unlisted and left the constraint to the lock, so a fresh resolve took numpy 2.5.4 and `import qdrant_client` raised the baseline-optimization `RuntimeError` | `numpy<2` is a declared dependency of the `semantic` extra, the comment now says why (1.26.4 is the only numpy whose x86 baseline imports on this host; 2.x import-fails before any query runs), and `uv.lock` was refreshed. The core extra still declares no numpy | packaging commit |

No fold was found "already true" and none was "not applicable".

One adjacent defect the table does not list, **observed and deliberately left alone**: `README.md:14`
still tells agents that `semantic-search` "transparently runs a staleness check + incremental indexing
first", which is the behaviour this round removed. It is not in the folds table, so it is reported here
for the owner to card rather than fixed in this leg.

## FOLD-9 — the fresh-environment proof

A fresh resolve into an empty environment, with the `semantic` extra, from the tree that carries the
change. (`--clear` is used because the path had been created by a first attempt: the resolve below is
still into an empty environment, which is what the bar measures.) The probe is the documented inline
import expressed as a script file, because an unattended session cannot run an inline `-c` command;
the script lives at `runs/round1-folds/fold9_probe.py`.

```
$ uv venv --clear /tmp/fold-sem
Using CPython 3.12.12
Creating virtual environment at: /tmp/fold-sem

$ uv pip install --python /tmp/fold-sem/bin/python -e ".[semantic]"
Resolved 33 packages in 7ms
   Building code-indexer @ file://<repo>
Installed 33 packages in 12ms
 + code-indexer==0.1.0 (from file://<repo>)
 + numpy==1.26.4
 + ollama==0.6.3
 + qdrant-client==1.14.3
 ... (remaining packages omitted)

$ /tmp/fold-sem/bin/python <repo>/runs/round1-folds/fold9_probe.py   # import qdrant_client; print("ok")
ok
numpy=1.26.4 qdrant_client=1.14.3
PROBE_EXIT=0

$ uv pip list --python /tmp/fold-sem/bin/python | grep numpy
numpy                     1.26.4
```

The bar is met: the import prints `ok` and the resolver picks 1.26.x, not 2.x. The full transcript,
including the complete package list, is `runs/round1-folds/fold9-fresh-venv.txt`.

Why the repository suite could not have proven this: the failing path is the resolver plus an import
in a fresh environment. The suite imports from the working tree's already-resolved environment, which
held numpy 1.26.4 throughout; only a new environment exercises what an install would get.

## Tests added or extended per behaviour fold

| fold | test | what it asserts |
|---|---|---|
| FOLD-5 | `tests/test_read_contract.py::test_a_query_read_computes_one_staleness_verdict_per_project` | for both the scoped and the `--all-projects` shape of `semantic-search` on a stale project, the count of `core.staleness` calls equals the project count (one), and the warning line still prints — a call count, not a timing |
| FOLD-6 | `tests/test_mcp_server.py::test_dense_mode_reaches_the_cli` | `semantic_search("q", mode="dense")` builds argv `["semantic-search", "q", "--limit", "8", "--mode", "dense"]` through the stubbed runner seam — the argv, not the tool's prose |
| FOLD-6 | `tests/test_mcp_server.py::MAPPING_CASES[semantic_search_full]` | extended: the parametrized argv expectation wrongly encoded the defect (no `--mode` for `dense`) and now expects `--mode dense` |
| FOLD-8 | `tests/test_query_staleness.py::test_absent_project_path_is_unknown_not_fresh` | extended: with the project tree removed, the `unknown` verdict's block carries `last_indexed` and `index_revision` from the manifest as well as `stale_known: false` |

The doc folds (FOLD-1/2/3/4/7) are text; `tests/test_docs.py` pins none of the changed strings (it
checks the command and flag inventory and the meaning words "inert" / `--skip-stale-check`, all of
which the new text keeps), and it stayed green.

## Verification at the tip

- `uv run pytest tests/ -q -W error` → **611 passed, 2 deselected** (609 at the base plus the two new
tests; the extended tests are not new items).
- `uvx ruff check src tests scripts` → **All checks passed!**
- `uv run python scripts/publishing_hygiene_lint.py` → **`scanned=496 skipped=0 violations=0`**.
- The fresh-environment proof above.

## Not touched, deliberately

- **Row surfaces answering from a needs-reindex index (the gate's row-surface finding)** — deferred to
  round 2 by the review, owned there as an acceptance criterion. Not this leg.
- **`doctor` probe wall and streaming** — filed as issue `#30`; not this leg.
- **ADR-0007 section 1's removal list** (it omits one of the eleven removed tools) — an owner call,
  carded separately. A ratified ADR is never edited by a fix leg.
- **The gate record's exit-4 row label and its bar labels** — the gate owner corrects their own record.
- **The round-1 review record** — not edited; this leg writes only its own record and its captures.
- **`README.md:14`** — the stale "runs a staleness check + incremental indexing first" sentence is
  reported above, not fixed: it is outside the nine folds.
