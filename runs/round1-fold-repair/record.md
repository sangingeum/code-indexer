# Round 1 fold repair — the two stale TTL lines

- Base tip: `34a5c09`. The narrow re-verify passed seven of the nine folds and failed one row
  (FOLD-1/2/3/7) on two lines that still describe the staleness TTL probe the round removed.
- This leg repairs those two lines, sweeps for the same defect class, and lands the regression.

## The two failing lines

| file:line | before | after |
|---|---|---|
| `SKILL-CLI.md:663` | `` `STALE_TTL` (default 60 s, staleness probe), `` — listed as a live environment variable that governs a probe | `` `STALE_TTL` (RETIRED — still parsed into `Config.stale_ttl` for compatibility and consumed by nothing: a read reports its verdict from the stat scan and never probes or repairs, so the TTL governs no behaviour; repair is `--refresh` or a pass command), `` |
| `src/code_indexer/core.py` (`watch_pass` docstring, the cited line 285) | "One watcher pass: staleness probe + incremental index under the flock. Same core op as `maybe_refresh`'s terminal call (run_index with force=False), but unconditioned by the STALE_TTL cache — the watch loop calls this every tick." | "One watcher pass: an incremental index under the per-project flock. The same core op `refresh` calls (run_index with force=False); the difference is the caller, not the work. The watch loop runs this every tick and gets a held lock back as a return state, where an explicit `--refresh` raises `LockedError`. Cheap when nothing changed (stat and hash diff against the manifest; embeddings happen only for real changes) and cached by no clock: a tick after an edit does that edit's work." |

The docstring no longer names a function that does not exist in `src/`, and no longer implies a
cache that no longer exists. What it says now is what the code does: the same pass `refresh` runs,
under the same flock, driven by the watcher instead of a flag.

## Two further lines in the same class, repaired with them

These were not in the re-verify's list; they are the same defect (prose that claims the retired
probe) and the sweep's instruction is to fix the class, so they are named here rather than slipped in.

| file:line | before | after |
|---|---|---|
| `src/code_indexer/config.py:17` | `"stale_ttl": "60",  # seconds; staleness re-scan interval` | `"stale_ttl": "60",  # RETIRED: parsed into Config.stale_ttl, consumed by nothing (reads never probe)` |
| `tests/test_stale_safe_context.py:6` (module docstring) | "line-mode stale flag with the documented stderr warning and exit 0; `--fresh` bypasses STALE_TTL." | "… exit 0; `--fresh` reads the live file instead of the indexed rows." |

## The sweep

Briefed command, run at the repaired tree:

```
$ rg -n 'STALE_TTL|transparently runs a staleness|staleness is normally enforced|maybe_refresh' src SKILL-CLI.md README.md docs
```

Full result, hit by hit, with the disposition of each:

| hit | disposition |
|---|---|
| `SKILL-CLI.md:663` | **fixed** — now states the retirement (above) |
| `docs/audit.md:85,89` | left — a reproduced live measurement of the OLD behaviour at its base revision ("the STALE_TTL probe had already run seconds before"); it is the evidence the round acted on, not a claim about today |
| `docs/overhaul-baseline-audit.md:82` | left — same: a baseline measurement, opening with "Base revision: tip `ef2be78` … decides nothing and changes nothing" |
| `docs/subcommand-design.md:263` | left — the document's own header says "design history … Kept as design history" |
| `docs/adr/0003-read-only-and-staleness-contract.md:14,25` | left — a ratified ADR records the contract; section 14 describes the pre-round defect the ADR replaces |

A widened sweep (`stale_ttl|ttl|staleness probe|probe cache`, case-insensitive, over `src`, the
skill, the README, the CHANGELOG and `docs/*.md`) found no further claim of the old behaviour. The
remaining hits are the retired-flag lines written here, the historical records above, and
`CHANGELOG.md:44`, which says the implicit `STALE_TTL` refresh **is gone** — true, and the place a
reader looks for exactly that.

`README.md` needed nothing: the sentence that told agents `semantic-search` "transparently runs a
staleness check + incremental indexing first" was corrected upstream at `695a4a7`, before this leg.

## Regression

Run once each at the repaired tree:

- `uv run pytest tests/ -q -W error` → **611 passed, 2 deselected** (no change in count; no test was
  added, removed or skipped).
- `uv run pytest tests/test_docs.py -q -W error` → **19 passed**.
- `uvx ruff check src tests scripts` → **All checks passed!**
- `uv run python scripts/publishing_hygiene_lint.py` → **`scanned=530 skipped=0 violations=0`**.

Captures: `runs/round1-fold-repair/{pytest,docs,ruff,hygiene}.txt`.

## Deliberately left

- **Deleting `Config.stale_ttl`.** The field and its `STALE_TTL` parse still exist; every test that
  builds a `Config` passes `stale_ttl=...`, so deleting it is a fixture-wide edit that the re-verify
  itself deferred. It is now documented as retired in the two places a reader meets it (the default
  table's comment and the skill's environment-variable list) instead of reading as a live knob — a
  documented retirement beats a silently dead flag. The cleanup card is: drop the field, the
  `_DEFAULTS` entry, the parse, and the `stale_ttl=` arguments in the test fixtures.
- **The `STALE_TTL` name itself.** It stays in the documented environment list because the code still
  parses it and `tests/test_docs.py` asserts the documented set mirrors the code; removing the name
  before removing the parse would document a variable the code still reads.
- **`tests/test_concurrency.py:84`** sets `STALE_TTL=0` in one fixture. It is inert now (nothing reads
  the value) and harmless, and it belongs to the same deferred cleanup rather than to this repair.
- **Ratified ADRs, the round records and the historical audits** — never edited; they are records of
  what was true when they were written.
