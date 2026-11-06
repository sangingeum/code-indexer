# Round 1 verification gate — record

- Card: the round-1 verification card (identifier withheld: `runs/*.md` is an authored,
  hygiene-governed surface, and a board id is a prohibited token there)
- Repo: `code-indexer`, branch `main`, tip under test `f7d63f1`
  (`origin/main` == `f7d63f1` at gate start; verified with `git ls-remote`)
- Gate role: household QA/test engineer
- Date: 2026-11-07
- Verdict: **PASS-with-findings** — every bar is met; five findings are named below,
  three of them medium, none of them a bar failure.

## Method

Every bar was ruled on fresh output run by the gate at `f7d63f1`, captured under this
folder with its command and exit code beside it. Dev-leg figures were treated as claims
and re-measured. Where a dev figure and a gate figure differ, the gate figure is the bar
and the difference is called out.

Environment: Linux 6.8, Python 3.12.12, uv-managed venv, installed CLI is an editable
install of this checkout. Ollama (`192.168.1.103:11434`) and Qdrant
(`192.168.1.105:6333`) are both reachable from this host; the offline bars point the two
environment variables at a local listener instead.

Reading caveat asked for by the brief: the indexed tree **is** this repository, so the
evidence files this gate writes under `runs/round1-gate/` are themselves part of the tree
that a staleness scan of `code-indexer` walks. Stale counts measured during the gate
therefore include the gate's own artefacts and grow as this folder grows (observed: 143 →
145 → 201 → 224 files across the session). No bar depends on the absolute number; the
bars depend on the *verdict* and on the verdict's shape.

## Bar table

| Bar | Verdict | Command | Exit | Observed |
|---|---|---|---|---|
| B1 | PASS | `uv run pytest tests/ -q -W error` | 0 | `609 passed, 2 deselected in 78.59s`; zero warnings (`-W error`); `B1-pytest.txt` |
| B2 | PASS | `.venv/bin/python -X importtime -m code_indexer.cli --help 2>B2-importtime-raw.txt` | 0 | 407 import lines, **0** matching `mcp|qdrant_client|ollama`; `B2-importtime-raw.txt` |
| B3a | PASS | manifest snapshot → 13 read commands → snapshot (`B3a-before.txt`, `B3a-batch/`, `B3a-after.txt`) | 0 (13/13) | sha256 `6270239f…ad17`, size 35,811,328, mtime `2026-11-07 01:23:29.282`, `meta.last_indexed=1793982209.273576` and row counts (files 609 / symbols 1851 / refs 188843 / imports 2793 / chunks_fts 2546) identical before and after |
| B3b | PASS | local listener on 34567/34568; `OLLAMA_URL`/`QDRANT_URL` pointed at it; same 13 reads; then one positive control | 0 | **0 connections** from the read batch on either port; the control (`semantic-search`) produced 1 connection on 34567 — the counter is proven live; `B3b-offline-sink.log`, `B3b-transcript.txt` |
| B4 | PASS | `overview`/`find-symbol`/`index-status --json` on a fresh, a stale and an unknown project (`B4/`, `B4-transcript.txt`) | 0 | fresh: 0 stderr lines, JSON `stale=false, stale_known=true`; stale: **exactly 1** stderr line in the record's shape, JSON `stale=true, stale_known=true, stale_reasons=5` against `stale_reason_count=147/148` (cap holds); unknown: 1 stderr line `Warning: index staleness check did not run (project path absent: …)`, JSON `stale=false, stale_known=false`; no `Warning:` line ever on stdout |
| B5 | PASS | one reproducing command per code (`B5/`, `B5-transcript.txt`) | 0,1,2,3,4,5 | see the taxonomy table below |
| B6 | PASS | both backends at `127.0.0.1:1`, scoped and `--all-projects` (`B6/`) | 3, 3 | scoped `wall=0.35s`, unscoped `wall=0.89s`; stdout **0 bytes** in both; one `BackendError:` line each (the other two stderr lines are `/usr/bin/time`'s own). Against the recorded old behaviour of 804.2 s, that is 903.6× inside the bar |
| B7 | PASS | throwaway index root + throwaway project (`B7/`, `B7-transcript.txt`) | 0/0/5/2 | `--refresh`: exit 0, stderr empty, manifest hash and `last_indexed` both moved (one pass ran and the answer came from it); `--skip-stale-check`: exit 0, stale notice still printed, manifest unchanged (inert); held flock + `--refresh`: exit 5, `LockedError: indexing in progress`, **0 bytes on stdout**; `get-code-context --fresh`: live lines returned, manifest hash and `last_indexed` unchanged; `code-indexer refresh`: exit 2, no such command |
| B8 | PASS-with-findings | three throwaway venvs (`B8/`, `B8-transcript.txt`) | 0/0/3 | core-only: `overview` works, `semantic-search` → exit 3 + `ConfigError: the semantic extension is not installed; install code-indexer[semantic]` + empty stdout, `doctor` → exit 0 with `warn ollama-reachable: unverified…` and `warn qdrant-reachable: unverified…`; `[mcp]`: `code-indexer-mcp --help` lists exactly **7** tools and names the missing `semantic_search`; `[semantic]`: installs, reports `BackendError` exit 3 (not a CLI import error) — see finding F-01 for what that actually means |
| B9 | PASS | live stdio sessions against `code-indexer-mcp` (`B9/`, `B9-transcript.txt`, `B9-transcript2.txt`, `B9-tests.txt`) | 0 | 8 registered tools == the 8 in `tests/test_mcp_server.py::DOCUMENTED_TOOLS` == the 8 in the record; `readOnlyHint` and `idempotentHint` true on all 8; success content == CLI stdout; failure content == CLI stderr with `isError: true` and `{exit_code: 3, ErrorType: BackendError}` in the structured result; `deps` and `find_definition` answered `Unknown tool: …`; usage record: 2 lines for 2 tool invocations, fields tool/outcome/exit_code/response_bytes/duration_ms/timestamp, **no arguments**; a failed record write (path made a directory) left the call `isError=false`; path documented at `README.md:115`; `tests/test_mcp_server.py` + `tests/test_mcp_stdio_handshake.py` → `34 passed` |
| B10 | PASS | `git log/diff 1468102..f7d63f1`; manifest meta dump (`B10/meta-rows.txt`) | 0 | 7 commits in the round; `src/code_indexer/manifest.py` diff is **0 bytes** (same DDL, same `SCHEMA_VERSION = "6"`); no `CREATE TABLE`/`ALTER TABLE` added anywhere under `src/`; `EXTRACTION_VERSION = 4` at base and at tip; `reindex-project` exists at base (`1` definition) and is not added by the round. Live manifest `meta`: `schema_version=6`, `extraction_version=4`, `chunker_version=1`, `embed_dim=4096`, `embed_model=qwen3-embedding:8b`, `embed_text_version=contextual-header-v1`, i.e. no round-2 key (`index_schema_version`, `embedding_version`, `vectors`) is present |
| B11 | PASS | `tests/test_docs.py` at the tip and at `f7d63f1` in a worktree with two deliberate drifts (`B11-flag-matrix.txt`) | 0 / 1 | 19 passed at the tip; drift 1 (a universal `every project-scoped read/search command also accepts --fresh` claim inserted into `SKILL-CLI.md`) → `test_global_flags_paragraph_names_the_fresh_set` FAILS; drift 2 (usage-record path removed from `README.md`) → `test_mcp_section_documents_the_install_line_and_usage_record` FAILS; both restored, tree clean, 19 passed again. The test is no longer a false green. Scoping claims checked against the real registry: all **27** commands accept `--skip-stale-check`, and the six that do **not** accept `--refresh` are exactly the four write commands plus `watch`/`unwatch` — so "`--refresh` is accepted by every read command" is true as written |
| B12 | PASS | `uvx ruff check src tests scripts`; `uv run python scripts/publishing_hygiene_lint.py`; `git log --format='%h %G?'` | 0; 0; — | ruff: `All checks passed!`; hygiene over the committed tree: `violations=0` at every stage (338 tracked files at the dev tip, 339 with the skeleton record, 495 with this folder's captures); all 7 round commits read `G`, and the gate's own commits read `G` too; `origin/main` carries the tip |

### B5 — the taxonomy, with the command that produced each code

| Code | Command | Stdout | Stderr |
|---|---|---|---|
| 0 | `find-symbol Core --project calc-engine` | the hits | empty |
| 0 (zero hits) | `find-symbol zzz_no_such_symbol_zzz --project calc-engine` | `no symbols matching 'zzz_no_such_symbol_zzz'` | empty |
| 1 | `find-symbol Core --project /nonexistent/nope` | empty | `NotFoundError: project not registered: /nonexistent/nope` |
| 1 | `outline /nonexistent/file.py --project code-indexer` | empty | stale notice + `InternalError: path outside registered project: …` |
| 2 | `deps --project code-indexer` (missing positional) | empty | click usage block |
| 2 | `frobnicate` | empty | click usage block |
| 3 | `semantic-search anything --project code-indexer` with both URLs at `127.0.0.1:1` | empty (0 bytes) | `BackendError: embedding service unavailable: …` |
| 4 | `semantic-search anything --project liveproj` (fingerprint `needs-reindex`) | empty | `RebuildRequiredError: index for … does not match the current configuration (extraction_version: 1 -> 4); run: code-indexer reindex-project …` |
| 5 | `overview --project <throwaway> --refresh` while another process holds `<INDEX_ROOT>/<slug>.lock` | empty (0 bytes) | `LockedError: indexing in progress` |

Every code in the record's table is reachable from a real command. Code 5 was produced on
a throwaway index root so the live locks were never touched; the collection the throwaway
pass created in the shared Qdrant (`idx_3608d08f`) was deleted afterwards and the
collection list was diffed back to its pre-gate state (`B7-collections-*.txt`).

## Rulings owed

### 1. `doctor` still dials — acceptable as designed, with the timeout named as a defect

`doctor`'s job **is** the probe: its help text says "Ollama (reachable / model present /
dimension probe), Qdrant (reachable / version / client compatibility …)", so contacting the
services is the command's stated contract, and ADR-0003 section 1's rule — a read command
"dials no service **to reach a staleness verdict**" — is about the *verdict* path, not about
a command whose whole output is service checks. `doctor` reaches no verdict: it reports
what it found, and it exits 1 when a check fails rather than answering fresh/stale/unknown.
So it is **not** a FAIL of the read class. What the measurement does show is a defect of
its own: see the blackhole timing below. Ruling: acceptable as designed; the unbounded
per-check timeout is a **MEDIUM** finding referred to the owning dev, not a bar failure.

Evidence: `R1-doctor/doctor-blackhole.out`, `R1-doctor/doctor-blackhole.err`. Measured
here with both URLs at `192.0.2.1` (TEST-NET-1, unroutable): **wall=185.84 s, exit 1**, and
the output is honest when it arrives — `fail ollama-reachable: … timed out` and
`fail qdrant-reachable: … timed out`, not a hang and not a false `ok`. The 185.8 s is the
sum of two fixed probes (~93 s each); a caller gets no partial output before the end
because stdout is buffered. That per-check budget is the defect to file, and it is the
only thing about `doctor` this gate would change.

### 2. `fingerprint=unverified` on a matched index — the honest reading, and section 1 does not demand more in round 1

ADR-0001 section 1's last rule is "A core command never probes a service for a verdict the
manifest already answers. The embedding dimension is read from the collection when writing,
never probed when reading." A dimension that is only knowable from a live collection cannot
be compared on a read path at all, so the only honest report is "unverified" — the
alternative (probe it) is the exact behaviour the rule forbids, and the other alternative
(report it matched without comparing) is a guess. Section 1 therefore does not demand more
in round 1; it demands *less*, and round 2's composed `embedding_version` is what makes the
comparison possible without a probe. Ruling: **honest reading, no FAIL**, with one condition
recorded — `unverified` must not be read by a consumer as `mismatch`, and it is not: the
records show `fingerprint=unverified reason=embedding-dimension-not-compared`, while
`state=needs-reindex` is a separate verdict (`liveproj`, `extraction_version: 1 -> 4`).

Evidence: `B4/fresh-index-status.json`, `B4/stale-index-status.json`; the zero-connection
bar (B3b) is what makes the "no probe" half of the rule observable.

### 3. `find_definition` as an eleventh removal — inside the grant; the record's list is short by one

The grant is two sentences: the kept table names eight tools, and "Eight tools with one
schema each is a starting point the implementation leg may trim further; it may not add a
tool that the rule above excludes". Trimming is explicitly permitted, and nothing outside
the kept eight is part of the surface, so dropping `find_definition` is inside the grant.
**The arithmetic is what is worth recording:** the base surface had 18 tools of which seven
are kept, `overview` is added (it is in the kept table and was absent before), and 18 − 11
= 7, +1 = the eight that ship. The record's removed list enumerates ten names and therefore
omits `find_definition`; the CHANGELOG ("Ten MCP tools removed, and `find-definition` with
them") and `code-indexer-mcp --help` (eleven names) both get it right. That is a
**documentation defect in the record**, reported to the owner, not edited by this gate.

Evidence: `git show 1468102:src/code_indexer/server.py` → 18 `@mcp.tool` decorations, no
`overview`; today's help lists 8 registered and 11 removed.

## Findings

### F-01 (MEDIUM) — the `[semantic]` extra installs a numpy this host cannot import

A fresh `uv pip install -e ".[semantic]"` resolves `numpy 2.5.4`, and importing
`qdrant_client` then raises `RuntimeError: NumPy was built with baseline optimizations:
(X86_V2) but your machine doesn't support: (X86_V2)`. The dev tree is unaffected because
`uv.lock` pins `numpy 1.26.4`, and `pyproject.toml`'s own comment says that pin exists for
exactly this reason — but the pin lives in the lockfile, not in the extra's requirement, so
an install that resolves from the index (the documented `uvx --from "code-indexer[mcp]"`
path, or any `pip install code-indexer[semantic]`) lands broken on this host. The CLI
surfaces it honestly (exit 3, `BackendError`, no traceback), which is why B8 passes by its
letter — but the extra does not work here after a fresh install.

Reproduce:

```
uv venv /tmp/g-sem && uv pip install --python /tmp/g-sem/bin/python -e ".[semantic]"
/tmp/g-sem/bin/python -c 'import qdrant_client'      # RuntimeError: numpy/X86_V2
uv pip list --python /tmp/g-sem/bin/python | grep numpy   # 2.5.4  (repo .venv: 1.26.4)
```

Owning dev: the leg that split the extras. Suggested shape of the fix: pin the constraint
where the resolver can see it (`semantic = ["qdrant-client<1.15", "numpy<2; platform_machine=='x86_64'"]`
is one option) or document the host requirement; the choice is the dev's, not this gate's.

### F-02 (MEDIUM) — a row-surface query on a `needs-reindex` index answers "no symbols" with exit 0

ADR-0003 section 5 gives code 4 to "the index requires a rebuild: fingerprint mismatch,
vectors absent, needs-reindex". The gate exists on the search path (`assert_fingerprint_ok`,
`src/code_indexer/core.py:832`) and `semantic-search` honours it (exit 4,
`RebuildRequiredError`). The row surfaces do not: `find-symbol`/`overview` read the rows
written by the obsolete extractor and report a normal, empty-looking answer.

Reproduce (project `liveproj` is `needs-reindex`, `extraction_version: 1 -> 4`):

```
code-indexer index-status --project liveproj            # state needs-reindex
code-indexer find-symbol Core --project liveproj        # exit 0, "no symbols matching 'Core'"
code-indexer semantic-search x --project liveproj       # exit 4, RebuildRequiredError
```

This is the same defect class the round exists to close — a failure that reads as an empty
answer — one layer below the search path. It becomes universal for every project when round
2's schema bump lands, which is what the plan's own risk register anticipates
("`find-*` exiting 4 with `IndexRequiresRebuild`"). Owning dev: the leg that owns the read
contract.

### F-03 (LOW) — an `unknown` verdict drops `last_indexed`/`index_revision` that the manifest holds

When the project path is absent, `Core.staleness` returns `unknown` before opening the
manifest (`src/code_indexer/core.py:324-325`), so the JSON block reports `index_revision:
null, last_indexed: null` even though the manifest records a pass. Consumers that want to
say "this answer is from an index built at T, and I could not check the tree" cannot.
`stale_known=false` keeps the verdict honest, so this is information loss, not a false
claim. (The aggregate block sets both null deliberately — `staleness.py:175` — so this note
is about the single-project case only.)

Reproduce:

```
code-indexer index-status --project liveproj --json | jq '{stale_known,last_indexed,index_revision}'
# {"stale_known": false, "last_indexed": null, "index_revision": null}
python3 <manifest dump> ~/.code-indexer/liveproj/manifest.db   # meta.last_indexed = 1791027797.55
```

### F-04 (LOW, record-only) — the record's removal list is one short

See ruling 3. Eleven tool names left the surface; the record enumerates ten. The code, the
help text and the CHANGELOG agree with each other; only the record's list is short. No code
change is implied and the record is not this gate's to edit.

### F-05 (MEDIUM) — `doctor` needs ~186 s on a blackholed host before it says anything

`doctor` is allowed to dial (ruling 1), but each reachability check carries a fixed ~93 s
budget, so a host whose two services are blackholed waits 185.84 s for a six-line answer.
The output is honest when it arrives (`fail … : timed out`, exit 1) — the defect is the wait
and the buffering, not the verdict. An agent that runs `doctor` on a broken host blocks for
three minutes with no partial output.

Reproduce:

```
OLLAMA_URL=http://192.0.2.1:11434 QDRANT_URL=http://192.0.2.1:6333 \
  /usr/bin/time -f 'wall=%e' code-indexer doctor
# wall=185.84, exit 1
```

## What this round does NOT verify

- Round 2 surfaces: the `chunks` table with text and hash, `--vectors auto|on|off`, the
  three version axes and the per-side fingerprint comparison, the service-free pass, symbol
  identity (`qualified_name`, `decl_hash`, surrogate id), the ambiguity envelope.
- Round 3-5 surfaces: structural query filters, plan format/validate/show/apply, the
  filekeeper apply path and partial recovery, the language-server provider and its contract
  suite, the skill split and the installed-copy drift checks.
- The MCP keep-or-remove decision, which is a dated, evidence-fed owner decision, not a bar.
- Anything about a clean-machine install beyond the three venvs measured here; no wheel was
  built, no sdist was inspected.
- Multi-user or concurrent indexing beyond the single held-flock case.
- Whether the live indexes should be kept: `doctor` reports one live data condition
  (`fail qdrant-missing: registered project(s) without a collection: idx_liveproj`) that is
  older than this round and is data, not behaviour.

## Publishing hygiene of this evidence

The captures quote this repository's own paths, so the tracked copies have host-specific
path text elided: `<checkout>`, `<index-root>`, `<scratch>`, `<dev>/`, `<home>/`. Nothing
else was touched. Counts, measured rather than asserted: **60 files, 162 path tokens**, in
two passes (59 files / 161 tokens before the `doctor` capture finished, 1 file / 1 token
after it). `diff -r` against the pre-elision snapshot kept outside the repository reports
62 files and 373 changed lines; the excess over two lines per token (2 x 162 = 324) is the
two `doctor` captures that were still being written when the snapshot was taken — they are
kept in the same outside-the-repo store, and no other line in any capture differs.

`jq -e .` still parses every `.json` capture, so only string values changed and no
structure did. The record itself is authored and carries no host path, profile name or
board id.

## Evidence index

`B1-pytest.txt`, `B2-importtime-raw.txt`, `B3a-before.txt`/`B3a-batch/`/`B3a-after.txt`,
`B3b-offline-sink.log`/`B3b-transcript.txt`, `B4/`+`B4-transcript.txt`,
`B5/`+`B5-transcript.txt`, `B6/`, `B7/`+`B7-transcript.txt`+`B7-collections-*.txt`,
`B8/`+`B8-transcript.txt`, `B9/`+`B9-transcript.txt`+`B9-transcript2.txt`+`B9-tests.txt`,
`B10/meta-rows.txt`, `B11-flag-matrix.txt`, `B12-ruff.txt`, `B12-hygiene.txt`,
`R1-doctor/`, `cli-help.txt`.
