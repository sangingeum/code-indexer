# Round 1 re-verify, narrowed — the two repaired prose lines

- Repo: `code-indexer`, branch `main`, tip under test `99d3475` (pushed; `git ls-remote origin refs/heads/main` matches).
- Role: household QA/test engineer.
- Scope: exactly the row that failed the previous narrow re-verify — the row asserting that no
  reader-facing line still sells the removed staleness probe — plus the regression the brief names.
  Nothing else in the round was re-run: not the other seven item rows, not the round's twelve bars.
- Verdict: **PASS. This closes round 1.**

## Item table

| item | verdict | command | exit | observed |
|---|---|---|---|---|
| The retired-probe prose row — the sweep over `src`, `SKILL-CLI.md`, `README.md`, `docs` | **PASS** | `rg -n 'STALE_TTL\|transparently runs a staleness\|staleness is normally enforced\|maybe_refresh' src SKILL-CLI.md README.md docs` | 0 | 7 hits, each read and dispositioned below. The one hit on a reader-facing surface of today (`SKILL-CLI.md:663`) now states the retirement. No hit presents the probe as live behaviour on a surface a reader meets as current. `sweep.txt` |
| `SKILL-CLI.md:663` region is true | **PASS** | quoted below; `rg -n 'stale_ttl\|STALE_TTL' src`; `rg -n '\.stale_ttl\|stale_ttl' src --glob '!config.py'` | 0, 1 | the line says RETIRED, still parsed, consumed by nothing, a read reports from the stat scan and never probes or repairs. Verified against code: `config.py:85` parses it, no other file in `src` mentions it at all, and `Core.staleness` is a pure read. `named_lines.txt`, `stale_ttl_consumers.txt`, `code_anchors.txt` |
| `src/code_indexer/core.py:285` region is true | **PASS** | quoted below; read `core.py:249-292`, `:317-365`; `rg -n '_last_scan' src/` | 0 | the docstring no longer names `maybe_refresh` or a TTL cache; every clause checked against the code it describes (below). `named_lines.txt`, `code_anchors.txt` |
| Regression — suite | **PASS** | `uv run pytest tests/ -q -W error` | 0 | `611 passed, 2 deselected in 76.31s` — ≥ 611 as the brief requires. `pytest.txt` |
| Regression — docs test | **PASS** | `uv run pytest tests/test_docs.py -q -W error` | 0 | `19 passed in 0.34s`. `docs_ruff_hygiene.txt` |
| Regression — ruff | **PASS** | `uvx ruff check src tests scripts` | 0 | `All checks passed!`. `docs_ruff_hygiene.txt` |
| Regression — publishing hygiene lint | **PASS** | `uv run python scripts/publishing_hygiene_lint.py` | 0 | `scanned=535 skipped=0 violations=0 (untracked files are out of scope)`. `docs_ruff_hygiene.txt` |

## The sweep, hit by hit

Run at `99d3475`, full output in `sweep.txt`.

| hit | what it says | disposition |
|---|---|---|
| `SKILL-CLI.md:663` | `STALE_TTL` (RETIRED — still parsed into `Config.stale_ttl` for compatibility and consumed by nothing …) | the reader-facing surface, corrected; true, verified below |
| `docs/subcommand-design.md:263` | "All run `maybe_refresh` first unless skipped (same contract as search)" | a step list inside a design document whose own header states "Status: design (no implementation)" and "Kept as design history"; it describes the plan at design time, not today's code. Historical, acceptable |
| `docs/audit.md:85,89` | a reproduced live measurement of the old behaviour: "`semantic-search` again → the SAME stale range after the edit (the STALE_TTL probe had already run seconds before)" | a dated audit record — opens "Step 0 Audit — code-indexer improvement plan (2026-10-03)", "All numbers were produced by real runs at the time of writing", "committed for the record". Evidence the round acted on, not a claim about today. Acceptable, with the advisory below |
| `docs/overhaul-baseline-audit.md:82` | quotes `Core.maybe_refresh`'s veto to describe the pre-round write path | a baseline audit that names its base revision on line 7 ("Base revision: tip `ef2be78`"); a measurement of the tree before the round. Historical, acceptable |
| `docs/adr/0003-read-only-and-staleness-contract.md:14,25` | the ADR records the pre-round defect it replaces, and states the contract that replaced it ("A read command … calls no `maybe_refresh`") | **excluded by the brief**: a ratified ADR is the record of a decision, not a description of today's surface. Editing it would falsify a decision record. Out of scope, named and not counted |

Also excluded, same reason and likewise named rather than silently skipped: the round records under
`runs/` are historical and outside the graph the sweep walks (`src`, `SKILL-CLI.md`, `README.md`,
`docs`). No `README.md` hit remains — the "runs a staleness check + incremental indexing first"
sentence is gone, and the briefed sweep over `README.md` returned nothing.

## The two named lines, quoted, and the code behind them

### `SKILL-CLI.md:662-666`, in the environment-variable list

```
  `OLLAMA_URL`, `QDRANT_URL`, `EMBED_MODEL`, `INDEX_ROOT`
  (default `~/.code-indexer`), `STALE_TTL` (RETIRED — still parsed into
  `Config.stale_ttl` for compatibility and consumed by nothing: a read
  reports its verdict from the stat scan and never probes or repairs, so the
  TTL governs no behaviour; repair is `--refresh` or a pass command),
```

Checked clause by clause:

- **"still parsed into `Config.stale_ttl`"** — `config.py:85` (`stale_ttl=int(cfg["stale_ttl"])`),
  fed from `_DEFAULTS["stale_ttl"]` at `:17`, itself read from the `STALE_TTL` environment
  variable by the generic `key.upper()` lookup at `:54`.
- **"consumed by nothing"** — `rg -n 'stale_ttl|STALE_TTL' src` returns exactly three lines, all in
  `config.py` (the default, the dataclass field `:35`, and the parse at `:85`).
  `rg -n '\.stale_ttl|stale_ttl' src --glob '!config.py'` exits **1** (no match). Nothing reads the
  field. The test fixtures pass `stale_ttl=…` only because the dataclass field exists.
- **"a read reports its verdict from the stat scan and never probes or repairs"** —
  `Core.staleness` (`core.py:317`) walks the tree with the scanner's include/exclude rules and stat
  fast-path, hashes only files whose stat identity changed, contacts no service, and returns a
  verdict; it opens no write transaction and calls no pass. The write path is `run_index`, reached
  only from `refresh`/`watch_pass`/pass commands.
- **"repair is `--refresh` or a pass command"** — `Core.refresh` (`core.py:294`) is the explicit
  pass; `--refresh` is the flag that reaches it.

So the line is accurate: the variable is accepted, reaches a field, and governs no behaviour — and
it says so where a reader meets it.

### `src/code_indexer/core.py:281-292`, the watcher-pass docstring

```
    def watch_pass(self, slug: str, project_path: str) -> dict[str, Any]:
        """One watcher pass: an incremental index under the per-project flock.

        The same core op ``refresh`` calls (run_index with force=False); the
        difference is the caller, not the work. The watch loop runs this every
        tick and gets a held lock back as a return state, where an explicit
        ``--refresh`` raises LockedError. Cheap when nothing changed (stat and
        hash diff against the manifest; embeddings happen only for real
        changes) and cached by no clock: a tick after an edit does that edit's
        work.
        """
        return self.run_index(slug, project_path, force=False)
```

Checked clause by clause:

- **"the same core op `refresh` calls (run_index with force=False)"** — `watch_pass` returns
  `self.run_index(slug, project_path, force=False)`; `refresh` calls
  `self.run_index(entry.slug, entry.path, force=False, verbose=True)` (`core.py:302-303`). Same op,
  same lock, same pass; only `verbose` and the caller differ.
- **"the watch loop runs this every tick and gets a held lock back as a return state"** —
  `watcher.py:520` calls `self.core.watch_pass(slug, path)` on a pass, and `run_index` returns
  `{"state": "indexing", "detail": "indexing in progress"}` when the flock is not acquired
  (`core.py:260-261`) instead of raising.
- **"where an explicit `--refresh` raises LockedError"** — `refresh` inspects that same return:
  `if result.get("state") == "indexing": raise LockedError("indexing in progress")` (`core.py:305-306`).
- **"cheap when nothing changed (stat and hash diff against the manifest; embeddings happen only for
  real changes)"** — the pass is `indexer.index_project` with `force_full=False`, the incremental
  path already covered by the read contract; unchanged files are not re-embedded.
- **"cached by no clock"** — `_last_scan` is written at `core.py:269` and `:585` and **read nowhere**
  (`rg -n '_last_scan' src/` returns the init at `:126` and those two assignments only). No TTL gate
  stands between a tick and the pass.
- **`maybe_refresh` / "the STALE_TTL cache"** — gone from the docstring; `rg -n 'maybe_refresh' src`
  returns nothing.

## What this row does not license

Two deferred items, both already named by the repair record and neither a condition of this row:

- **The dead field still exists.** `Config.stale_ttl`, its `_DEFAULTS` entry and its parse remain,
  and every test fixture passes `stale_ttl=`. Deleting them is a fixture-wide cleanup, documented as
  retired in both places a reader meets them. Not a defect of truth — the prose now matches the code.
- **`docs/audit.md`'s stale section reads in the present tense.** It is a dated record
  ("2026-10-03", "at the time of writing", "committed for the record") and the sweep's rule treats
  historical records as acceptable, but a reader who lands on that section without the header could
  read it as current. If the round wants zero present-tense residue, that is an edit to a record
  document — an owner call, not a QA edit.

## Regression detail

Both commands were run once at `99d3475` with full output redirected, then read from the file:

- `uv run pytest tests/ -q -W error` → `611 passed, 2 deselected in 76.31s`, exit 0. Same count as
  the previous legs; no test added, removed or skipped by the repair.
- `uv run pytest tests/test_docs.py -q -W error` → `19 passed`, exit 0.
- `uvx ruff check src tests scripts` → `All checks passed!`, exit 0.
- `uv run python scripts/publishing_hygiene_lint.py` → `scanned=535 skipped=0 violations=0`, exit 0.

## What I did not re-check

- The other seven item rows of the narrow re-verify (the fresh semantic install, the core-only
  install, the skill's exit-4 statement, the unknown verdict's manifest fields, `mode="dense"`
  reaching the CLI, the per-project single staleness walk, the README residual). They passed at
  `695a4a7` and this repair touches two prose lines; they were not re-run.
- The round's twelve bars at `f7d63f1` — ruled there, not re-run here.
- Nothing was fixed: no source, doc, test or another agent's record was edited by this leg.

## Evidence produced by this leg

All under `runs/round1-reverify-ttl/`: `sweep.txt` (the briefed sweep, hit by hit), `named_lines.txt`
(both repaired regions, quoted with line numbers), `stale_ttl_consumers.txt` (the only mentions in
`src`, plus the absence of any consumer outside `config.py`), `consumers_and_docs_test.txt` (the
docs test's env-var mirror, which is why the name must stay documented while the parse remains),
`code_anchors.txt` (run_index / watch_pass / refresh / staleness anchors, the watcher call site and
the baseline-audit header), `pytest.txt`, `docs_ruff_hygiene.txt`.
