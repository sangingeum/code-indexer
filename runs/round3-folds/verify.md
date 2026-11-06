# Narrow re-verify of the round-3 folds

- Card: T-000085 (board `code-indexer`, read-only to me)
- Range verified: `f3d6b1e..d4dc39e`; tip `d4dc39e`; `origin/main` == `d4dc39e`
- Verifier: QA / test engineer
- Object: the eight folds in `runs/round3-review/record.md` (`## Folds for the fix leg`),
as implemented by `runs/round3-folds/record.md`. Not a re-run of the round-3 gate's nine bars.
- Inputs: `runs/round3-gate/record.md`, `runs/round3-review/record.md`,
`runs/round3-structural-query/record.md`, `runs/round3-folds/record.md`, the tree at the tip.

## Verdict

**PASS WITH FINDINGS** — one low, documentation only (finding F-1, FOLD-C's wording).

All eight folds landed and the two behavioural claims hold on a live payload: a graph root now
carries the label its row's verdict supports (the `Core` payload agrees on `unknown` for the two
`.out` roots), and text mode says when the subject name had several definitions. The round record's
populations are now bound to one revision and replay as revision-bound numbers, not as timeless
ones. The scope fence held: no schema change, no extraction-version change, no reindex of the
register, no new command; the only behaviour changes in the diff are FOLD-D's label and FOLD-G's
one line.

What keeps this from a clean pass is FOLD-C's letter: the bar asked all three sites to state that a
leading wildcard is `SCAN s`, and two of the three state it in prose ("a scan of `symbols`",
"a scan of the table") without the planner token. The false claim is gone from all three, so the
substance is right; the wording is short of the bar.

## Verdict on the index I read

Two frozen reads, both with the standing watcher stopped (`code-indexer unwatch --all`):

- the three CLI replays and the first population pass at `index_revision=84`;
- the accepted population capture at `index_revision=86` (`b-populations.txt`),
  `last_indexed=2026-11-06T23:10:49Z`, `manifest_schema=7`.

Every population the two reads share is identical (2,331 symbol rows; `language_python` 2,076;
`inherits_IndexerError` 7), because the two revisions between them added only this verification's
own `.txt`/`.json` captures. I quote the revision beside every number below.

Watcher lineage: it was at pid 352213 when I started; I stopped it twice (`pid=352213`, then
`pid=355470`) for the frozen reads and restarted it each time. **It is running now as pid 355623**
(`b-watch-restart.txt`).

Before the live FOLD-D check I ran the ordinary incremental per-project pass the brief asked for:

```
$ code-indexer reindex-project <project>
code-indexer: {"files_indexed": 0, "chunks_embedded": 0, "chunks_reused": 3514,
 "files_deleted": 0, "duration_s": 0.09, "chunks_skipped": 0, "files_committed": 0,
 "sensitive_skipped": 0}
```

It took the revision 76 -> 77, indexed no files, and left one stale reason standing:
`{"kind": "added", "file": "tests/test_sensitive_exclusion.py"}`. That file is tracked and clean in
git; the pass does not index it, so this repository's `index-status` will report `stale: true`
whatever else happens. I did not establish why (see "What I did not verify").

## The eight bars

### FOLD-A — the editorial placeholder

```
$ rg -n 'to be filled before the push' . --glob '!runs/round3-folds/verify/*'
./runs/round3-review/record.md:153:is `(to be filled before the push)` under a verification block that is filled in. It is the round's own
./runs/round3-folds/record.md:27:`(to be filled before the push)`. The verification block above it was already

$ rg -n 'to be filled|TBD|FIXME|TODO' runs/round3-structural-query/ runs/round3-gate/ runs/round3-review/ runs/round3-folds/record.md
runs/round3-folds/record.md:31:(`rg 'to be filled|TBD|TODO|FIXME|placeholder'` over
runs/round3-review/record.md:153 ... runs/round3-review/record.md:155 ... runs/round3-gate/record.md:165
```

Zero occurrences in `runs/round3-structural-query/record.md`; its tail is now the verification
block (`24 passed`, ruff, hygiene lint). The three quoting sites are the gate record (finding F1),
the review record (its ruling), and the fold record describing the fold — the same sweep the fold
record describes, scoped to those three directories, still answers "only the gate and review
records", so the claim is accurate as scoped.

**PASS.**

### FOLD-B — one snapshot, one revision

The record now opens with a **Measured at** paragraph naming `index_revision=68`, commit `f3d6b1e`,
the watcher stop (`unwatch --all`, pid 345973) and restart (pid 352213), and the raw capture
`runs/round3-folds/measure.txt`. Populations:

- the ledger block carries its own label: "Across the whole index (`index_revision=68`, 2,322 symbol rows)";
- the over-attribution figures carry theirs: "Measured on this index at `index_revision=62`";
- the graph block says "Measured at the base commit";
- the remaining filter/truncation blocks carry no per-block label and are covered by the
  document-level paragraph — which is the fold's own allowed alternative ("re-quoted from one
  frozen snapshot ... say which leg each block was measured at").

Record against the raw capture: `measure.txt` opens with `"index_revision": "68"` and
`stale_reasons` = `measure.txt` (itself) + `tests/test_sensitive_exclusion.py`, and every block the
record quotes appears verbatim in it, including the 2,322-row ledger line at its end
(`{"index_revision": "68", "symbol_rows": 2322, "ledger": [["ast", "parsed", 2033],
["regex", "parse_unavailable", 289]]}`). Internal consistency holds: 2,033 ast + 289 unknown = 2,322
(2033+289 = 2322, checked with calc), `heuristic` 0, parse verdicts
`parsed:170, parse_error:0, parse_unavailable:794`.

Replay at `index_revision=86` (the accepted read), against the record's numbers:

| population | record (rev 68) | replay (rev 86) | holds? |
|---|---|---|---|
| `find-symbol --inherits IndexerError --json` | 7, same seven names | 7, same seven names | **holds** |
| `--language python` `match_count` | 2,070 | 2,076 | drifted +6 |
| `find-symbol find_symbol --json` `match_count` | 2 | 2 | **holds** |
| `--kind class` | 137 | 137 | **holds** |
| `--container Manifest` | 49 | 49 | **holds** |
| `--language markdown` | 97 | 97 | **holds** |
| `--has-call find_symbols` | 26 | 26 | **holds** |
| `--visibility private` | 377 | 378 | drifted +1 |
| symbol rows / ledger | 2,322 (2,033 ast / 289 unknown) | 2,331 (2,038 ast / 293 unknown) | drifted |
| files holding symbols without AST | 137 | 138 | drifted +1 |

The drift is explained and cannot be removed: revisions 68 -> 86 span the fold leg's own record and
captures plus this verification's, so the populations grew by rows that did not exist at capture
(2076-2070 = 6, 378-377 = 1, 138-137 = 1 — calc). One replay is a line-number drift the fold record
itself predicts: `find_symbol`'s second candidate moved from `cli.py:1781-1801` to
`cli.py:1799-1819` while `match_count` stayed 2.

**PASS** — every population is revision-bound, each carries a label at block or document level, the
numbers are internally consistent, and the two replays that could still hold do.

### FOLD-C — the name index, in three sites

```
$ rg -n 'never a pass over the table' .
./runs/round3-review/record.md:317: | FOLD-C (F3+N4) | ... drop "never a pass over the table" |
./runs/round3-folds/record.md:73:  claims "never a pass over the table"; it names `SCAN s` for a leading
$ rg -n 'SCAN' SKILL-CLI.md CHANGELOG.md docs/json-schema.md src/code_indexer/manifest.py
src/code_indexer/manifest.py:623:        cannot be a range and is a scan of ``symbols`` (``SCAN s``) — cheap
```

The claim is gone from all three sites (only the review's fold table and the fold record's own
description of the removal quote it). No index added / prefix = range seek:

- `SKILL-CLI.md:207` — "no index was added — a prefix glob is a range seek on the existing name
  index, and a leading wildcard is a scan of `symbols`, cheap at this size";
- `CHANGELOG.md:9-11` — "no index was added for it — a prefix glob is a range seek on the existing
  name index, and a leading wildcard is a scan of the table, cheap at this size";
- `manifest.py:618-624` — "No index was added for it: the existing name index answers a PREFIX
  pattern as a range seek, while a leading wildcard cannot be a range and is a scan of ``symbols``
  (``SCAN s``) — cheap at this size, and measured rather than assumed. The planner plan is asserted
  in the tests".

**PASS on the substance, FINDING F-1 on the letter**: only `manifest.py` states `SCAN s`; the two
prose sites stop at "a scan of `symbols`" / "a scan of the table". The bar asked all three to state
`SCAN s`.

### FOLD-D — one row, one label, on a live payload

Live payload at `index_revision=86` (`d-find-symbol.json`, `d-find-callers.json`), the gate's own
reproducer name:

```
$ code-indexer find-symbol Core --json | jq '{match_count, ev: ...}'
{"match_count":9,"ev":[{"k":"ast","n":7},{"k":"unknown","n":2}]}
  unknown rows: runs/round1-gate/read-write-intent-batch/skeleton.out:1
                runs/round1-gate/service-dial-batch/skeleton.out:1

$ code-indexer find-callers Core --json
{"match_count":9,"roots":[{"k":"ast","n":7},{"k":"unknown","n":2}],
 "unknown_roots":[{"file":"runs/round1-gate/read-write-intent-batch/skeleton.out","line":1,"confidence":"unknown"},
                  {"file":"runs/round1-gate/service-dial-batch/skeleton.out","line":1,"confidence":"unknown"}]}

$ jq '[.ambiguity.candidates[] | {file,evidence}]' d-find-callers.json
  ... 7 candidates evidence=ast, then the same two `.out` files evidence=unknown
```

The roots and the candidates and `find-symbol` agree on `unknown` for both `.out` files, and the
seven `ast` rows agree on `ast`. The contradiction the gate and the review measured is gone in the
live payload, not only in the test. Text mode, the gate's other form of the reproducer:

```
$ code-indexer find-callers Core --max-nodes 9 --depth 1
... 9 definitions match 'Core' and this name is ambiguous: every one is a root above ...
```

One column list serves every symbol read:

```
$ rg -n '_SYMBOL_COLS|_SYMBOL_FILTER_SELECT|_SYMBOL_SELECT' src/ tests/
src/code_indexer/manifest.py:571:    _SYMBOL_SELECT = (
... :709 find_symbols, :762 symbol_query_plan, :805 symbols_with_files, :810 symbols_for_file, :1083 all_symbols
```

No occurrence of the two retired names anywhere (source or tests). `count_symbols` needs no column
list (`SELECT count(*)`), `all_symbol_names` reads names only, and `definitions_of` delegates to
`find_symbols` — so every read that carries evidence carries the verdict join.

One label function:

```
$ rg -n 'confidence="ast"|confidence="unknown"|confidence=EV' src/
(exit 1: no hardcoded label)
$ rg -n 'evidence_of' src/code_indexer/
identity.py:37 def, :89 candidate record; graph.py:172 roots; cli.py:1916, :1958
```

`identity.evidence_of` is the only label function any surface calls; the roots are built by
`_definition_roots` (`graph.py:161-174`), which labels each root from its own row.
Ties: `tests/test_structural_filters.py::test_a_parse_error_row_is_unknown_on_every_surface` builds
the `parse_error` case (AST-shaped rows, failed verdict) and asserts all three surfaces say
`unknown` — a row-level proof, not a constant.

**PASS.**

### FOLD-E — the name-level join, disclosed

- Record: `runs/round3-structural-query/record.md:105-117` — the `(file, bare name)` join, "37 of
the 1,488 call-filter hits share their `(file, name)` with another symbol row, while `base_class`
has none (20 hits, 0 shared)", labelled "Measured on this index at `index_revision=62`", and the
deferral: "the structural fix — resolving a reference to one container — is the staged
reference-resolution work the identity model's section 3 schedules".
- `SKILL-CLI.md:215-222` — "The join is name-level, and that is a known limit ... Fixing that needs
reference resolution to a container, which is the staged work the identity model's section 3
schedules — no shipped command narrows the join today".
- `docs/json-schema.md` (structural filters section) — "That join is **name-level by design** ...
Narrowing it needs reference resolution to a container (the staged work ...); until then a hit
means \"this file's `name` does this\", not \"this exact symbol does\"".
- `manifest.py` `_symbol_filters` docstring — the same statement at the code site.

No code change for it: the diff in `_symbol_filters` is docstring-only (the EXISTS clause at the
same place is byte-unchanged), and nothing claims the false positive is fixed — three sites say
the join is not narrowed, none says it is. **PASS.** (The 37/1,488 figure lives only in the round
record, which is the only site that can carry a revision label for it; the docs carry the join and
the deferral. Noted, not a failure.)

### FOLD-F — `heuristic` reserved, and still unreachable

```
$ rg -n 'heuristic' src/code_indexer/identity.py SKILL-CLI.md docs/json-schema.md CHANGELOG.md
identity.py:10  * ``heuristic`` — RESERVED, and produced by nothing today. The chunker labels
identity.py:13    so no symbol row can be ``heuristic``
identity.py:38  """``ast`` | ``unknown`` for one symbol row (``heuristic`` is reserved).
SKILL-CLI.md:226 `heuristic` is reserved in the vocabulary and produced by nothing today
json-schema.md:268 The third value, `heuristic`, is reserved in the vocabulary and produced by no extractor today
CHANGELOG.md:45 - **`heuristic` is reserved, not reachable.** No extractor produces it
$ rg -n 'EVIDENCE_VALUES' .
(no hit in src/ or tests/; only the review's N1, the fold record's note, and this verification's own diff capture)
```

The remaining `heuristic` mentions are a different, reachable axis and are not claims about symbol
evidence: `SKILL-CLI.md:553-558` (reference confidence for non-AST languages), `docs/json-schema.md:300`
(`find-definition` confidence `exact|heuristic`), `CHANGELOG.md:732` (refs), and the `--rerank` flag.

The code still cannot produce it, checked rather than trusted:

- `identity.py:50-55` returns `EVIDENCE_HEURISTIC` only when `verdict == "parsed"` **and**
  `source != "ast"`;
- every parser-side writer sets `source="ast"`: `ts_chunker.py:170` (`c.source = "ast"` for every
  chunk of a parsed file), `:279`, `:296`, `:319`;
- the regex fallback is the only source of `source="regex"` (`chunker.py:45` is the dataclass
  default), and `parse_file` returns it only together with `PARSE_UNAVAILABLE` (`:144`, `:148`,
  `:155`);
- so `parsed` + `regex` cannot be written, and the heuristic branch is unreachable today — which is
  exactly what "reserved for a future extractor" means. The index agrees: at `index_revision=86` the
  ledger is `parsed/ast = 2038` and `parse_unavailable/regex = 293`, zero `heuristic`.

**PASS.** Note: `docs/json-schema.md:248` still lists `"evidence": "ast"|"heuristic"|"unknown"` in the
payload type while the prose at :268 declares it reserved — the vocabulary shape, not a claim of
reachability; not a finding.

### FOLD-G — the graph commands' help and the one line of text

```
$ code-indexer find-callers --help | rg 'match_count|ambiguity|targets'
24: the NAME too: JSON carries `match_count` and, when the subject has more than
25: one definition, an `ambiguity` block listing every candidate the walk unions
26: over; each direct hit then carries `targets`, the candidate definition(s) it

$ code-indexer find-callees --help | rg 'match_count|ambiguity|targets'
10: line in the tree. The subject NAME is reported too: JSON carries `match_count`
11: and, when the name has several definitions, an `ambiguity` block, with
12: `targets` on each direct callee naming the definition it was reached from.
```

Live text mode, both commands:

```
$ code-indexer find-callers Core --max-nodes 9 --depth 1
9 definitions match 'Core' and this name is ambiguous: every one is a root above, and each direct hit names the definition(s) it can belong to

$ code-indexer find-callees __init__ --max-nodes 3 --depth 1
61 definitions match '__init__' and this name is ambiguous: every one is a root above, and each direct hit names the definition(s) it can belong to
```

The JSON payload is unchanged by this fold (the sentence is emitted only in text mode), and the
tests pin both: "2 definitions match 'step'" for `find-callers` and `find-callees`, and silence for a
single-definition subject. **PASS.**

### FOLD-H — `--exactly-one`'s empty answer, documented and driven

```
$ code-indexer find-definition __zz_no_such_symbol__ --exactly-one ; echo exit=$?
NotFoundError: no indexed definition for '__zz_no_such_symbol__'
exit=1

$ code-indexer find-definition __zz_no_such_symbol__ ; echo exit=$?
no exact-match symbols named '__zz_no_such_symbol__'
exit=0

$ code-indexer find-definition __init__ --exactly-one ; echo exit=$?
ArgumentError: name '__init__' resolves to 61 symbols and this command needs exactly one — ...
exit=2
```

`docs/json-schema.md` (find-definition section) states the empty answer, exit 1, empty stdout, and
the choice: "This is the documented choice (not an alignment with the other refusals, which are
`ArgumentError`, exit 2, because the name is ambiguous rather than missing)". `identity.resolve_one`'s
docstring says the same. **PASS.**

## The round's standing bars at the tip

```
$ uv run pytest tests/ -q -W error
707 passed, 2 deselected in 93.25s (0:01:33)          # warn-clean; dev's 707/2 reproduced

$ uvx ruff check src tests scripts
All checks passed!

$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=659 skipped=0 violations=0 (untracked files are out of scope)

$ sha256sum SKILL-CLI.md ~/.hermes/skills/code-indexer/SKILL.md
d862bc3ed4d005b8a8f8cea30bd61264320d71b0cc04c603aff4613748e9fb6c  SKILL-CLI.md
d862bc3ed4d005b8a8f8cea30bd61264320d71b0cc04c603aff4613748e9fb6c  ~/.hermes/skills/code-indexer/SKILL.md
```

Scope fence, from `git diff f3d6b1e..d4dc39e`:

```
CHANGELOG.md | SKILL-CLI.md | docs/json-schema.md
runs/round3-folds/{fold-d-after.txt,measure.txt,record.md} | runs/round3-structural-query/record.md
src/code_indexer/{cli.py,graph.py,identity.py,manifest.py}
tests/{test_ambiguity.py,test_structural_filters.py}
13 files changed, 647 insertions(+), 104 deletions(-)

$ git diff f3d6b1e..d4dc39e -- src/ | rg 'SCHEMA_VERSION|EXTRACTION_VERSION|CREATE TABLE|ALTER TABLE'
(no match)
$ git show f3d6b1e:src/code_indexer/cli.py | rg -c '@app.command'   -> 27
$ git show d4dc39e:src/code_indexer/cli.py | rg -c '@app.command'   -> 27
```

Behaviour changes in the code diff, read line by line: `graph.py` roots now come from
`_definition_roots` (FOLD-D); `cli.py` gains the `subject` argument and one `typer.echo` line plus
the two help paragraphs (FOLD-G); `identity.py` loses the dead tuple and gains docstrings;
`manifest.py` merges the column lists and rewrites two docstrings. No other behaviour change, no new
command, no schema or extraction-version change, no register reindex.

```
$ git log --format='%h %G?' f3d6b1e..d4dc39e
d4dc39e G
e1c3efb G
044f0cc G
6f3f366 G
917b5b0 G

$ git status --porcelain --untracked-files=no
(empty)
$ git ls-remote origin main
d4dc39e927f25ddd71e3d82129cf4a58b49e3d5e	refs/heads/main
```

## Findings

**F-1 — FOLD-C's letter is short at two of three sites. Severity: low. Is a fold: yes (FOLD-C).**
`SKILL-CLI.md:207` and `CHANGELOG.md:9-11` state that a leading wildcard is "a scan of `symbols`" /
"a scan of the table" rather than `SCAN s`; the bar asked all three sites for the planner token, and
`CHANGELOG`'s "scan of the table" is the phrase the review asked to replace. The false claim is
gone and nothing says anything untrue, which is why this is low and documentation-only. Closing it
is two clauses, not a leg.

No other defect found. The dev's report matches the tree on every bar I could re-run.

## Notes (not findings, no action implied)

1. `identity.evidence_of:53-54` keeps the `heuristic` branch live; it is unreachable because of the
   chunker's source values, not because the function was narrowed. That is the reservation working
   as designed, but a future textual-in-AST extractor would light the branch with no code change —
   worth knowing when that extractor lands.
2. `runs/round3-folds/record.md` says the index "was stale by one added test file at capture";
   `measure.txt` lists two added files, the second being `measure.txt` itself. Self-capture, no
   effect on a number.
3. The ordinary incremental pass cannot make this project's index fresh:
   `tests/test_sensitive_exclusion.py` stays `added` and `files_indexed` is 0. Every answer will
   carry a stale warning until someone looks at why (see below).
4. The hygiene lint's `scanned` count is 659 at my run against 657 in the dev record; violations
   are 0 either way, and the difference is in the tree the lint walked, not in the result.

## What I did not verify

- The round-3 gate's nine bars: of record, not this leg's object.
- FOLD-D's label on a file whose live verdict is `parse_error`: this index has 0 such files, so my
  live check used `parse_unavailable` rows (the class the gate's reproducer used); the
  `parse_error` case is proven by the dev's new test, which I read and ran, not by a live row.
- Why `reindex-project` leaves `tests/test_sensitive_exclusion.py` unindexed (`sensitive_skipped: 0`,
  `files_indexed: 0`, yet the file is reported `added`). One pass, no retry.
- Any other registered project's index; the register reparse path (owner-gated, out of scope);
  coverage numbers; the MCP surface against the docs.
- The two commands `find-callees --help`'s text on a subject with one definition: I checked the
  sentence is absent for `find-callers` via the test, not live on `find-callees`.

## Captures in `runs/round3-folds/verify/`

`b-index-status.json`, `b-unwatch.txt`, `b-watch-restart.txt`, `b-replay-inherits.json`,
`b-replay-python.json`, `b-replay-ambiguity.json`, `b-populations.txt`,
`d-find-symbol.json`, `d-find-callers.json`, `d-find-callers-text.txt`,
`g-help-callers.txt`, `g-help-callees.txt`, `g-callees-text.txt`, `g-callees-text-init.txt`,
`h-exactly-one.txt`, `h-plain.txt`, `h-ambiguous.txt`, `pytest-tip.txt`, `scope-src.diff`,
plus `replay_populations.py` (read-only population replay) and `mask.py` (host paths replaced with
`<project>` / `<home>`; re-running it is a no-op).
