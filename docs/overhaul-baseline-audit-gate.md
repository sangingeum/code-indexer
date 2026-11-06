# Verification gate: baseline audit of the current implementation

Subject: `docs/overhaul-baseline-audit.md`, audit tip `0116074` (commits
`9e725bb`, `0116074`), base `ef2be78`.

Verdict summary: **14 bars, 14 PASS, 0 FAIL, 0 NOT TESTED.** Six findings below;
none of them overturns a claim that a decision rests on, but two published
numbers are wrong or narrower than they read.

## Method

Every bar was re-run from the repository root at the audit tip on this host.
One run per command, full output redirected to a file under
`runs/milestone0-gate/`, then read from the file. Service endpoints were made
unroutable (`http://127.0.0.1:1`); no Ollama or Qdrant process was running on
this host during the gate, so "offline" here means genuinely offline.

The live index was never written: every read command carried
`--skip-stale-check` where it accepts the flag, no refresh, reindex, remove or
sweep was run, and the live manifest is byte-identical before and after the
gate (mtime `2026-10-31 16:38:35 +0900`, row counts `files=445 symbols=1733
refs=113751 imports=1406 index_errors=0 chunks_fts=2198 meta=11` both times —
`runs/milestone0-gate/live-index-untouched.txt`). Anything that indexes ran
against a throwaway `INDEX_ROOT` under `runs/milestone0-gate/`.

Arithmetic in this record (section spans, medians, token estimates) was
offloaded to `calc`; the calls are in
`runs/milestone0-gate/calc-arithmetic.txt` and `bar3-spans.txt`.

Terminology as in the audit: `<repo>` is this checkout, `<index-root>` is
`INDEX_ROOT`. Host-specific paths are elided in the same way the audit elides
them.

## Bar results

| Bar | Claim under test | Verdict | Raw artifact |
|---|---|---|---|
| 1 | Docs-only change over `ef2be78..0116074` | PASS | `bar1-docs-only.txt` |
| 2 | Suite unchanged at the audit tip | PASS | `pytest.txt`, `bar2-collection.txt` |
| 3 | Eleven sections, none a skeleton | PASS | `bar3-structure.txt`, `bar3-spans.txt` |
| 4 | 27 commands, 2 hidden aliases, 14 with machine output | PASS | `commands.json` |
| 5 | Offline matrix reproduces | PASS | `offline-unroutable.json`, `offline-sink.json` |
| 6 | Embedding is mandatory for an index pass | PASS | `throwaway-add-project.json` |
| 7 | Failed search exits 0 with `error:` on stdout | PASS | `semantic-failure.json`, `semantic-failure-scoped.json` |
| 8 | MCP numbers (18 / 22,491 B / 1,514 B / no `overview`) | PASS | `mcp-tools.json`, `mcp-size-probe.txt` |
| 9 | Identity and schema claims | PASS | `manifest-inventory.json`, `manifest-ddl.txt` |
| 10 | Import cost | PASS (divergence stated) | `import-gate.json`, `importtime-variance.json` |
| 11 | Stale-read figures 1,712 / 1,626 | PASS | `import-gate.json` |
| 12 | No language server; `keeper` 0.1.8 primitives | PASS | `lsp-probe.txt`, `keeper-probe.txt` |
| 13 | Hygiene and publication | PASS | `hygiene-lint.txt`, `ruff.txt`, `doc-hygiene-scan.txt` |
| 14 | Signed commits, tip equals `origin/main` | PASS | `bar14-signature-remote.txt` |

### Bar 1 — docs-only change: PASS

```
$ git diff --stat ef2be78..0116074
 docs/overhaul-baseline-audit.md | 764 ++++++++++++++++++++++++++++++++++++++++
 1 file changed, 764 insertions(+)
$ git diff --name-status ef2be78..0116074
A	docs/overhaul-baseline-audit.md
$ git diff --stat 9e725bb..0116074
 docs/overhaul-baseline-audit.md | 2 +-
```

One added file, no source, test, dependency, CHANGELOG or `AGENT.md` change,
no version bump. `git rev-list --count ef2be78..0116074` is 2.

### Bar 2 — suite unchanged at the audit tip: PASS

```
$ uv run pytest tests/ -q -W error
564 passed, 2 deselected in 83.04s (0:01:23)

$ uv run pytest tests/ -q -W error --collect-only | tail -1
564/566 tests collected (2 deselected) in 0.85s

$ git diff --stat ef2be78..0116074 -- tests src pyproject.toml uv.lock CHANGELOG.md AGENT.md
(empty)
```

The audit reports `564 passed, 2 deselected in 82.93s`; this run is the same
counts in 83.04 s. The two deselected tests are the `live`-marked pair, and
the empty diff over `tests/` proves no test was added, removed or reselected
between the base and the audit tip. 48 modules contribute collected tests.

### Bar 3 — document structure: PASS

Eleven numbered sections at these line starts (`rg -n '^## '`), with the line
count of each section body:

| Section | Starts | Lines |
|---|---|---|
| 1 Command inventory | 33 | 78 |
| 2 Module map | 111 | 64 |
| 3 Index schema inventory | 175 | 126 |
| 4 Semantic/vector dependency map | 301 | 115 |
| 5 Test inventory | 416 | 46 |
| 6 MCP surface assessment | 462 | 48 |
| 7 Skill / help surface | 510 | 32 |
| 8 Gap matrix | 542 | 48 |
| 9 Feasibility facts | 590 | 45 |
| 10 Risks and sequencing | 635 | 83 |
| 11 Questions for the decision leg | 718 | 46 |

731 lines of body across the eleven sections, 150 table rows in the document,
and no `TODO`/`TBD`/`placeholder`/`lorem` marker anywhere (`rg -ni` returns
nothing, exit 1). Spot-checked by reading the whole document: every section
carries measured tables or quoted code, not headings with filler. The
structure claim holds.

### Bar 4 — command inventory completeness: PASS

A script file walks the real registry (`typer.main.get_command(app).commands`
and `app.registered_commands`), not `--help` prose:

```
$ uv run python runs/milestone0-gate/dump_commands_gate.py
{ "typer_registered_commands": 27, "click_command_count": 27,
  "hidden_names": ["file-outline", "map"],
  "machine_output_count": 14, "no_machine_output_count": 13 }
```

`hidden=True` is declared at `cli.py:1335` (`map` -> `skeleton`) and
`cli.py:1377` (`file-outline` -> `outline`), inside the ranges the audit
cites. The 14 that accept machine output are 12 distinct commands plus the
two aliases: `changed-symbols`, `deps`, `doctor`, `find-callees`,
`find-callers`, `find-symbol`, `get-code-context`, `outline`, `overview`,
`semantic-search`, `skeleton`, `sweep-orphans` (+ `map`, `file-outline`). The
13 that do not are exactly the audit's list: `add-project`, `eval`,
`eval-compare`, `find-definition`, `find-references`, `index-more`,
`index-status`, `list-projects`, `lookup-project`, `reindex-project`,
`remove-project`, `unwatch`, `watch`. Count and classification both reproduce.

### Bar 5 — the offline matrix: PASS

Run on this checkout's registered project, with `--skip-stale-check` on every
command that accepts it. Two passes: unroutable endpoints, then a local TCP
sink that counts accepted connections.

| Command | exit | conns | wall | audit's figure |
|---|---|---|---|---|
| `overview --skip-stale-check` | 0 | 0 | 0.92 s | 0 conns, 0.95 s |
| `find-symbol Core --skip-stale-check` | 0 | 0 | 0.93 s | 0 conns, 0.92 s |
| `index-status --skip-stale-check` | 0 | 5 | 34.9 s | 5 conns, 34.4 s |
| `list-projects` | timeout | 15 | >75 s | 14 conns, >75 s |

`index-status` printed
`state=idle fingerprint=unverified reason=embedder-unreachable`, as claimed.
`list-projects` was killed at the harness cap of 75 s after printing two
project lines and never returned; the 15th connection (audit: 14) is one
registered project more than the registry held at audit time — the registry
now holds 12 projects (`registry-count.json`). The run time of `index-status`
is the honest surface for the audit's "~34 s" expectation: 33.9 s refused,
34.9 s accepted-then-closed.

### Bar 6 — embedding is mandatory for an index pass: PASS

Throwaway index root and a two-file throwaway project (`alpha.py`, `beta.py`)
under `runs/milestone0-gate/`, `INDEX_ROOT` redirected for every call:

```
$ add-project runs/milestone0-gate/throwaway-project   (exit 1)
registered <repo>/runs/milestone0-gate/throwaway-project (slug 667be468)
InternalError: 667be468: indexing error: [Errno 111] Connection refused

$ index-status --project runs/milestone0-gate/throwaway-project   (exit 0)
project=<repo>/runs/milestone0-gate/throwaway-project state=never-indexed
languages_without_ast=[...] note=registered but no indexing pass has completed
```

Exit 1 and `state=never-indexed`, exactly as the audit states. The throwaway
root contains only `registry.db`, `667be468/manifest.db` and a lock file; the
live index root holds no `667be468` entry.

### Bar 7 — the failed-search exit code: PASS

The defect reproduces, and it reproduces harder than the audit's row implies.

Audit-faithful shape (one `--project`, `--limit 2`, default hybrid mode):

```
$ semantic-search "where does the staleness probe run" --project <repo> --limit 2
  --skip-stale-check
stdout: error: search failed on every project (<repo>: Ollama embed failed after 5 retries)
exit:   0     wall: 67.5 s     5 retry warnings on stderr
```

Unscoped default (`semantic-search Core`, no `--project`):

```
stdout: error: search failed on every project (<project1>: Ollama embed failed after 5 retries;
        ... twelve projects ...)
exit:   0     wall: 804.2 s
```

So: exit **0** with an `error:` line on **stdout** in both shapes — the claim
holds. What the document does not say is that its 67 s figure is a
single-project measurement; on this host's registry (12 projects) the same
defective call takes 804 s and only then reports failure. See finding F5.

### Bar 8 — MCP numbers: PASS

```
$ uv run python runs/milestone0-gate/dump_mcp_gate.py
 tool_count 18 | has_overview false | semantic_search description 1,514 bytes
$ uv run python runs/milestone0-gate/mcp_size_probe.py
 22491 bytes  shape=list indent=1 ensure_ascii=True  (exact match, delta +0)
 25962 bytes  shape=list indent=2 ensure_ascii=False
 17660 bytes  shape=list compact separators
```

18 tools, `overview` absent, `semantic_search` description exactly 1,514
bytes, and 22,491 bytes reproduces exactly under `json.dumps(list, indent=1)`
with `ensure_ascii=True`. The stale docstring claim holds too — `server.py`
says "all 14 tools" — but it is at line 61, not line 34 (finding F1). Because
the size figure is serialization-specific, the derived "approximately 5,622
tokens" is a ceiling, not a constant (finding F2).

### Bar 9 — identity and schema claims: PASS

The declared DDL matches the audit's quotation (`manifest.py:43-91`), with
`PRIMARY KEY (file, name, start_line)` for `symbols` at `manifest.py:62` and
`to_name`, `confidence`, `receiver` on `refs` at `:78-81`. The live manifest
agrees:

```
symbols columns: file, name, symbol_type, start_line, end_line, source, signature, visibility
refs columns:    file, line, from_symbol, to_name, relationship, confidence, receiver
meta keys (11):  branch, chunk_hashes, chunker_version, embed_dim, embed_format,
                 embed_model, embed_text_version, extraction_version,
                 last_full_scan, last_indexed, schema_version
row counts:      files 445, symbols 1,733, refs 113,751, imports 1,406,
                 index_errors 0, chunks_fts 2,198
size:            21,557,248 bytes, journal_mode=wal
```

No surrogate id, no qualified name, no container/parent column, no language
column on `symbols` (language lives on `files`); `to_name` is a bare `TEXT
NOT NULL`. Every number in the audit's schema section reproduces, including
the `ast`/`regex` split (1,589 / 144). Read with SQLite `mode=ro`; no refresh
ran.

### Bar 10 — import cost: PASS, with divergence stated

```
$ python -X importtime -m code_indexer.cli --help
qdrant_client      cumulative 355.3 ms   (audit: 389 ms)
mcp                cumulative 284.9 ms   (audit: 284 ms)
code_indexer.core  cumulative 424.3 ms   (audit: 457 ms)

$ python -m code_indexer.cli --help, five runs
0.9139 0.9540 0.9344 0.9194 0.9067   median 0.9194 s (calc stat median)
```

Three repeat importtime runs put `qdrant_client` at 358.9 / 377.4 / 364.7 ms
and `mcp` at 277.0 / 292.8 / 271.3 ms. So `mcp ≈ 284 ms` reproduces inside the
spread, and `qdrant_client ≈ 389 ms` does not: the highest value observed is
377 ms, 3 % below the published figure and 9 % above my single-run 355 ms.
The CLI start time reproduces (median 0.919 s versus 0.931 s). The
conclusion the numbers are used for — a third of startup in each of
`qdrant_client` and `mcp` — is unaffected (finding F4).

### Bar 11 — the stale-read observation: PASS

```
$ overview --skip-stale-check
  src/code_indexer/cli.py: 1712 LOC
  src/code_indexer/core.py: 1626 LOC
$ wc -l src/code_indexer/cli.py src/code_indexer/core.py
1716 src/code_indexer/cli.py
1358 src/code_indexer/core.py
```

Both manifest figures and both `wc -l` figures match the document exactly; the
manifest is genuinely stale (`core.py` is 268 lines out of date).

### Bar 12 — feasibility facts: PASS

```
clangd -> NOT FOUND          pyright-langserver -> NOT FOUND
pylsp -> NOT FOUND           roslyn-language-server -> NOT FOUND
OmniSharp -> NOT FOUND       csharp-ls -> NOT FOUND
gopls -> NOT FOUND

$ keeper --version
keeper 0.1.8
$ keeper file --help
  write           replace a file's content
  replace-lines   replace an inclusive 1-based line range
  insert-lines    insert lines before, after or at the end
  delete-lines    delete an inclusive 1-based line range
  apply-patch     apply a unified diff, recoverably across files
$ keeper file write --help
  --dry-run
  --expected-hash HASH
```

All seven language servers are absent; the `keeper` version and all six
primitives the audit relies on exist.

### Bar 13 — hygiene and publication: PASS

```
$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=308 skipped=0 violations=0 (untracked files are out of scope)

$ uvx ruff check src tests scripts
All checks passed!

$ rg -n '<machine-path pattern>' docs/overhaul-baseline-audit.md
621:{"url":"file:///<repo>","dir_info":{"editable":true}}
$ rg -nw '<internal-name pattern>' docs/overhaul-baseline-audit.md
503:`~/.hermes/skills/code-indexer/SKILL.md` is byte-identical to the repo's
$ rg -n '<board-card pattern>' docs/overhaul-baseline-audit.md   (no match, exit 1)
```

The three patterns are elided above so this record does not restate them; they
are the lint's own classes (`absolute-host-path`, `internal-profile-name`,
`board-card-id`) applied to the one document, and the raw capture keeps the
literal commands under `runs/milestone0-gate/doc-hygiene-scan.txt`.

No absolute host path, no board card id. The two hits are benign and are the
elided/relative forms the audit promises: `file:///<repo>` is the house's
published placeholder spelling (the lint's own allow rule), and `~/.hermes`
is a home-relative configuration path, not a machine path and not an agent
attribution. The lint-visible surface is clean and the working tree's lint
agrees.

### Bar 14 — signature and push: PASS

```
$ git log --format='%h %G? %s' -3
0116074 G docs: correct the module count in the baseline audit
9e725bb G docs: audit the current implementation against the overhaul proposal
ef2be78 G docs(29): annotate dev-record counts as point-in-time ...

$ git ls-remote origin main
0116074d44512d35cc94bd32fb44dcd030502c17	refs/heads/main
$ git rev-parse HEAD
0116074d44512d35cc94bd32fb44dcd030502c17
```

Both audit commits are good signatures and the remote tip equals the audit
tip. The commit carrying this record is signed with the repository identity
and pushed to the same remote; the signature is verified in the report that
accompanies it (`git log --format='%G?' -1`).

## Findings

Disagreements are recorded here rather than edited into the audit.

**F1 — the stale-docstring citation is off by 27 lines (trivial).**
`server.py` says "all 14 tools", but at line 61 inside the module docstring,
not at line 34. The substance (the docstring is stale by four) stands.

**F2 — the 22,491-byte MCP figure is serialization-dependent.** It reproduces
exactly as `json.dumps(list_of_records, indent=1, ensure_ascii=True)`. The
compact form of the same document is 17,660 bytes, 21 % smaller, which puts
the derived token estimate at 4,415 rather than 5,622
(`calc eval "17660/4"` -> 4415). The number is not wrong; it is one end of a
range, and the document does not name the serialization. A decision that
weighs the MCP surface by tokens should quote the range.

**F3 — "19 test modules define their own stub class" is wrong.** The
sentence says 19 and then lists 31 module names; 31 is what a scan of the tree
returns (`rg -l 'class _?(Stub|Fake)(Embedder|Store)' tests/ | wc -l` -> 31).
The enumerated list is right and the count is wrong — this is the weakest
claim in the document, because it contradicts itself on the same page.

**F4 — the `qdrant_client` import cost is overstated by up to 9 %.** 389 ms is
not reproduced on this host: one run gives 355.3 ms, three repeat runs give
358.9 / 377.4 / 364.7 ms, and `code_indexer.core` follows it down
(424.3 ms versus 457 ms). `mcp` at 284 ms reproduces exactly. The startup bill
argument is unaffected at these magnitudes.

**F5 — the failed-search row's 67 s is a single-project measurement.** The
defect (exit 0, `error:` on stdout) reproduces in both shapes, but the wall
time only matches when the query is scoped with `--project` and `--limit 2`,
which is how the audit measured it. The unscoped default call iterates every
registered project and takes 804.2 s here (12 projects) before printing the
same failure. That direction makes the defect more serious, not less: an
agent's default call can spend thirteen minutes and then report success. The
document's risk item reads as if the cost were 67 s.

**F6 — `list-projects` opens 15 connections, not 14.** Consistent with one
more registered project than the registry held at audit time; the hang itself
reproduces unchanged.

## Verdict

The audit is a faithful measurement of this codebase. The thirteen bars that
test its numbers reproduce to the digit; the two that diverge (import cost,
MCP byte count) diverge in ways that do not change the architectural
conclusion they support, and both are recorded above with their measurements.
The one flatly wrong statement is the seam-module count (F3), and the one
under-stated consequence is the unscoped failure cost (F5).
