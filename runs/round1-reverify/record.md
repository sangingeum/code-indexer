# Round 1 folds — narrow re-verification

- Card: the narrow re-verify card for the round-1 folds (identifier withheld: this record is an
  authored, hygiene-governed surface)
- Repo: `code-indexer`, branch `main`, tip under test `695a4a7` (round-1 fold commits `2669a47`,
  `73be58f`, `a86c7b9`, `695a4a7`)
- Role: household QA/test engineer
- Scope: the nine folds the two-axis review tabulated, the README residual the fold leg reported,
  and the correction this gate owed its own round-1 gate record. The round's twelve bars were ruled
  at `f7d63f1` and are not re-run here.
- Verdict: **FAIL**, narrowly scoped — seven of the eight item rows pass; FOLD-1/2/3/7 fails on two
  surviving lines that still describe the removed TTL probe. Neither blocking fold (FOLD-9, FOLD-4)
  fails, no behaviour regressed, and the remedy is two text edits. The full suite, the docs test,
  ruff and the publishing lint are green at the tip.

## Item table

| item | verdict | command | exit | observed |
|---|---|---|---|---|
| FOLD-9 (blocking) — a fresh semantic install imports its client | PASS | `uv venv --clear /tmp/rv-sem`; `uv pip install --python /tmp/rv-sem/bin/python -e ".[semantic]"`; `/tmp/rv-sem/bin/python runs/round1-reverify/semantic_import_probe.py` | 0, 0, 0 | fresh environment, 33 packages resolved; `numpy==1.26.4`, `qdrant-client==1.14.3`; the probe printed `numpy=1.26.4`, `qdrant_client=imported`, `PROBE_OK`; `uv pip list` → `numpy 1.26.4`. `fold9_semantic_venv.txt`, `semantic_import_probe.py` |
| FOLD-9 (blocking) — a fresh core-only install answers without the extra | PASS | `uv venv --clear /tmp/rv-core`; `uv pip install --python /tmp/rv-core/bin/python -e .`; `overview --project code-indexer --max-lines 8`; `semantic-search staleness --project code-indexer` | 0, 0, 0, 3 | 12 packages, no numpy/ollama/qdrant; `overview` answers (`609 files, 60488 LOC`) with exit 0; `semantic-search` → exit 3, **0 bytes** on stdout, one line `ConfigError: the semantic extension is not installed; install code-indexer[semantic]`. `fold9_core_venv.txt`, `fold9_core_semantic_stderr.txt` |
| FOLD-4 (blocking) — the skill states the code that ships | PASS | `rg -n -B2 -A4 'RebuildRequiredError' SKILL-CLI.md`; `code-indexer semantic-search staleness --project liveproj` | 0, **4** | `SKILL-CLI.md:477` reads "`RebuildRequiredError` naming the difference (one line on stderr, exit 4)"; the live refusal on the `needs-reindex` project is exit **4**, `stdout_bytes=0`, one `RebuildRequiredError: index for … (extraction_version: 1 -> 4); run: code-indexer reindex-project …` line. `fold4_skill_and_pyproject.txt`, `fold4_exit4.txt` |
| FOLD-8 — an `unknown` verdict still reports the manifest's pass fields | PASS | `code-indexer index-status --project liveproj --json` (registered project whose path is absent) | 0 | `stale_known: false`, `stale: false`, and `last_indexed: "2026-10-03T11:43:17Z"`, `index_revision: "1791027797.5572622"` — both non-null from the manifest. `tests/test_query_staleness.py::test_absent_project_path_is_unknown_not_fresh` passes (2 passed with the FOLD-5 test). `fold8_unknown_fields.txt`, `fold5_fold8_tests.txt` |
| FOLD-6 — `mode="dense"` reaches the CLI | PASS | `uv run pytest tests/test_mcp_server.py::test_dense_mode_reaches_the_cli -q -W error`; `uv run python runs/round1-reverify/dense_argv_probe.py` | 0, 0 | the wrapper builds argv at `src/code_indexer/server.py:304-305` under `if mode:`; the independent probe read the argv for each mode: `dense -> ["semantic-search", "q", "--limit", "8", "--mode", "dense"]`, `hybrid -> … "--mode", "hybrid"`, `lexical -> … "--mode", "lexical"`. Test: 1 passed. `fold6_dense_test.txt`, `fold6_argv_probe.txt` |
| FOLD-5 — one staleness walk per project on a fan-out | PASS (row) with a residual, below | `uv run pytest tests/test_read_contract.py::test_a_query_read_computes_one_staleness_verdict_per_project -q -W error`; read `cli.py:313-365`, `:837-856`, `:879` | 0 | the helpers return the line they built (`(block, line)`; `(aggregate, blocks, lines)`), `semantic-search` prints those lines at `:879` instead of recomputing, and the call-count test asserts `seen == [entry.path]` for both the scoped and the `--all-projects` shape — 2 passed. `fold5_fold8_tests.txt` |
| FOLD-1/2/3/7 — no docstring, help text or skill line still describes the removed TTL probe | **FAIL** | `rg -n 'STALE_TTL\|transparently runs a staleness\|staleness is normally enforced' src SKILL-CLI.md README.md`; `code-indexer --help`; `code-indexer semantic-search --help`; `rg -n 'STALE_TTL_DEFAULT'` | 0, 0, 0, 1 | the four named surfaces are corrected (CLI tagline is the new "Code intelligence for agents: …" line; `--refresh` help says "one per project for --all-projects / doctor"; `STALE_TTL_DEFAULT` is gone), but **two lines still describe the removed probe**: `SKILL-CLI.md:663` lists `` `STALE_TTL` (default 60 s, staleness probe) `` as a live environment variable, and `src/code_indexer/core.py:285` says the watch pass is "unconditioned by the STALE_TTL cache" and cites `maybe_refresh`'s terminal call — a function that no longer exists anywhere in `src/`. `docs_residue.txt`, `help_tagline_refresh.txt`, `stale_ttl_residue_detail.txt` |
| README residual — no implicit refresh | PASS | `rg -n 'incremental indexing first' README.md` | **1** | no match: the "runs a staleness check + incremental indexing first" sentence is gone. `docs_residue.txt` |
| suite/docs — nothing regressed | PASS | `uv run pytest tests/ -q -W error`; `uv run pytest tests/test_docs.py -q -W error`; `uvx ruff check src tests scripts`; `uv run python scripts/publishing_hygiene_lint.py` | 0, 0, 0, 0 | `611 passed, 2 deselected in 75.72s`; docs test `19 passed`; ruff `All checks passed!`; hygiene `scanned=508 skipped=0 violations=0`. `suite.txt`, `docs_ruff_hygiene.txt` |

## The finding behind the FAIL — two lines that still sell the removed probe

Neither line is behaviour: no code reads `Config.stale_ttl` (its only mentions are its own default and
constructor assignment; every test passes it because the dataclass field exists), and the watch pass
runs exactly as it did before the round. What is wrong is the prose, and it is the same class the
review tabulated as FOLD-1/FOLD-2 — which is why the row fails rather than passing on the four named
sites:

1. `SKILL-CLI.md:663`, in the environment-variable list: `` `STALE_TTL` (default 60 s, staleness
   probe) ``. Nothing probes on a TTL. A reader following the skill sets a knob that does nothing.
2. `src/code_indexer/core.py:285`, in `watch_pass`'s docstring: "Same core op as maybe_refresh's
   terminal call (run_index with force=False), but unconditioned by the STALE_TTL cache".
   `maybe_refresh` was removed in this round and survives only in these words; "the STALE_TTL cache"
   does not exist.

Smallest honest remedy (owner: the dev leg that owns the read contract, then the skill's owner):
reword the skill line to say the variable is accepted and inert, and reword the `watch_pass`
docstring to describe what the watch loop does (an incremental pass per tick, cheap when nothing
changed) without the removed cache and function. Removing the dead `stale_ttl` field touches every
test that constructs `Config`; that is a cleanup for a later leg, not a condition of this row.
Note for the docs bar: `tests/test_docs.py` pins the command and flag inventory but not the env-var
list's meanings, which is how both lines survived a green suite.

## A second, non-blocking finding — one read still walks its project twice

FOLD-5's own prescription is met (the helpers return their line and the fan-out prints it), but the
same defect class survives one call site away: `get-code-context` discards the line
(`block, _ = _read_staleness(...)`, `cli.py:1830`) and then recomputes the verdict to print it
(`cli.py:1842`). Measured, not assumed: `runs/round1-reverify/walk_count_probe.py` wraps
`core.staleness` with a counter and invokes the command through the CLI on a fresh project —
`EXIT=0`, `WALKS=2`. The other direct call sites are `_read_staleness:331` and
`_read_staleness_many:357`, both single-walk by construction, so this is the only survivor.
Single-project, text mode only; no fan-out cost. Owner: the read-contract leg; round 2 is the natural
home, with a call-count test in the shape of the FOLD-5 one (`fold5_walk_count_probe.txt`).

## The gate-record correction I made

I corrected my own record, `runs/round1-gate/record.md`, in commit `0164c38` (signed, `G`), and
renamed that folder's capture paths in the same commit. No other record, ADR or code file was
touched.

- **The exit-taxonomy row.** The folder's only capture labelled for code 4 was
  `find-symbol Core --project liveproj` — which exits **0** and prints `no symbols matching 'Core'`.
  That is a demonstration of the deferred row-surface finding (F-02), not of code 4. The capture is
  renamed `exit-taxonomy/exit0-row-surface-on-needs-reindex.*`, and the bar's code-4 row now cites a
  fresh search-path refusal captured while correcting the record
  (`exit-taxonomy/exit4-search-refusal.txt`: exit **4**, `stdout_bytes=0`, one
  `RebuildRequiredError:` line). The bar's verdict stays PASS, and now rests on evidence it names.
- **Bar labels as concepts.** The twelve bars are now styled by concept ("the pytest bar",
  "the service-dial bar", "the exit-taxonomy bar", "the refresh-and-flock bar", …) and the capture
  paths are renamed from their coordinate labels (`pytest-bar.txt`, `exit-taxonomy/`,
  `service-dial-transcript.txt`, `staleness-reporting/`, `doctor-probe/`, …). The record carries a
  mapping table as its Evidence index; finding ids survive only as inline provenance in the finding
  headings, because other legs cite them.
- I also recorded, inside the old findings' bodies, that the numpy-resolution and unknown-verdict
  findings are now corrected — both of which this leg re-measured above.

## Anything still broken

1. `SKILL-CLI.md:663` and `src/code_indexer/core.py:285` — the two lines above. Owner: the
   read-contract dev leg (code comment) and the skill's owner (env list). Not blocking a behaviour
   bar; blocks this row.
2. `get-code-context` text mode — two staleness walks per single-project read (`cli.py:1830`,
   `:1842`). Owner: the read-contract leg; round 2.
3. Unchanged from the gate and the review, not re-verified here: the row surfaces answering from a
   `needs-reindex` index (round 2's register), `doctor`'s ~186 s blackhole wait (filed as an issue),
   the record's removal list being one short (an ADR owner call).

## What I did not re-check

- **The round's twelve bars** at `f7d63f1`. They were ruled there; this leg re-ran only the suite,
  the docs test, ruff and the publishing lint as regression evidence at `695a4a7`.
- **Rounds 2–5 surfaces**: chunks/vectors-off, the three version axes and per-side fingerprints,
  symbol identity and structural filters, the plan lifecycle, the provider surface.
- **The `[mcp]` install surface** beyond the core-only and `[semantic]` venvs the row names.
- **Live-service behaviour**: Ollama/Qdrant were reachable, so no offline re-run of the query-cost or
  service-dial bars was made here.

## Evidence produced by this leg

`suite.txt` (611 passed, 2 deselected), `docs_ruff_hygiene.txt` (19 passed; ruff clean;
`scanned=508 violations=0`), `fold9_semantic_venv.txt`, `fold9_core_venv.txt`,
`fold9_core_semantic_{stdout,stderr}.txt`, `fold4_skill_and_pyproject.txt`, `fold4_exit4.txt`,
`fold8_unknown_fields.txt`, `fold6_dense_test.txt`, `fold6_argv_probe.txt`, `fold5_fold8_tests.txt`,
`fold5_walk_count_probe.txt`, `docs_residue.txt`, `help_tagline_refresh.txt`,
`stale_ttl_residue_detail.txt`, and the probe scripts `semantic_import_probe.py`,
`dense_argv_probe.py`, `walk_count_probe.py`.

Host-specific path text in the captures is elided (`<checkout>`), and the publishing lint over the
committed tree reports `violations=0`.
