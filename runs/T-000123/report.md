# Narrow re-verify of the withheld-file folds

Final tip `d047e07`; `git ls-remote origin refs/heads/main` =
`d047e0706f807431105dfb00bdbd2e95836ab30b`, equal to `HEAD`. Range
`143d85f..d047e07` is 16 commits; the code fold is `2e76717`, the rest are
records. Every commit in the range reports `%G? = G` (signed, signer
`sangingeum`):

```
d047e07 G  2dfc63b G  e7bf852 G  8054b36 G  974f0ed G  2e76717 G  5e13ac0 G
c138f87 G  95fa312 G  8220166 G  1ad6684 G  219fd61 G  14a85e1 G  ebd8d58 G
ea7dee5 G  e2bb054 G
```

Scope: the rows the folds changed, plus one regression run. The gate's ten
bars were not re-run, and nothing here is a fix.

## Method

Throwaway trees indexed into a throwaway index root
(`$INDEX_ROOT=…/scratch/t000123/state`), driven through the real `uv run
code-indexer` commands; the live index was never written by a probe. Rows that
need a file above the default 1 MiB scan cap ran with `MAX_FILE_BYTES=67108864`,
a configuration knob of the product. The fan-out wall is 30 s and has no CLI
flag; to reach its skip branch in one run a driver lowered
`core.QUERY_BUDGET_DEFAULT_S` and then invoked the shipped `semantic-search`
command through the CLI app, so command, argument parsing, staleness
aggregate, search and the JSON renderer are the shipped code — only the wall
value is reduced. Raw captures stay outside the repository; the evidence is
quoted below.

## Field collision — PASS (`staleness.py:250-262`, `core.py:1348-1352`)

One `semantic-search --all-projects --json` answer, seven projects skipped by
the wall and nine withheld files across the remaining trees, same run:

```
skipped_count 7   skipped [budget x 7]   partial true
withheld_count 9  withheld_unexamined_count 9
stale true        stale_reason_count 83     hits 2
(.skipped|length) == .skipped_count  ->  [7, 7]
```

Four counts, four independent values. The fan-out count does not move with the
withheld block: the same command and wall with the only withheld file moved out
of the tree reads `skipped_count 2` / `withheld_count 0`, and with the file back
`skipped_count 2` / `withheld_count 1`. The pre-existing keys are all present
with their old meaning — `schema`, `hits`, `truncated`, `dropped`, `skipped`
(the fan-out project list), `skipped_count` (its length), `partial`, `stale`,
`stale_known`, `stale_reasons`, `stale_reason_count`, `index_revision`,
`last_indexed`, and `staleness` (the per-project map). The withheld facts are
`withheld_count` / `withheld_files` / `withheld_summary` /
`withheld_unexamined_count`, a distinct vocabulary; no pre-existing name changed
meaning.

## The override — PASS (`core.py:547,574`; `staleness.py:145-148,190`)

Two throwaway projects, identical content, the sensitive file written AFTER the
pass (so it is rowless in both):

```
--allow-sensitive set    stale true   reasons [{added, secret.py}]   withheld_count 0
override unset           stale false  reasons []                     withheld_count 1
                         withheld_files [{secret.py, sensitive content}]
```

Both directions on the real read path: the first reports `added`, which is what
the next pass will write; the second reports the withheld state.

## Budget accounting — PASS (`staleness.py:202,206`; `sensitive.py:50`)

A project indexed with the 1 MiB scan cap raised, then one 18,000,000-byte new
file (`aa_big.py`, which sorts before the secret) and one content-sensitive file
(`z_secret.py`):

```
stale true  added [aa_big.py]  withheld_count 1
withheld_files [{z_secret.py, sensitive content}]  withheld_unexamined_count 0
```

The later sensitive file keeps its classification. The debit is by bytes read,
not size — `staleness.py:206` `budget -= len(text.encode("utf-8", "replace"))`
against `SENSITIVE_RECHECK_BYTES = 8 MiB` (`:54`), with the read capped at
`SENSITIVE_READ_CHARS = 262144` (`sensitive.py:50`, `staleness.py:202`):

```
8,388,608 - 262,144      =  8,126,464  budget left after the big file
8,388,608 - 18,000,000   = -9,611,392  what size-charging would leave
```

A negative remainder is the old defect: the next candidate fails the budget
check and is reported `added`. It did not happen here.

## Withheld summaries — PASS (`staleness.py:215-232`)

```
1 file skipped (sensitive content)                          singular
2 files skipped (sensitive content)                         plural, one category, no numeral
3 files skipped (2 sensitive content, 1 sensitive filename) both categories, numeral present
```

Vocabulary note: the block is `withheld_*`, the printed phrase says `skipped` —
which is the spelling the review's fold table specifies (`2 files skipped
(sensitive content)`), not `file withheld`.

## One count per line — PASS (`cli.py:1178-1181,1248-1254`)

A project whose last pass skipped two sensitive files (`last_pass` carries
`sensitive_skipped: 2`) and whose tree still withholds two:

```
project=… state=idle … last_pass={'files_indexed': 0, …, 'sensitive_skipped': 2} …
2 files skipped (sensitive content) — privacy; override with --allow-sensitive on add-project
```

One phrase on the line: `[0-9]* files* skipped` matches exactly once and
`sensitive=` zero times; the last pass's number rides only in the JSON field
`sensitive_skipped` (2). The two vocabularies no longer state the same fact
twice.

## Truncation disclosed — PASS (`staleness.py:260,322`; `indexing.md:332-335`)

Forty 408,000-byte new files (prefix read 262,144 each) sorted before a
sensitive file:

```
stale true  stale_reason_count 41  added count 5 (capped list)
withheld_count 0  withheld_summary null  withheld_unexamined_count 9
```

32 prefixes exhaust 8 MiB; the remaining 8 files and the secret are `added`
reasons and are counted as 9 unexamined — the path the budget never reached is
not silently reported as merely `added`. The reference says what that means:
`skill/references/indexing.md:332-335` — the budget is charged in bytes actually
read, one huge file costs its prefix, and "A path the budget did not reach stays
an `added` reason and is counted in `withheld_unexamined_count`: an `added`
reason says the path is not in the index, and nothing about its content."

## One prefix constant, one composition — PASS (`sensitive.py:50,90`)

Both call sites import the same constant and the same decision function:

```
sensitive.py:50   SENSITIVE_READ_CHARS = 65536 * 4
sensitive.py:90   def skip_verdict(rel_path, text=None)
indexer.py:174    text = fh.read(SENSITIVE_READ_CHARS)
indexer.py:178    verdict = skip_verdict(path, text)
staleness.py:202  text = fh.read(SENSITIVE_READ_CHARS)
staleness.py:192,207  verdict = skip_verdict(f.path[, text])
```

No second literal prefix and no `file_is_sensitive` remain in `src/`.

## Skip-reason lookup — PASS (`sensitive.py:113-125`)

`skip_reason_text` is `SKIP_REASON_BY_LAYER[verdict]`, a keyed lookup:

```
filename -> 'sensitive filename'
content  -> 'sensitive content'
unknown third layer -> KeyError: 'entropy'
skip_verdict('x.pem') -> 'filename'
skip_verdict('a.py') -> None
skip_verdict('a.py', secret text) -> 'content'
```

An unknown layer raises; it cannot be reported under another layer's phrase.

## Consumer rule and docstring — PASS

`skill/references/indexing.md:211-214`: "**Consumer rule:** `stale: false` with
a non-zero withheld count is not 'nothing was withheld'. A consumer that
branches on `stale` alone learns nothing about withheld files: they are not
stale, and they are not in the index. Branch on the withheld count." The same
rule is in `docs/json-schema.md:203-210` — the fields are present on a `fresh`
verdict, so "nothing changed" and "one file withheld on purpose" never read
alike — with the truncation paragraph beside it. `Core.staleness` says it opens
rowless files, `core.py:513-517`: "A scanned path with no manifest row is opened
for the same bounded prefix the pass reads, so a deliberately withheld file is
reported as withheld rather than as an added reason; the project's
`allow_sensitive` override decides whether anything is withheld at all."

## Regression — PASS

```
uv run pytest tests/ -q -W error          998 passed, 2 deselected in 105s
uvx ruff check src tests scripts          All checks passed!
uv run python scripts/publishing_hygiene_lint.py
    publishing hygiene: scanned=920 skipped=0 violations=0
```

The hygiene run above is the one after this record was written; it scanned 920
tracked files, this one included.

## No live reindex — observed

- `git log --name-only 143d85f..d047e07` carries no reindex script and no
  `scripts/` path.
- The live watch log contains no `reindex` line at all (0 matches).
- The standing daemon is alive: `code-indexer watch --all --background`,
  pid 576211, up 1 h 28 m at observation, cwd the project repository.
- All 12 live projects report `last_indexed` inside one block,
  2026-11-07T09:43:27Z–09:43:29Z, and the log's periodic-sweep block names the
  same 12 projects at that moment.
- The project's own index moved once more, to 2026-11-07T09:46:06Z, on a one-file
  incremental pass. The only file written inside the repository in that minute
  was `.pytest_cache/v/cache/nodeids`, from this leg's regression run; no
  multi-file pass and no reindex appears. The movement is the daemon's, but the
  09:46 pass was triggered by this leg's test run, and that is stated rather
  than assumed away.
- The live registry holds the same 12 household projects; no probe project
  appears there.

## Verdict

All eleven rows PASS. The folds hold at the tip, the regression is green, and
nothing outside the folded files was re-verified.
