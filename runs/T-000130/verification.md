# Gate: the staleness banner became a stateless count

- Repo: `<checkout>`
- Base `7bccc54` -> candidate tip `7704364` (pushed)
- Gate: independent QA, re-measured from scratch; the leg's captures were
treated as claims
- Date: 2026-11-07 · **Verdict: FAIL** (bars 6 and 7)

Worktrees: `base` = 7bccc54, `tip` = 7704364, each with
`uv sync --frozen --all-extras`. Fixtures under `sandbox/`: one index root
built by the tip binary and read by BOTH binaries, so base and tip answer from
the same manifest. Every read is a true fresh process: `env -i INDEX_ROOT=...`
with nothing else in the environment.

Paths in the captures are redacted at publish time (`<scratch>` = the throwaway
root, `<checkout>` = this repo, `<venv>` = a worktree's virtualenv); the byte
counts quoted below were measured on the raw output before that substitution.

## Bar 1 — the repeat is gone: PASS

Stale project (`proj`: 2 files indexed, `gamma.py` added afterwards).

- base `7bccc54`, 5 fresh reads (`captures/base-five.txt`): each read prints the
  same **222-byte paragraph**, exit 0 —
  `Warning: index is stale (1 file changed since 2026-11-07T10:27:05Z: 1 added); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>`
- tip `7704364`, 5 fresh reads (`captures/tip-five.txt`): each read prints the
  same **212-byte single line**, exit 0 —
  `Warning: index is stale: 1 added file -- <path>; refresh with code-indexer reindex-project <path>`

Byte-identical across all five at the tip (`len(set(lines)) == 1`). A sixth read
with no token in the environment prints the same line. A three-kind project
(`proj5`: 1 modified, 1 added, 1 deleted) prints
`Warning: index is stale: 1 modified file, 1 added file, 1 deleted file -- <path>; ...`
identically on both reads (`captures/multikind.txt`), so the per-kind count,
its pluralisation and its fixed order are real, not a one-reason artifact.

## Bar 2 — statelessness proved: PASS

Two shells, two environments, one index:

- shell A (`env -i INDEX_ROOT=...`, cwd = checkout): 212-byte line.
- shell B (cwd `/tmp`, `env -i INDEX_ROOT=... TZ=UTC-07 LANG=C TERM=dumb
  SHELL=/bin/sh CODE_INDEXER_STALE_TOKEN=nonsense-<pid>`): the **same 212
  bytes**, exit 0 (`captures/tip-shellA.txt`, `tip-shellB.txt`).

State written by reads — measured, not asserted (`captures/snap-before.txt` /
`snap-after.txt`, 10 reads: 5 JSON, 5 text):

- file set identical, every file's size identical, only
  `1c3d53d4/manifest.db-shm` (the SQLite shared-memory index) advances its
  mtime. `manifest.db` and `manifest.db-wal` are unchanged —
  `1c556a2776a6da7b9926f098e1c16a09f608cb49ee7546a78b3659dc0e831a0a` and
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
  (empty) before and after.
- no file anywhere in the sandbox home, index root or project tree named
  like a marker (`*marker*`, `*session*`, `*seen*`, `*warn*`, `*stamp*`): none.

Code, read at the tip:

- `src/code_indexer/staleness.py`: the only `open(` is a **read** of a
  candidate file's prefix (`:201`); `warning_line` takes the verdict and a
  path and returns a string — it records nothing.
- `src/code_indexer/core.py:506` `staleness()` opens the manifest
  `writable=False` and walks the tree; no write branch.
- `src/code_indexer/cli.py:391` `_read_staleness` writes only under
  `if refresh:` (`:412`).
- `rg 'os\.environ|getenv' src/code_indexer/staleness.py src/code_indexer/cli.py`
  — **no hits**: no session token variable exists on either path.

No state file, no manifest column, no mtime heuristic, no suppressed first
read: read 1 and read 5 print the same bytes.

## Bar 3 — JSON completeness unchanged: PASS

Same stale project, `--json` on five reads at base and five at the tip from the
same manifest. The staleness block is **10 fields, byte-identical to base on
every read** (332 bytes sorted), and the whole JSON stdout is identical on all
five (`captures/cmp-base-*.json` vs `cmp-tip-*.json`):

```
index_revision=1  last_indexed=2026-11-07T10:27:05Z  stale=true
stale_known=true  stale_reason_count=1
stale_reasons=[{kind: added, file: gamma.py}]
withheld_count=0  withheld_files=[]  withheld_summary=null
withheld_unexamined_count=0
```

No key added, removed or re-typed. The three-kind project carries all three
reasons with their kinds and files (`captures/multikind.txt`, JSON block).

## Bar 4 — fresh silent, unknown speaks: PASS

- fresh `proj3`: `overview` exit 0, **stderr 0 bytes** (`captures/tip-fresh.txt`).
- unknown `proj4` (its `manifest.db` removed): exit 0, one 123-byte line —
  `Warning: index staleness check did not run (no index manifest for this project); answers describe the index, not the tree.`
  (`captures/tip-unknown.txt`).

## Bar 5 — double fault: PASS

`proj2` is stale (`extra.py` added) and holds one withheld file (`notes.md`,
sensitive by content).

- `overview` stderr, one 250-byte line, two distinct clauses (`tip-double-text.txt`):
  `Warning: index is stale: 1 added file; 1 file skipped (sensitive content) -- <path>; refresh with code-indexer reindex-project <path>`
- `index-status` (`tip-double-status.txt`): stderr carries the count only, stdout
  carries `1 file skipped (sensitive content) — privacy; ...`. A count of
  `skipped (sensitive content)` across stdout+stderr is **1** for `index-status`
  and **1** for `overview`: the fact never prints twice.
- JSON (`tip-double-json.txt`) carries both distinctly: `stale_reasons`
  `[{kind: added, file: extra.py}]`, `withheld_count` 1, `withheld_files`
  `[{file: notes.md, reason: sensitive content}]`, `withheld_summary`
  `1 file skipped (sensitive content)`.

## Bar 6 — the stale line no longer says what the verdict means: **FAIL**

ADR-0003 section 3 defines the stale text line as carrying
`answers describe the index, not the tree` beside the refresh hint. Measured at
the tip:

```
Warning: index is stale: 1 added file -- <path>; refresh with code-indexer reindex-project <path>
```

The guidance sentence is **absent from the stale line**; it survives only on the
unknown branch (`Warning: index staleness check did not run (...); answers
describe the index, not the tree.`). This is not an accident of one fixture: the
round deleted the assertion that used to pin it — `tests/test_read_contract.py:304`
at base (7bccc54), `assert "answers describe the index, not the tree" in line`,
inside `test_stale_answer_prints_the_record_line_once`, the test the round
renamed — and
`tests/test_docs.py:743` still requires the sentence somewhere in the entry
document, so the docs test stays green while the shipped line lost the words.
Repair belongs in the folds leg.

## Bar 7 — a committed document quotes a line the tool no longer prints: **FAIL**

Every tracked document that quotes the banner:

| document | quote | matches the shipped line? |
|---|---|---|
| `SKILL-CLI.md:91` | `Warning: index is stale: 2 modified files -- <path>; refresh with code-indexer reindex-project <path>` | **yes** |
| `skill/references/machine-output.md:52` | `Warning: index is stale: 2 modified files, 1 deleted file -- <path>; refresh with ...` | **yes** |
| `CHANGELOG.md:6` (previous shape) and `:10` (new shape) | old paragraph, explicitly labelled as what it used to print; new line | **yes** (the old quote is history, not a claim about the tool) |
| `README.md:206-207` | `Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z: 2 modified, 1 deleted); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>` | **NO** — the old paragraph, verbatim |
| `docs/adr/0003-read-only-and-staleness-contract.md:56` | the old paragraph, verbatim | **NO** — owner call, already carded; listed, not fixed |

`README.md:202-204` also lists the JSON block as six fields and omits
`withheld_count` / `withheld_files` / `withheld_summary` /
`withheld_unexamined_count` — pre-existing, outside this row's rule but the same
sentence block needs the same edit.

## Bar 8 — no exit-code change: PASS

- stale read: **exit 0**, one stderr line, the answer on stdout
  (`captures/tip-exit-stale.txt`).
- schema-version refusal (manifest's recorded `schema_version` set to 6 in a
  throwaway index, then restored): **exit 4**, **stdout 0 bytes**, one stderr
  line — `RebuildRequiredError: index for <path> was written at manifest schema 6 and this build reads schema 7; ...`
  (`captures/schema-refusal.txt`).

## Bar 9 — suite and hygiene: PASS

- `uv run pytest tests/ -q -W error` at the tip (`uv sync --frozen --all-extras`):
  **1002 passed, 2 deselected in 152.22s**, zero red
  (`captures/pytest-tip.txt`). Base for parity: 999 tests, 998 passed and the
  installed-digest test red (`captures/pytest-base.txt`); see below.
- installed-skill digest: **GREEN at the tip, not red as the record states.**
  the installed skills root was refreshed after the leg's run
  (references mtime 19:10, `SKILL.md` 19:25 today) and its `references/` is
  byte-identical to the tip's `skill/references/`; it is precisely this that
  makes the digest test red at the base worktree (the installed copy is already
  the tip's). The record's "the single red is the expected one" was true when
  captured and is no longer the current state; nothing to fix, the count (1002
  total) is unchanged from the leg's 1001 + 1.
- `uvx ruff check src tests scripts`: **All checks passed!**
- `uv run python scripts/publishing_hygiene_lint.py`: see below (run last).
- signatures: all three commits `7bccc54..7704364` are `G` (good, signed) by
  `sangingeum <sangingeum@gmail.com>`.
- pushed ref equals the tip: `git rev-parse HEAD origin/main` ->
  `7704364c003599d40d32eb00d09ddcc6ee2ef18a` on both (verified before this
  gate's own evidence commit; the commits after it in `git log` are this
  evidence directory, not a candidate change).

## Bar 10 — scope: PASS

`git diff --name-only 7bccc54..7704364` = 18 files: `CHANGELOG.md`,
`SKILL-CLI.md`, `runs/r4b-stale-banner.md` + 9 captures, `skill/references/machine-output.md`,
`src/code_indexer/cli.py`, `src/code_indexer/staleness.py`, `tests/test_docs.py`,
`tests/test_read_contract.py`. All inside the allowlist; no manifest schema, no
`EXTRACTION_VERSION`, no `docs/adr/` file touched.

## Rows needing a fix

1. **Bar 6** — the stale line must carry the meaning sentence beside the refresh
   hint (ADR-0003 section 3), as the unknown branch still does. Folds leg.
2. **Bar 7** — `README.md:206-207` must quote the shipped line (and its JSON
   field list should name the withheld fields). `docs/adr/0003:56` is the
   owner's already-carded call: listed, not fixed here.

## Verdict: **FAIL** — two documentation/contract rows

Evidence: this directory (`verification.md`, `captures/`, and the four scripts that
built the fixtures, took the captures, proved statelessness and produced the
schema refusal).
