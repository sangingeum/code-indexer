---
name: code-indexer
description: "Use for shell code search against indexed repos: semantic search, symbol/def/ref lookup, callers/callees/import graph, line-ranged source reads, and post-edit change maps via the code-indexer CLI. Use for any 'how does this codebase work' question; do NOT use for literal string search (rg), file listing (fd), or git history."
version: 0.1.0
---

# code-indexer (CLI)

One-shot typer CLI over `code_indexer.core`. Binary: `code-indexer`; package
`code_indexer`. CLI-only, LAN-local, no daemon on the query path.

## When to use

- All semantic code navigation against registered repos: search, symbols,
  refs, line-ranged source context — from shell, scripts, or in-conversation.

## Decision tree (agent workflow)

```
Need to understand a repo?
├─ First contact / orientation
│   └─ code-indexer overview --project P        # languages, dirs, entry
│                                                # points, hotspots (capped)
│   └─ code-indexer skeleton --project P [PATH] # symbol map of the repo
│                                                # or one subtree
├─ Know the area, want structure of one file
│   └─ code-indexer outline FILE --project P    # declarations + signatures
│   └─ code-indexer find-symbol NAME            # locate a known symbol
├─ Need actual source
│   └─ code-indexer get-code-context FILE --start-line N --end-line M
│       (or --symbol Foo.bar)                   # ONLY the relevant lines —
│                                                # never read whole files
├─ Conceptual question, names UNKNOWN
│   └─ code-indexer semantic-search "how does X work?" \
│        --project P --format compact --limit 8
│   (default ranking is `vector` (pure cosine); `hybrid` ranks cosine with a
│    lexical token-overlap bonus — pass --ranking hybrid when the query
│    contains an exact identifier)
└─ After edits
    └─ code-indexer changed-symbols --project P [--impact]
        # read what changed before searching anything
```

**When NOT to use code-indexer:**

- Exact string/regex search → use `rg`/`grep`. Semantic search is for
  meaning ("where do we validate tokens?"), not for literal text.
- Listing files → `fd`/`ls`. The index is not a filesystem replacement.
- git history/blame → `git log`/`git blame`. changed-symbols reads the
  working diff, not history.
- Huge mechanical refactors → do them with edit tools; re-index afterwards
  (the watcher does it automatically).

**Token-saving defaults:** prefer `--format compact` for search output (one
line per hit), keep `--limit 8`, and cap long result sets with
`--max-tokens 400`. Read source only via `get-code-context` with explicit
line ranges or `--symbol`. Take `overview --max-lines 40` before any deep
dive on an unfamiliar repo.

**State meanings (two vocabularies — do not confuse them):** the *pass state*
(`idle | indexing | never-indexed | error`) answers "is an indexing pass
running / did one ever complete?"; the *fingerprint state*
(`ok | needs-reindex | unverified`) answers "was the index built by the code
and configuration running now?". `index-status`, `list-projects`,
`lookup-project` and `doctor` report both: a project that owes a rebuild shows
`fingerprint=needs-reindex reason=...` (or `state=needs-reindex`) even while its
pass state is `idle`, so the two surfaces never contradict each other.
`needs-reindex` means the index configuration changed (model, dimension,
embed-text format, chunker, or the row-extraction version) — `search` refuses
with `ConfigError` until a reindex rebuilds it. That refusal is one line on
stderr, `ConfigError: index for <path> was built with <recorded>; current is
<current>; run: code-indexer reindex-project <path>`, exit 1 — the same
one-line `<ErrorType>: <message>` shape every other failure in this CLI
prints, never a traceback. Reaching the verdict needs the embedder (a
dimension probe), so on a host where the embedder is unreachable the verdict
cannot be given: `index-status` then prints
`fingerprint=unverified reason=embedder-unreachable` instead of omitting the
field. "Cannot verify" is its own value — a missing `fingerprint=` field
means the index matched. The *manifest-only* surfaces
(`find-references`, `find-callers`, `find-callees`, `deps`, `skeleton`/`map`,
`outline`/`file-outline`) still answer on such an index, with **one** stderr
warning line when the stored rows may have been produced by a different
row-extraction version (that warning compares only `chunker_version` and
`extraction_version`; a model/dimension change affects vectors, not stored
rows, and does not warn). Separately, `stale: true` on a hit means the file
changed after the index pass — line numbers may have shifted; the CLI still
returns the live lines (with a stderr Warning in line mode).

**Never run `reindex-project` unless explicitly told to** — it is a full
re-embed (minutes of Ollama time). The watcher keeps indexes fresh
incrementally; `needs-reindex` states are resolved by the owner's call.

## Automatic indexing (watch daemon — standard setup)

Projects are indexed **automatically** via the inotify watcher:

```bash
code-indexer watch /path/repo --background      # one project
code-indexer watch --all --background           # every registered project
```

`--background` daemonizes (PID file `<INDEX_ROOT>/watch.pid`, flock-guarded;
logs `<INDEX_ROOT>/watch.log`). File events trigger an incremental index pass
after a 3 s quiet period (bursts coalesce); a full staleness sweep runs every
300 s even with no events. Watchers self-heal; a stopped watcher leaves no
residue. Start a watcher for a project right after `add-project` — one-shot
commands still work without any watcher, but the watcher keeps the index
fresh so queries never pay the staleness pass.

Re-running `watch ... --background` while a live watcher already holds the
pidfile is idempotent: it prints
`watch: already running (pid=NNNN, pidfile=...)` plus the projects the live
watcher serves, and exits 0 when the requested project(s) are already covered
by that watcher (a watcher on a parent directory covers subprojects). If the
requested project(s) are NOT covered, it exits with a clearly-worded
nonzero status (3) telling you to stop the live watcher first. The message
"failed to start" never appears for this case — it is reserved for genuine
startup failures.

## Commands (read and write classes)

Every command is either a **write command** or a **read command**. A write
command changes the manifest, the vector store or the sources: `add-project`,
`index-more`, `remove-project`, `reindex-project`, `watch`, `unwatch`, and
`sweep-orphans --apply` (its default dry run is a read). Every other command
is a read: it opens no write transaction, runs no index pass, and contacts no
service to reach a staleness verdict. An invocation carrying `--refresh` is a
write, because it runs one incremental pass first.

The three plan writers — `plan-replace-body`, `plan-insert-symbol`,
`plan-delete-declaration` — are write commands whose only write is one plan
document in the plan directory; under `--print` they write nothing at all.
`plan-validate` and `plan-show` are reads and write nothing, always.
`plan-apply` is a write command: it mutates the sources through filekeeper and
writes one receipt. Under `--dry-run` it writes nothing at all.

Global flags: every command accepts `--skip-stale-check`, and it is
**inert**: reads never index, so there is nothing to skip, and reporting
staleness without repairing it is the default. Exactly seven commands also
accept `--fresh`: `semantic-search`, `skeleton`, `outline`, `find-symbol`,
`find-definition`, `get-code-context`, and `find-references`. It is absent on
`find-callers`, `find-callees`, `deps`, `overview`, and `changed-symbols`.
On `get-code-context` `--fresh` keeps its meaning — read the live file and
ignore cached symbol rows — and writes nothing; on the other six it is
accepted for compatibility and inert.

`--refresh` is accepted by every read command and is the **only flag that
turns a read into a write**: it takes the per-project flock, runs one
incremental pass, and answers from the result. A held lock is
`LockedError: indexing in progress` with exit 5 and no answer on stdout.
`--refresh` is also accepted by `doctor` (it refreshes each registered
project's index before the checks run), `eval` (refreshes the evaluated
project), and accepted-and-inert on `eval-compare`, which reads report files
rather than an index. No separate `refresh` command exists. The plan reads
(`plan-validate`, `plan-show`) accept `--skip-stale-check` and **not**
`--refresh`: they answer from a plan file and
the live sources, so there is no index answer behind them to refresh.

```bash
uv run code-indexer <cmd>   # or installed console script

# registry
code-indexer add-project /path/repo [--name myproj] [--allow-sensitive] [--include 'src/**'] [--priority 'src/main/**']   # register + initial index (foreground)
code-indexer lookup-project /path/repo [--json] [--refresh]   # registration check, no side effects
code-indexer list-projects [--json] [--refresh]               # per-project index summary + staleness verdict
code-indexer remove-project /path/repo                # DESTRUCTIVE: drops collection + manifest + registry entry
code-indexer index-more /path/repo src/subtree        # extend the indexed scope with another subtree

# orientation
code-indexer overview [--project P] [--path-prefix X] [--max-lines 60] [--json] [--refresh]
code-indexer skeleton [--project P | --name P] [PATH_PREFIX] [--tree] [--no-signatures] [--limit N] [--json] [--refresh] [--fresh]
code-indexer map ...      # alias for skeleton
code-indexer outline [--project P | --name P] FILE [--docstrings] [--json] [--refresh] [--fresh]
code-indexer file-outline ...   # alias for outline

# lookup
code-indexer find-symbol Name [--project P | --name P] [--kind class] [--type class] [--symbol-type class] [--file F] [--container X] [--visibility public|private] [--language python] [--inherits Base] [--has-call name] [--limit N] [--json] [--refresh] [--fresh]   # exact-first: EVERY definition of the name (40 if there are 40); --limit bounds it only when given; the substring tier is a capped fallback for a bare name
code-indexer find-symbol [--project P | --name P] [--kind class] [--name-pattern '*Manager'] [--file src/x.py] [--container X] [--visibility public|private] [--language python] [--inherits Base] [--has-call name] [--substring] [--limit 25]   # browse mode (no name); default cap 25
code-indexer find-definition Name [--project P | --name P] [--exactly-one] [--json] [--refresh] [--fresh]   # exact match only, every definition; --exactly-one refuses an ambiguous name with ArgumentError (exit 2) listing the candidates
code-indexer find-references Name [--project P | --name P] [--relationship calls|includes|inherits|references] [--limit 25] [--json] [--refresh] [--fresh]   # references from the single store; every row labelled confidence=/receiver=; json adds the staleness block
code-indexer find-callers Name [--project P] [--depth 2] [--max-nodes 40] [--json]   # fan-in over the refs table (ast vs heuristic); dotted Type.Member ok; every CALL SITE is a node; json adds match_count/ambiguity and truncated/omitted; an ambiguous name unions over every definition and each direct hit carries targets
code-indexer find-callees Name [--project P] [--depth 2] [--max-nodes 40] [--json]   # fan-out over the refs table; a TARGET is a node (one target reached twice is one callee); json adds match_count/ambiguity and each direct callee carries targets
code-indexer deps PATH [--project P] [--direction in|out|both] [--depth 2] [--max-nodes 60] [--format tree|edges|json]   # import graph around a file; json keeps the per-level in/out dicts and adds truncated/omitted

# reading
code-indexer get-code-context src/f.hpp --start-line 40 --end-line 80 [--refresh] [--fresh]   # or --symbol Foo::bar [--context-lines N]

# search
code-indexer semantic-search "query" [--project P] [--all-projects] [--limit 8] [--file-filter '*.py'] [--symbol-type class] [--language python] [--ranking vector|metadata|hybrid] [--mode hybrid|dense|lexical] [--rerank heuristic|none] [--per-file N] [--max-chars N | --max-tokens N] [--context-lines N] [--format text|compact|json] [--json] [--fresh]

# edits
code-indexer changed-symbols [--project P] [--base HEAD] [--head REF] [--staged] [--include-untracked] [--impact] [--depth 1] [--max-nodes 40] [--json]   # post-edit diff -> symbols; --impact lists callers + candidate tests, prints the first six as `(+N more)`, and names the bound its list hit; json reports truncated/omitted/bounds per file

# edit plans (prepare writes one plan file; validate and show write nothing)
code-indexer plan-replace-body SYMBOL [--project P] [--file F] [--kind K] [--container C] --text '...' | --body-file F [--plan-dir D] [--print] [--json]   # replaces the body node's whole line range
code-indexer plan-insert-symbol SYMBOL [--project P] [--file F] [--kind K] [--container C] [--position before|after|end] --text '...' | --body-file F [--note N] [--plan-dir D] [--print] [--json]   # before|after a declaration boundary, or at end of file
code-indexer plan-delete-declaration SYMBOL [--project P] [--file F] [--kind K] [--container C] [--plan-dir D] [--print] [--json]   # whole declaration range, never a body-only delete
code-indexer plan-validate REF [--project P] [--plan-dir D] [--json]   # per-check verdicts; writes nothing; a stale file exits 5
code-indexer plan-show REF [--project P] [--diff] [--plan-dir D] [--json]   # the document as read; --diff adds the unified diff; writes nothing
code-indexer plan-apply REF [--project P] [--plan-dir D] [--dry-run] [--refresh] [--json]   # WRITE: one keeper mutation per edit after a read-only pre-pass; per-file report, ordered operation ids, receipt

# lifecycle
code-indexer index-status [--project P | --name P | /path/repo] [--json] [--refresh]
code-indexer doctor [--json]              # health check; exit 1 on any fail
code-indexer sweep-orphans /path/repo [--apply] [--json]   # residue rows for deleted files; dry-run by default
code-indexer reindex-project /path/repo   # full rebuild, foreground
code-indexer watch /path/repo [--duration 300] [--background]   # optional inotify re-index daemon
code-indexer watch --all                  # watch every registered project
code-indexer unwatch /path/repo           # stop the watcher (reverse of watch)
code-indexer unwatch --all [--timeout 10] # stop it for every registered project

# dev utilities (CLI-only, not agent surface)
code-indexer eval --project P --queries eval/queries.jsonl [--k 1,3,5,10] [--mode dense|lexical|hybrid] [--rerank heuristic|none] [--out FILE]
code-indexer eval-compare BASE_REPORT.json CANDIDATE_REPORT.json
```

## Structural filters, evidence and ambiguity

`find-symbol` is the one structural query surface: every filter below is an
AND clause in the same read, so they compose without a second command.

| filter | answer |
|---|---|
| `--kind` (`--type`, `--symbol-type` kept as aliases) | function/method/class/struct/enum/namespace |
| `--name-pattern GLOB` | a case-sensitive glob over the name (`*Manager`, `get_*`); no index was added — a prefix glob is a range seek on the existing name index, and a leading wildcard cannot be a range and is a scan of `symbols` (`SCAN s`), cheap at this size |
| `--file F` | one project-relative file |
| `--container X` | the parent qualified name (`Manifest` for its methods) |
| `--visibility public\|private` | the stored column; a NULL visibility reads as public |
| `--language python` | the denormalized file language |
| `--inherits Base` | an ATTRIBUTED `base_class` reference to `Base`, joined to the symbol by file and bare name |
| `--has-call name` | an ATTRIBUTED `call` reference to `name`, joined to the symbol by file and bare name |

**The join is name-level, and that is a known limit.** `refs.from_symbol`
carries no container, so the two filters match a reference to a symbol's own
name in its own file. Two same-named symbols in ONE file are therefore both
credited with the union of both bodies' attributed references. Fixing that
needs reference resolution to a container, which is the staged work the
identity model's section 3 schedules — no shipped command narrows the join
today, so read a hit from these two filters as "this file's `name` makes this
call", not "this exact symbol does".

**Evidence.** Every symbol row carries `evidence`:
`ast` (attributed syntax) or `unknown` (the file has no AST extractor, or its
parse failed). `heuristic` is reserved in the vocabulary and produced by
nothing today — the chunker labels a parsed file's rows `ast` and its regex
fallback `unknown` — so it is not a state a row can be in. `--inherits` and
`--has-call`
answer from attributed syntax only, so a symbol in a docs/data file reports
`unknown` — never "no". Most repositories this tool indexes are mostly files
with no grammar, so an empty answer from those two filters prints an
`evidence_note` (JSON) / one explanatory line (text) naming how many files
were not attributed. Do not read such a zero as a negative.

**Limit and truncation.** `--limit` keeps its meaning and its default (none in
exact-name mode, 25 in browse mode). A capped answer says so: `match_count`
is the uncapped population computed from the same clauses, `truncated` is
true when fewer rows were shown, and text mode prints
`... N of M matches shown (--limit N reached; ...)`.

**Ambiguity.** An exact name with more than one definition reports
`match_count` and an `ambiguity.candidates[]` block — file, range, kind,
container, signature, language and `decl_hash` for each — instead of picking
a row. `find-definition --exactly-one` is the single-symbol surface: an
ambiguous name refuses with `ArgumentError` (exit 2) and lists every
candidate (`candidates` as data in `--json`). `find-callers` and
`find-callees` union over every definition and label each direct hit with
`targets` — the candidate definition(s) it can belong to, narrowed to one
where the store attributes the edge (an import resolves to a file) and
naming every candidate where a name-level reference cannot be attributed.
Their text mode prints the tree, those `targets` labels, and one line saying
the subject name matched several definitions, so an ambiguous walk is never
read as the walk of one definition.

## Staleness, refresh and exit codes

A read answers from the index as of `meta.last_indexed`, never from the
working tree. Every command that answers from the index reports a verdict:
`fresh` (nothing differs), `stale` (the tree differs; reasons are
`modified` / `added` / `deleted` files) or `unknown` (the check could not
run — the project path is absent, no pass has completed, or the walk
exceeded its own budget of 5 s / 200,000 entries). `unknown` is never
reported as `fresh`.

JSON, additive, `schema` stays 1, on every read answer:

| field | meaning |
|---|---|
| `stale` | boolean, unchanged meaning; on `get-code-context --json` it keeps its file-level meaning |
| `stale_known` | boolean; false means the verdict is `unknown`, so `stale: false` alone never means "checked and fresh" |
| `stale_reasons` | `[{kind: modified\|added\|deleted, file}]`, capped at five |
| `stale_reason_count` | total count before the cap |
| `index_revision` | the revision the answer was read from (the pass that wrote it; `last_indexed` on older manifests) |
| `last_indexed` | timestamp of the pass that wrote the index, ISO-8601 UTC |

Text mode prints exactly ONE stderr line and nothing extra on stdout:

```
Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z: 2 modified, 1 deleted); answers describe the index, not the tree. Refresh: code-indexer reindex-project <path>
```

A fresh answer prints nothing extra; an `unknown` answer prints one line
saying the check did not run. Repair is explicit: `--refresh` (or a pass
command: `add-project`, `index-more`, `reindex-project`) — no read repairs
the index on its own.

Exit codes:

| code | meaning |
|---|---|
| 0 | success, including zero hits — "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable (nothing reachable or installed) |
| 4 | the index requires a rebuild (fingerprint mismatch, needs-reindex) |
| 5 | lock or concurrency conflict — the same meaning filekeeper gives 5 |

"Nothing installed or reachable" is 3; a working provider that does not
implement the requested operation is 2. A failed query is distinguishable
from an empty result by both code and stream: `ErrorType: message` goes to
stderr and stdout stays empty (exit 3/4/5 as above), while a successful
empty answer prints `no results` on stdout and exits 0. No failure prints a
traceback.

## Symbol identity (schema revision 7)

A symbol row is identified by `(file, qualified_name, symbol_type,
decl_hash)` — a key with NO line number in it, so a definition shifted by a
line keeps its identity and the answer stops depending on how far down the
file the declaration sits.

| field | meaning |
|---|---|
| `qualified_name` | the container chain joined to the name: `Core.search` for a method, the bare name at top level |
| `container` | the enclosing declaration's qualified name, `null` at top level |
| `language` | copied from the file row (`python`, `csharp`, ...) |
| `decl_hash` | `sha256:` of the normalized declaration signature |
| `symbol_id` | an integer surrogate, regenerated by any reparse |

Rules an agent has to know:

- **`decl_hash` is a hash of the declaration's signature text, not of its
  body.** Editing the body keeps the hash; a shift keeps it; a reformat that
  changes the signature changes it. Do not promise durability across
  arbitrary source rewrites — the record says so deliberately.
- Two overloads that differ only by their signature are two rows, told apart
  by `decl_hash`, not by line. Two byte-identical declarations in one file
  are ONE symbol under this key.
- **`symbol_id` is revision-scoped, not an identifier you may keep.** Any
  reparse re-inserts every row and the ids move; JSON reports `symbol_id`
  beside `index_revision` so the pairing is explicit. Persisting an id
  across a pass is wrong by contract. The durable locator is the key
  (`path`, `language`, `qualified_name`, `symbol_type`, `decl_hash`).
- `find-symbol --json` and `find-definition --json` carry all five fields per
  row; `index_revision` rides in the staleness block on every read answer.

## Parse verdicts

Every indexed file carries exactly one verdict, read from the parser's own
result and never inferred from the chunker (which cannot fail — it falls back
to the regex chunker and walks error-tolerant trees):

| verdict | meaning |
|---|---|
| `parsed` | the parser returned a tree with no error and no missing node |
| `parse_error` | the tree carries an error or a missing node; its chunks are still indexed |
| `parse_unavailable` | no grammar for this language, a parser exception, or no AST chunks — i.e. the regex fallback ran. A failed claim, not a pass |

Do not read "the file was chunked" as "the file parses". `index-status`
counts the three per project (`parse=parsed:N,parse_error:N,parse_unavailable:N`
in text; three flat fields in `--json`).

## Edit plans (prepare, validate, preview)

A change can be prepared as a plan: an immutable JSON document named by the
sha256 of its own canonical bytes. There is no plan store to keep in sync — a
plan reference is a path or `sha256:<hex>`, resolved in the plan directory
(`<project>/.code-indexer/plans/` unless `--plan-dir` says otherwise), and
deleting the file deletes the plan. A path reference whose content hash
differs from the requested hash is refused.

```bash
code-indexer plan-replace-body SYMBOL --project P --file src/x.py --text '    return 41'
code-indexer plan-insert-symbol SYMBOL --project P --file src/x.py --position before --text 'def helper():\n    pass'
code-indexer plan-delete-declaration SYMBOL --project P --file src/x.py
code-indexer plan-validate sha256:<hex> --project P
code-indexer plan-show sha256:<hex> --project P --diff
```

- **Discovery** is the symbol query. `--file` searches that file's live
  declarations, so preparing never needs an index; without it the manifest's
  symbol index finds the file. A name with two definitions is refused with
  every candidate listed (`ArgumentError`, exit 2) — never resolved to the
  row that sorted first.
- **One write per prepare**: the plan file. `--print` emits the canonical
  document on stdout and writes nothing at all — not the file, not the
  directory. Use `--print` when you want the plan in a variable or a pipe;
  `code-indexer plan-replace-body ... --print | jq -r .plan_id` equals the
  sha256 of the document's own bytes with `plan_id` removed.
- **The locator is structural**: `path`, `language`, `qualified_name`,
  `symbol_type`, `decl_hash`, plus the observed `range` as evidence. There is
  no `symbol_id` in a plan — it is revision-scoped. Two nodes in one file
  with the same name, kind and declaration hash collide by construction and
  are refused with both listed (`AmbiguousNode`, exit 2).
- **Coordinates** are 1-based inclusive lines plus byte offsets into the
  stored bytes; offset 0 is the first byte after any BOM, and a terminator is
  not part of its line. `validate` cross-checks the two, so a corrupted offset
  fails arithmetic instead of being trusted. Columns do not exist in the
  format.
- **Replacement text always uses `\n`**; the apply adapter translates to the
  file's declared convention. A plan that encodes a carriage return is
  refused. `--body-file` input is translated at the input boundary.
- **`plan-validate` writes nothing and reports, it does not claim**: plan
  hash, schema, paths, file hashes and newlines, node re-derivation, the
  line/byte cross-check, region hashes, overlap and ordering, and the parser's
  verdict per file (`parsed` / `parse_error` / `parse_unavailable`). Nothing
  in its output says the result is valid, compiles or type-checks.
- **`plan-show --diff` writes nothing** and prints every affected file with a
  unified diff computed from the plan plus the current bytes. Without `--diff`
  it prints the document as read, unknown members included: reading never
  rewrites a plan, because a rewrite changes its hash.
- **Ordering is part of the format**: within a file descending start line,
  across files ascending path. Adjacent edits are legal; overlapping edits are
  refused (`OverlappingEdits`, exit 2), two insertions at one point are refused
  (`AmbiguousOrder`, exit 2), and an insertion after the last line is
  `position: end` with no line number.
- **A stale file refuses.** The plan's `files[].expected_hash` is checked
  first: a mismatch is `PlanStale`, exit 5, naming the file and both hashes.

## Applying a plan (the only command that writes a source file)

```bash
code-indexer plan-apply sha256:<hex> --project P            # apply, then report
code-indexer plan-apply sha256:<hex> --project P --dry-run  # the same report, nothing written
```

`plan-apply` needs `keeper` (filekeeper 0.1.8 or newer) on `PATH`; an absent
keeper is `KeeperUnavailable`, exit 3, with the install line, and there is no
fallback path. Once per invocation it reads `keeper file replace-lines --help`
and requires the flags it is about to pass (`--expected-hash`,
`--expected-content`, `--dry-run`); a build that dropped one is
`KeeperCapabilityMissing`, exit 3, naming the flag.

What a caller may assume, and nothing more:

1. **Nothing is written before every precondition passes.** A read-only
   pre-pass — the same code `plan-validate` runs, so the two cannot disagree
   about what stale means — checks every file hash, re-derives the targeted
   node, and reproduces every edit's region. One failure refuses the whole
   plan (`PlanStale`, exit 5) with zero mutations.
2. **Each file's bytes move from old to new through keeper's hash guard.**
   Each edit is one keeper line-range mutation carrying the hash observed
   immediately before it; a concurrent change is refused by keeper itself and
   the file keeps its bytes. Files apply in ascending path order and, within a
   file, start lines descending.
3. **The operations that ran can be undone in reverse order.** Every
   operation id is in the report and the receipt, in application order.
4. **No promise across files.** A failure stops the run: no further file is
   attempted and no compensating write is issued, because an undo is most
   likely to be refused just after a failure. The report says which files were
   `applied`, which `would_apply`, which `failed` and which are
   `not_attempted`, and names the keeper code and message for the failure.

Exit codes: a pre-pass refusal is 5 `PlanStale` and is not a partial apply; a
mid-loop failure is 1 `PartialApply` with the report on stdout once anything
was written, and 1 `KeeperFailure` when nothing was; keeper 5 maps to 5.

**Recovery** is the ordered operation id list, undone in reverse:

```bash
keeper history undo op_...   # last operation first, as the report prints it
```

Undo them in the order the report prints (reverse of application). keeper
refuses an undo with exit 12 when an affected path moved after the operation;
that ends recovery and is reported, never retried. Every call also carries
`--task-id <plan_id>`, so `keeper history list --task-id sha256:<hex>`
reconstructs the whole application from keeper's own journal. A JSON receipt
is written to `<project>/.code-indexer/receipts/<plan-hash>-<timestamp>.json`
and its path printed — losing the receipt loses convenience, not evidence.

**Post-apply** reparses every modified file and reports the parser's own
verdict per file: `parsed`, `parse_error` (with the offending lines) or
`parse_unavailable` when the language has no grammar here. This is a parse
verdict, never a compile verdict. The reparse writes nothing; `--refresh`
runs one incremental index pass after the report and the report says which
happened (`refresh: ran` / `refresh: skipped`).

## Vectors are an option, not a precondition

A pass can build a complete index with **no service reachable**. It writes
files, symbols, references, imports, the `chunks` table, `chunks_fts` and
`index_errors`, records `vectors=off`, and exits 0. Qdrant is a vector store
only: the search snippet is read from the manifest, never from the payload.

One option, not a command:

| flag | `add-project` | `index-more`, `reindex-project` |
|---|---|---|
| `--vectors auto` (default) | a NEW project records `off` | the project's recorded mode |
| `--vectors on` | embed and upsert as it parses | embed from stored chunks |
| `--vectors off` | rows only, no service | rows only, no service |

`auto` never silently enables embedding on a fresh install, and an existing
vectors-on project stays on across an upgrade. `watch` follows the recorded
mode and never turns vectors on by itself.

Turning vectors on for a project whose rows are current **reparses nothing and
reads no source file**: it embeds the stored chunk text and hash. A pass also
never mixes vectors across models: the composed embedding identity
(`model/dimension/embedded-text-format`) is stamped into the physical
collection name (`idx_<slug>__<identity digest>`), so a changed identity is a
NEW collection. **Every** vector-writing pass — a full pass, an incremental
pass or a vectors-only rebuild — fills that identity-stamped collection and
then binds the stable `idx_<slug>` alias to it, dropping the superseded one;
writes name the physical collection, reads resolve the alias. There is no
`__new` scratch name left and no plain-slug collection.

Three version axes, and which bump invalidates what:

| axis | recorded | a bump means |
|---|---|---|
| `extraction_version` | integer in `meta` | stored rows are obsolete: one pass per project (re-embeds from the cache) |
| `embedding_version` | one string, e.g. `qwen3-embedding:8b/4096/contextual-header-v1` | a NEW collection; rows and their chunk text survive |
| `index_schema_version` | integer in `meta` | the manifest migrates on the next WRITE open (a pass command, the watcher, `--refresh`); the collection is untouched |

**A read never migrates the manifest.** Every read opens it read-only: no
journal-mode write, no DDL, no migration, so a read can destroy nothing. A
manifest whose recorded schema is older than this build's is reported, not
upgraded — `index-status` prints `schema=<n>` (`manifest_schema` in `--json`,
where `schema` is the payload version), and every row surface refuses with the
same `RebuildRequiredError` shape and exit 4 until a pass runs. `rows=absent`
is a separate verdict: a manifest whose rows were written by this build's
extraction prints `rows=current` even when it records the older revision, so
`schema=<n>` does not imply `rows=absent`.

```
RebuildRequiredError: index for <path> was written at manifest schema 6 and this build reads schema 7; its rows are in an older layout, so this answer would describe an index this build would not produce; run: code-indexer reindex-project <path>
```

That migration is not free: a manifest predating the current DDL has its
symbol table re-created (the old key cannot be re-keyed), so the pass
repopulates those rows. Nothing is lost that a reparse cannot reconstruct —
but only a WRITE pass should perform it, which is why reads refuse.

`index-status` reports both sides: `rows=current|absent`, `vectors=on|off`,
`vectors_state=current|absent|stale|error`. `stale` means the recorded
embedding identity is not the one this build would write.

**`semantic-search` on a `vectors=off` project refuses** with one
`ConfigError` line naming the remedy and exit 4 — it never degrades to a
lexical answer and never returns an empty success. The refusal is decided
BEFORE the query is embedded, so it holds on a machine with no reachable
service too (an unreachable embedder would otherwise answer exit 3
`BackendError`, which names the wrong cause):

```
ConfigError: index for <path> was built with vectors=off; semantic search needs vectors; run: code-indexer reindex-project <path> --vectors on
```

`--mode lexical` still answers from `chunks_fts` without a vector; that is an
answer about text, not a silent substitute for a ranked one.

## The refusal contract (`needs-reindex`)

An index built by different extraction code is an index whose stored rows
this build would not produce. A manifest recorded at an older SCHEMA
revision is refused the same way (the message names the two revisions).
**Every surface that answers from those rows
refuses** — exit 4, stdout EMPTY, one stderr line:

```
RebuildRequiredError: index for <path> does not match the current configuration\(...\); the stored rows are obsolete, so this answer would describe an index this build would not produce; run: code-indexer reindex-project <path>
```

| refuses (exit 4) | stays answerable |
|---|---|
| `find-symbol`, `find-definition`, `outline`, `skeleton`, `overview`, `get-code-context`, `find-references`, `find-callers`, `find-callees`, `deps`, `changed-symbols` | `list-projects`, `lookup-project`, `index-status`, `doctor` |

The exemption is a property of the answer, not a favour: those four report on
the index itself and need no rows. A refusal is not a staleness warning — a
warning above an answer still reads as a fact about the code. `--refresh` on
a refusing command runs the pass first, so one refresh repairs the project and
the command then answers normally.

## Subcommand reference

- **add-project / lookup-project / list-projects** — registry management.
  `add-project` registers a repo and runs the initial index (foreground);
  `lookup-project` is a side-effect-free registration check. Sensitive
  files (.env*, keys, credentials*, and files matching high-confidence
  secret content patterns) are skipped by default and counted in
  index-status; `--allow-sensitive` stores the per-project override to
  index them too. `--include 'src/**'` (repeatable) scopes indexing to
  matching paths; `--priority` globs are indexed first within the pass;
  `index-more PROJECT SUBPATH` appends another subtree to the scope and
  indexes the newly covered files. `--vectors auto|on|off` picks the vector
  side; a NEW project records `off`, so the initial pass needs no service.
- **semantic-search** — natural-language search over indexed chunks.
  Best when you know *what the code does*, not what it's called. Follow up
  with `find-definition`/`get-code-context` for the precise lines.
  Ranking modes via `--ranking` (default `vector`, pure cosine):
  `metadata` adds small score adjustments (definition boost, test-path
  penalty) on top of cosine; `hybrid` fuses cosine with a lexical
  token-overlap score (better for queries containing exact identifiers) —
  pass `--ranking hybrid` when the query contains an exact identifier.
  Retrieval pipeline via `--mode` (default `hybrid`: dense + lexical RRF
  fusion — an eval win over dense-only; `dense` = embeddings only;
  `lexical` = FTS5 bm25, no Ollama call). On a project kept with vectors
  off, `dense` and `hybrid` refuse with one `ConfigError` line naming
  `--vectors on` and exit 4; `lexical` still answers from `chunks_fts`.
  `--rerank heuristic` is an
  opt-in token-overlap reranker (default `none`). In every mode pure-data
  chunks (`.json`/`.yaml`/`.toml`) are down-weighted and capped to at most
  40% of the top-k window so they cannot crowd out code for
  analysis-shaped queries; they stay searchable, and the mitigation is
  waived when the query names a data format. `--symbol-type` and
  `--language` scope the search by payload filters without extra round
  trips.
  Result shaping: same-file overlapping/adjacent hits merge into their
  union range (best score, symbols listed together); `--per-file N` caps
  hits per file; `--max-chars`/`--max-tokens` trim lowest-ranked hits to a
  budget (trailing dropped-count line, `truncated`/`dropped` in JSON);
  `--format compact` prints one line per hit
  (`path:start-end  symbol  score`) — prefer compact + get-code-context
  for agent loops. The default is ONE project — the containing project
  inferred from the invocation path; `--all-projects` fans out. The query
  makes one embedding attempt with a short timeout (no retry chain) and the
  fan-out stops at a 30 s wall, reporting the projects it did not reach as
  `skipped` with `reason=budget` (`partial: true` in JSON). A fanned-out
  answer fuses per-collection ranks with Reciprocal Rank Fusion
  (`rrf_score`, `vector_score` keeps the original cosine).
  **`--language` values (exhaustive)** — the stored `lang` payload, derived
  from the file extension at index time; filter matches it exactly:
  - AST-parsed via tree-sitter (structured chunks with symbol names):
    `python`, `javascript`, `typescript`, `rust`, `go`, `java`, `c`, `cpp`,
    `csharp`, `ruby`, `php`, `bash`, `lua`, `swift`, `kotlin`, `markdown`,
    `json`, `yaml`, `toml`, `html`, `css`.
    NOTE the one map mismatch: `.sh` files PARSE via the `bash` grammar but
    the stored payload value is `shell` — filter `.sh` with
    `--language shell`. C/C++/Java carry weak visibility (everything
    public) by design; other grammars carry real visibility.
  - Fallback-only (regex-window chunks, no AST symbols; stored as the raw
    extension string): any other extension — observed values `gitignore`,
    `lock`, `python-version`, `csx`, `text`. Still valid filter values.
- **skeleton** (alias `map`) — token-reduction workhorse. Dense structural
  map of the whole project or a `PATH_PREFIX` subtree: one file header per
  group, one symbol per line (`type name:start-end  signature`). No bodies,
  no prose — a whole-repo orientation layer in a few hundred–few thousand
  tokens. `--tree` instead prints a directory tree with per-dir symbol
  count + dominant language; `--no-signatures` drops signature text;
  `--limit N` caps symbol lines per file. Call this **before any
  exploratory reads** in an unfamiliar repo.
  Project auto-resolution: when `--project` is omitted, the positional
  `PATH_PREFIX` (then the cwd) is matched against registered projects —
  the most-specific containing project wins. So
  `code-indexer skeleton . --tree --limit 6` works from inside a
  registered repo even with multiple projects registered; `.`/absolute
  prefixes are normalized to project-relative. The old
  single-project/ambiguity error only fires when nothing matches.
- **outline** (alias `file-outline`) — one file's top-level declarations,
  signatures, and line ranges, one per line. Use to decide whether a full
  `get-code-context` call is worth it. `--docstrings` adds the first
  docstring/comment line per declaration (the only subcommand that reads
  source at query time).
- **find-symbol** — symbol lookup by exact name (every definition of it; a
  capped substring fallback only when the exact name has no row), or **browse
  mode** with no name: filter by `--type`, `--file`, `--limit` to list "what
  exists where" (browse cap 25). Output includes the persisted signature; the
  JSON rows also carry `qualified_name`, `container`, `language`, `decl_hash`
  and the revision-scoped `symbol_id`. `--type` is an alias of `--symbol-type`
  (both accepted).
- **find-definition** — exact-name definition location only: every row, no cap.
- **find-references** — all references TO a symbol from the SINGLE reference
  store (schema v6): one writer, one row shape, one honest relationship and
  `confidence=` per row. Tree-sitter rows (`call`/`type`/`base_class`/
  `member`/`includes`, confidence `ast`) for `python`/`typescript`/`tsx`/
  `javascript`/`c`/`cpp`/`go`/`csharp` — `member` is a member access read such
  as a C# property/field, and `call`/`member` rows record `receiver=` (the receiver
  expression as written) when it is a plain identifier. Only the OUTERMOST
  member access of a chained access is a row: `System.Console.WriteLine(x)`
  yields `WriteLine` and no row for `Console`/`System`; the surviving row of a
  chain deeper than one hop carries no receiver (its receiver expression is
  itself a member access, not a plain identifier). Rows are keyed by
  `(file, line, to_name, relationship)`, so two same-name member reads on ONE
  line collapse to the first in line order — put the two reads on separate
  lines when both receivers matter. Plus a textual scan
  for every other language (confidence `heuristic`): an identifier followed
  IMMEDIATELY by `(` is a `call` row, any other identifier is an `identifier`
  row (one row per occurrence — a call site is not also reported as an
  identifier), and an import/include line yields an `includes` row. The
  textual scan skips lines that BEGIN with `#`, `//`, `*` or `--`; trailing
  comments and strings DO match, so those rows are heuristic and may be false
  positives (a `name(` in a string or a trailing comment still matches). A
  call is stored once, so a same call never appears twice. A bare
  `no references to X` is printed only when the store has none at all — a
  filtered report that comes back empty instead says so and names the
  unfiltered count and the relationships that do carry the name
  (`no calls references to 'X'; 12 references exist unfiltered (identifier,
  type)`); that check is one aggregate query, not a second report.
  `includes` is an import/include directive: an AST language records its
  import target (python module name, TS/JS module specifier, Go import path,
  C# `using` target, C/C++ quoted include) as an `includes` row AND as an
  `imports` row, while a textual scan records its import/include line. For
  TS/JS that target comes from an `import` statement or a `require(...)` call
  specifically — an ordinary string-argument call (`alert("hi")`) is neither
  an `imports` nor an `includes` row. Angled C/C++ system headers stay
  unresolved and produce no row.
  `--relationship` filters the reported vocabulary
  (`calls`|`includes`|`inherits`|`references`; `inherits` selects the
  `base_class` rows, `references` the `type`/`member`/`identifier` rows).
  This answers a different question than `find-callers`, over the same store.
- **find-callers / find-callees / deps** — graph traversal over `refs` and
  the import graph. `find-callers` walks fan-in edges, `find-callees` fan-out,
  `deps` the resolved import edges around one file; all are BFS with depth and
  node caps, cycle-safe. The graph is TEXTUAL AND UNBOUND by design: no
  compiler resolution, names are matched as text, so a heuristic `identifier`
  row is a navigation aid, not a resolved call. `find-callers NAME` accepts a
  dotted subject (`Type.Member`): the qualifier resolves against the symbol
  rows, the fan-in is matched on the trailing member name, and the subject is
  reported once on the `note:` lines. A direct hit carries `receiver=<expr>`
  only when the store holds one for that row (a member access records the
  receiver expression as written); transitive nodes never carry a receiver.
  Symbol extraction covers
  `method`|`class`|`function`|`enum`|`interface`|`struct` only, so properties
  and fields have NO definition row: when the subject has no definition,
  `find-callers` seeds the BFS from the `refs` rows and says so plainly on its
  `note:` lines instead of printing a bare `no results` (which would read as
  "nothing calls this").
  **Node identity differs by direction, deliberately.** A `find-callers` node
  is the call SITE — `(file, line, from_symbol, relationship)` — so two calls
  to the same target on two lines of ONE function are two hits, each with its
  own line and its own receiver, at every depth (a transitive site is not
  swallowed either). Enclosing-symbol identity used to collapse the second
  one into the first, silently (issue #21). A `find-callees` node is the
  TARGET — `(file, to_name)` — because that walk answers "what does this
  symbol reach", so one target reached twice is one callee. Definition roots
  in both walks are `(file, symbol)`.
  **An incomplete graph answer says so — no graph-walk cap is silent.** A node
  cap does not just stop adding nodes: every edge the walk examined and
  refused is counted. In `--json` the payload is unchanged apart from two new
  top-level siblings — `"truncated": <bool>` (the answer is incomplete) and
  `"omitted": <int>` (edges refused at the node cap; `0` when nothing was
  refused) — beside `roots` (`deps` keeps its per-level `in`/`out` dicts and
  gains the same two fields). `truncated` is the completeness flag; `omitted`
  counts node-cap refusals only and is `0` on a depth-capped answer, so read
  `truncated`, never `omitted`, to decide whether an answer is complete. The
  store reads beneath these walks cap nothing at all — the store hands over
  every row and the walks own their bounds (see "Store reads" below) — so a
  short answer is a cap this walk applied and announced.
  **Definition roots are one of those bounds.** Both walks read EVERY
  definition of the subject (40 `__init__` rows on a live index, not the first
  25) and then spend the node budget on them. When the root set alone exceeds
  `--max-nodes` the walk shows the first `cap` roots, sets `truncated`, and
  prints `note: 40 definition roots exist for this subject, node cap 10 — 30
  roots not shown; raise --max-nodes`. Those refusals are NOT counted in
  `omitted` — a definition list is not an edge list — but `omitted` is not
  thereby `0`: a root set that spends the whole budget leaves no slot for
  level-0 edges, and those refusals ARE counted (on this tool's own index,
  `find-callers __init__ --project code-indexer --max-nodes 10 --json` answers
  `roots 10, truncated true, omitted 124`). So read `truncated` for
  completeness and never infer either field from the other. In the
  text tree the result gains a `note:` line naming the cap and the count, e.g.
  `note: node cap 4 reached — 61 more reference edges not shown (a lower
  bound: a refused edge is counted but never expanded, so its own edges are
  not walked, and a depth cap on the same answer can cut more); raise
  --max-nodes to see more`. The count is what the walk itself saw, never a
  claim about the whole population. A depth cap that stops a frontier which
  still has unvisited edges adds one line — `deeper references exist beyond
  depth 2` (callers), `deeper callees exist beyond depth 2` (callees), `deeper
  out import edges exist beyond depth 2` (`deps`) — because a depth-bounded
  answer is incomplete too. These lines print on stdout with the answer, even
  when rows were printed; the explanatory notes of a complete answer stay on
  stderr. An unknown name is not reported as truncated: nothing was refused.
- **get-code-context** — the only way to read actual source: a line range
  (`--start-line/--end-line`) or the lines around a symbol
  (`--symbol Foo::bar`), optionally padded with `--context-lines N`. Never
  read whole files; this answers "show me the code" questions. Symbol mode
  resolves the name from that file's own rows — the name and the file are
  filtered in one uncapped read — so a row sorting past `find-symbol`'s
  25-row default is still found, and EVERY matching symbol row in that file
  is returned. Ranges of the requested name that **overlap or touch** are
  then merged into one contiguous span per run, so a decorated definition —
  stored as two rows, the decorator line and the declaration line, with the
  same end line — answers as ONE segment with one range, while genuinely
  disjoint ranges stay separate. The rows' own ranges are merged **first**,
  and the `--context-lines` padding is applied to each surviving span
  afterwards: identity is decided on the definitions, so padding never
  changes how many segments answer. The merge is part of the answer, never a
  hidden convenience: counting segments counts definitions. Spans are listed
  in start-line order. Padded spans of nearby definitions may overlap: a
  segment is a padded definition, not a disjoint window, so concatenating
  segment bodies can repeat lines. `--context-lines` never changes how many
  segments answer. Each segment in symbol mode also carries `symbol_rows`,
  the number of stored rows it merged, so the merge is auditable rather than
  silent; a definition nested inside another of the same name is absorbed by
  the outer span.
- **index-status / doctor / sweep-orphans / reindex-project / remove-project / watch / unwatch** — index
  lifecycle. Two state vocabularies (see "State meanings" above): the
  **pass state** `idle | indexing | never-indexed | error` describes indexing
  passes; the **fingerprint state** `ok | needs-reindex | unverified`
  describes whether the index was built by the current configuration.
  `index-status` prints the pass
  state as `state=`, the manifest's recorded schema as `schema=<n>` (`none`
  when there is no manifest yet), and, when the fingerprint does not match, adds
  `fingerprint=needs-reindex reason=<diff>` — the same verdict
  `list-projects`/`lookup-project`/`doctor` give, so the surfaces never
  contradict each other about whether a rebuild is owed. When the running
  configuration cannot be probed (the embedder is unreachable) the verdict is
  `unverified` and `index-status` prints
  `fingerprint=unverified reason=embedder-unreachable` — that third value is
  never folded into the pass state, and a missing `fingerprint=` field means
  the index matched. Meanings and agent
  action:
  `indexing` — a pass is running; wait/retry.
  `never-indexed` — registered but no completed pass (an interrupted
  `add-project`); do not trust empty results, tell the owner
  (`reindex-project` builds it).
  `needs-reindex` — index built with a different embed model/dimension/
  text format/chunker or row-extraction version; `search` fails with
  `RebuildRequiredError` naming the difference (one line on stderr, exit 4);
  do not run `reindex-project`
  yourself, report to the owner.
  `error` — run `doctor`.
  `idle` — normal. `reindex-project` is a full rebuild into a temporary
  collection, swapped in on completion (also populates schema-v3
  signature/visibility columns on old manifests);
  `--skip-stale-check` does not bypass the needs-reindex gate;
  `remove-project` is destructive; `watch` is the background freshness
  daemon; `unwatch` is the reverse of `watch` (details in "watch details").
- **doctor** — installation health check, one line per check
  (`ok|warn|fail <name>: <detail>`, exit 1 on any fail; warnings do not
  fail). Covers runtime versions, Ollama (reachable/model/dimension probe),
  Qdrant (reachable/version/client-vs-server compatibility, collections vs
  registry, dims), INDEX_ROOT writability + disk, SQLite integrity, watch
  pidfile + locks, and per-project fingerprint status. The `watch` line
  reports liveness from the pidfile **flock** — a held flock is `running
  (pid <pid>)` (or `running (pid unreadable)`), and only a free flock with a
  pidfile present is `stale pidfile`; a pidfile that cannot be opened at all
  is reported as a distinct warning (`liveness unknown ...`), never as `stale`
  or `running`. The probe opens the pidfile **read-only**, so a
  readable-but-not-writable pidfile is still probed correctly. The fingerprint
  line names the live watcher when a mismatch is present. Dev/ops surface —
  CLI-only, no MCP tool; agents use index-status instead. Run it when
  searches misbehave or after changing OLLAMA_URL/QDRANT_URL/EMBED_MODEL.
- **sweep-orphans** — clear residue rows left by a file deletion. A row in
  `refs`, `imports` or `symbols` whose `file` has no `files` row **and** whose
  file is absent from the working tree is residue. The command is a **dry run
  by default**: it prints the per-table counts and the file list it would
  clear and changes nothing; `--apply` removes them in one transaction and
  reports the count. It reads the manifest directly (no index pass, no
  Ollama). The tree check is a guard — "no files row" and "the file is gone"
  are different predicates; the dry run plus the reported count is the real
  safety property. A path with rows but no `files` row that IS present on
  disk is reported and left alone. New deletions no longer create `imports`
  residue (the delete path purges `imports` too), but this sweep clears what
  earlier deletions left behind. CLI-only (no MCP tool); the owner decides
  when to `--apply` on a live index.
- **plan-replace-body / plan-insert-symbol / plan-delete-declaration** —
  prepare a plan: one JSON document written into `<project>/.code-indexer/`
  `plans/`, named by the sha256 of its canonical bytes. `--text` or
  `--body-file` carries the replacement block (LF only; a body file's CRLF is
  translated at the input boundary). `--file` narrows the query to one live
  file; `--kind` and `--container` narrow it further. `--print` emits the
  document on stdout and writes nothing at all. Nothing is applied: no source
  file is touched by any of these commands.
- **plan-validate** — report, per check, what the plan claims against the
  tree: plan hash, schema, paths, file hash and declared newline, node
  re-derivation, line/byte cross-check, region hashes, overlap and ordering,
  and the parser's verdict per file. Writes nothing. A stale file is
  `PlanStale` with exit 5, an overlap `OverlappingEdits` with exit 2, an
  ambiguous node `AmbiguousNode` with both candidates listed, and a plan that
  asks for a semantic promise `SemanticProviderUnavailable` with exit 3.
  `--json` carries the whole report; the text mode prints one line per check
  followed by the caveat that this is not a compile check.
- **plan-show** — print the plan as read (unknown members preserved; reading
  never rewrites it), and with `--diff` print every affected file with the
  unified diff computed from the plan plus the current bytes. Writes nothing;
  a stale file refuses rather than drawing a diff against bytes the plan
  never saw.
- **plan-apply** — apply the plan's edits through filekeeper and report every
  file. `--dry-run` runs the same pre-pass and the same report and writes
  nothing at all; the keeper `--dry-run` goes to every call. It is a write
  command: the only command in this roster that mutates a source file. A
  pre-pass refusal is exit 5 with zero mutations, a mid-loop failure is exit 1
  `PartialApply` with the report on stdout, an absent keeper is exit 3
  `KeeperUnavailable` and never a fallback. `--refresh` runs one incremental
  pass after the report and the report says so.

## Token reduction (schema v3)

Signatures + visibility are extracted at index time (schema v3;
`signature`/`visibility` columns on `symbols`; v2 manifests ALTER-migrate,
old rows keep NULL — `reindex-project` fills them). `skeleton`/`outline`/
`find-symbol` are manifest-only at query time: zero re-parsing. Dense
output: one symbol per line, no blank lines; `--json` for structured
consumers. `outline --docstrings` is the only on-demand source read.
C/C++ visibility is weak by design (everything public — access sections
are not tracked); do not oversell it.

## watch details

- `--duration T` bounds its life: exits 0 after T seconds (`0`/omitted =
  forever; honored by `--background` too). SIGINT/SIGTERM exit 0.
- Degradation: no watchdog installed or inotify watch-descriptor
  exhaustion -> quiet-period polling fallback (hash scan per project per
  quiet tick). Correctness is never lost, only latency. The failure that
  actually occurs when a scheduled root cannot be watched (raised from
  either the schedule call or the observer's `start`) degrades to polling
  rather than exiting.
- A registered project whose root is **missing or unreadable** is skipped
  with exactly one warning naming it; the remaining projects are still
  served and the watcher reports the projects it covers. It exits nonzero
  only when it covers none.
- `--background` prints `watch daemon started` only after re-confirming the
  daemon is alive and still holding the pidfile: a daemon that died on entry
  is reported as a failure with a nonzero exit, never a false start.
- `--foreground` (default) is the plain inherited-stdio behavior, and takes
  the SAME flock-guarded `<INDEX_ROOT>/watch.pid` as `--background`, so "one
  watcher per index root" holds in both modes.
- Projects: repeatable path/slug/name args, or `--all` (round-robin).
  Never pass both.
- A second watcher is refused while a live one holds the PID-file flock.
- **Version bumps need an ordered restart.** A manifest's recorded
  fingerprint (model, dim, embed-text format, `CHUNKER_VERSION`,
  `EXTRACTION_VERSION`) describes the code that built it, and a watcher
  re-stamps its OWN fingerprint on every sweep — so a watcher left running
  across a version bump silently reverts the `needs-reindex` flag and can
  re-embed with its older extraction code. After upgrading the code:
  `unwatch --all` (stop all watchers) -> `reindex-project <path>` per
  project (rebuild) -> `watch --all --background` (restart on the new code).
  `doctor` names the running watcher when a fingerprint mismatch is present.
- `unwatch [PATH...|--all] [--timeout S]` is the reverse: it SIGTERMs the
  live watcher holding `<INDEX_ROOT>/watch.pid` (either mode), waits for the
  flock to be released (default 10 s), and reports the projects that watcher
  was serving. The flock — never pid existence — is the liveness test, so a
  stale pidfile left by a killed watcher is cleaned up silently. No watcher
  running is a clean no-op (one-line message, exit 0); exit 1 when a live
  holder could not be stopped (`--timeout` elapsed, or its pid is unreadable),
  or when a pidfile that exists could not be probed (liveness unknown —
  absence is not claimed). Arguments mirror `watch`: paths or `--all`, never
  both.

## Project selection

Project-scoped commands accept the project as a path, slug, or registered
custom name. Two selector spellings exist:

- `--project X` — accepted by every project-scoped command.
- `--name X` — a dual name for the same option on `skeleton`/`map`,
  `outline`/`file-outline`, `find-symbol`, `find-definition`,
  `find-references`, and `get-code-context` (typer dual option names).
- `skeleton`/`map` additionally auto-resolve when `--project` is omitted:
  the positional `PATH_PREFIX` (then the cwd) is matched against registered
  projects; the most-specific containing project wins.

Lifecycle commands (`reindex-project`, `remove-project`, `lookup-project`,
`index-more`) and `index-status` accept either spelling: the historical
positional path, or `--project` / `--name` (path, slug, or custom name).
For `reindex-project`, `remove-project` and `lookup-project` the flag wins
when both are given; `index-more` accepts `<project> <subpath>` or
`--project <project> <subpath>` and rejects the flag plus two positionals as
ambiguous. None of these mutating commands has an MCP tool: the MCP
surface is read-only (see README's parity list). Mutating commands
(`reindex-project`,
`remove-project`, `index-more`) never infer a project from the cwd — with
neither spelling they stop and list the registered projects. `index-more`
takes the subpath as its positional after either the project path or the
flag.

## Store reads (no silent caps)

Under these walks the manifest store hands over everything it holds.
`Manifest.callers_of`, `callees_of_file_symbol` and `import_rows` take
`limit: int | None = None`, and `None` — the default — means the **whole store
read**, not a large number; `Manifest.definitions_of` — the exact-name
definition read behind a walk's roots, a dotted subject's qualifier and the
definer lookup — has no limit parameter at all, so it always reads every row.
A cap belongs to the layer above, which can announce it: the graph walks count
what their `--max-nodes` refused in `omitted` and name a capped definition-root
list in a note, `overview` computes hotspots from one SQL aggregate instead of
a row sample, and `changed-symbols --impact` reports its own per-name read and
shared node budget as `truncated`/`omitted` per file, plus which of the two
fired. An explicit `limit` stays available to a caller that wants a bounded
read — and that caller is then responsible for saying so in its own output.
The claim is about these walks' reads, not about every read in this module:
`refs_to` and the browse/substring tier of `find_symbols` keep their own
25-row defaults, as the next paragraph says.

Two caps are a surface's own default, not a sampling of a store read, and
they stay: `find-references --limit` (25) and `find-symbol`'s browse mode and
substring fallback (25, `--limit` honoured). Exact-name `find-symbol`,
`find-definition`, the graph's definition roots, the qualifier resolution and
the definer lookup read every row.

The capped reads' order keys are total, so a capped read is a reproducible
prefix of the full read rather than a query-plan lottery: `callers_of` orders
`(file, line, relationship, to_name, receiver)` and `import_rows` orders
`(file, line, raw_target)` — the tables' primary-key column sets, plus the
receiver for refs.

## Key rules

- `--skip-stale-check` is accepted by every command and is **inert**:
  reads never index, so the default is to REPORT staleness without repairing
  it (see "Commands" and "Staleness, refresh and exit codes"). `--refresh`
  is the only flag that turns a read into a write: it takes the per-project
  flock, runs one incremental pass and answers from the result; a held lock
  is `LockedError` with exit 5. `--fresh` is accepted on the seven commands
  listed in the Global flags paragraph; only on `get-code-context` does it
  still mean "read the live file", and it never writes.
- A read answer describes the index as of `meta.last_indexed`. Every read
  that answers from the index prints at most one stderr `Warning: index is
  stale (...)` line (nothing when fresh, one "did not run" line when the
  verdict is unknown) and carries the staleness block in JSON. The repair is
  `--refresh` or a pass command, never an implicit write.
- `get-code-context` checks the live file hash against the manifest:
  `--symbol` mode re-resolves the symbol on the live file, and otherwise
  answers from that file's own symbol rows (name and file filtered in one
  uncapped read, every matching range returned — a definition past
  `find-symbol`'s 25-row default is not reported as absent — and then the
  ranges of that one name that overlap or touch are merged into one span per
  contiguous run, so a decorated definition answers as one segment; the rows'
  own ranges merge first and `--context-lines` padding is applied to each
  merged span after, so padding never changes the segment count); line-range
  mode on a changed file returns the requested lines plus a one-line
  `Warning: ...` on stderr (exit 0) and `stale: true` in JSON. Search hits
  carry `file_hash` and `indexed_at`.
- Workflow: `semantic-search` -> `find-symbol`/`find-definition` ->
  `get-code-context --symbol`. Never read whole files.
- First index of a big repo is slow (~1.2 docs/s GPU-hosted); check
  `index-status` before trusting thin results.
- Never manage index state yourself (manifests, chunk hashes, collections) —
  the tool owns them.
- Env (resolved CLI flags > env vars > defaults):
  `OLLAMA_URL`, `QDRANT_URL`, `EMBED_MODEL`, `INDEX_ROOT`
  (default `~/.code-indexer`), `STALE_TTL` (RETIRED — still parsed into
  `Config.stale_ttl` for compatibility and consumed by nothing: a read
  reports its verdict from the stat scan and never probes or repairs, so the
  TTL governs no behaviour; repair is `--refresh` or a pass command),
  `EMBED_BATCH` (48 texts per Ollama embed request), `UPSERT_BATCH`
  (256 points per Qdrant upsert), `MAX_FILE_BYTES` (default 1048576; larger
  files skipped), `CHUNK_MAX_CHARS` (chunk cap, default 1000),
  `WATCH_DEBOUNCE` (`watch` quiet period, default 3 s; alias
  `WATCH_QUIET_PERIOD` wins when both set), `WATCH_SWEEP_INTERVAL` (periodic
  full sweep, default 300 s), `OLLAMA_TIMEOUT` (Ollama connect/read timeout,
  default 120 s; transient failures retry with exponential backoff + jitter,
  max 5; fatal errors — e.g. model not found — fail fast with a ConfigError),
  `EMBED_CONCURRENCY` (default 1; >1 pipelines parsing while embedding),
  `QUERY_INSTRUCTION` (query-side embedding instruction, ON by default
  after an eval win; `QUERY_INSTRUCTION=""` disables; documents are never
  instructed — toggling needs no reindex), `EMBED_CACHE` (default `1`/ON =
  content-addressed embedding cache at `<INDEX_ROOT>/embed_cache.db`;
  unchanged chunks/queries skip Ollama), `EMBED_CACHE_MAX_GB` (default 2;
  LRU eviction by last_used), `PARANOID_HASH` (default OFF; `1` disables the
  stat fast-path and forces full content hashes), `VERBOSE` (default OFF;
  verbose diagnostics on stderr). Defaults for OLLAMA_URL/QDRANT_URL point
  at LAN placeholder hosts and must be set explicitly.

## Gotchas

- `remove-project` deletes the whole index; re-adding re-indexes from scratch.
- Existing `idx_*` collections are reused if the registry is retained; a fresh
  registry re-add re-attaches to the same collection and re-indexes (chunk-hash
  diff means only changed chunks are re-embedded; deterministic uuid5 point IDs
  make re-upserts idempotent).
- `.gitignore`/`.codeindexignore` are honored; files >1 MB and binaries skipped.
- Branch switches re-index correctly (content-hash diff, mtime ignored).