# Round 1 two-axis review

- Reviewed tip: `a1ad8ac` (round base `1468102`); `origin/main` == `a1ad8ac` at review start
- Reviewer: the household reviewer/architect
- Deliverable: this record. No code, no dev record and no gate record was edited by me.

## Verdict

**approve-with-folds.**

The round does what its records say it does. The roster is closed and complete; a read no longer
repairs the index; the exit taxonomy exists and is reachable on the search path; the MCP relay
keeps the two streams apart and preserves the exit code; the core installs nothing it does not use.
I verified the load-bearing claims by running them, not by reading the records (evidence at the end).

What holds it off a plain approval is nine corrections, none of them architectural: two module
docstrings and the CLI tagline still describe the behaviour the round removed; `SKILL-CLI.md` states
the wrong exit code for the round's own headline refusal; the documented install line resolves a
numpy the host cannot import; a fan-out read walks every project tree twice; the MCP wrapper cannot
ask for one of its own documented search modes; and an `unknown` staleness verdict drops two fields
the manifest holds. Two gate-record corrections are named below; I did not make them.

Blocking if left uncorrected: **FOLD-9** (the documented install path is broken on this host) and
**FOLD-4** (the skill states `exit 1` for the fingerprint refusal that ships as `exit 4`).

## Axis 1 — specification fidelity

Section by section, against the shipped tree.

### ADR-0003 section 1 — two command classes

**Holds.** `src/code_indexer/command_classes.py` classifies 27 commands: 20 reads, 6 write-only,
and `sweep-orphans` as conditional on `--apply` — the roster ADR-0003 section 1 closes. The six plan
commands are classified in the record and do not exist yet, as the plan intends. `--skip-stale-check`
is accepted everywhere and inert (its only consumer is `Core.__init__`, where the value is stored and
never read); `--refresh` is accepted by every read command and by no write command. `--fresh` on
`get-code-context` still reads the live file and writes nothing.

Reachability, not just the roster — what I actually traced:
- `Core.__init__` builds no client; `embedder`, `store` and `indexer` are lazy properties, and the
  client imports live inside `_make_embedder` / `_make_store` (`core.py:144-172`).
- `maybe_refresh` is gone from `src/`. The only caller of `run_index` is `refresh()`, itself called
  only from a `--refresh` branch (`cli.py:318-321, 339-341`) and from the watcher.
- A read that touches rows opens the manifest through `Manifest(...)` directly, not through
  `manifest_for()`.
- **Probed for write intent at the storage layer**, because the roster alone cannot prove it:
  `manifest.db` and `registry.db` mtimes are byte-identical before and after `overview`,
  `find-symbol` and `lookup-project`. `Manifest.__init__` and `Registry.__init__` do re-execute their
  DDL on every open (`manifest.py:123-175`, `registry.py:66-79`), so the letter of "opens no write
  transaction" is met observably rather than structurally; nothing is committed, and I did not turn
  that into a finding.
- Two residues worth naming and not fixing here: `manifest_for()` still calls `os.makedirs`
  (`core.py:200-203`) and several read commands call it, so a read can create an empty directory under
  the index root; and `doctor` dials by design (see section 5).

### ADR-0003 section 2 — stat-only staleness

**Holds.** `Core.staleness` feeds the scanner the manifest's `(size, mtime_ns, inode, content_hash)`
as `previous`, so the existing stat fast-path reuses the recorded hash and only a file whose stat
changed is re-hashed (`scanner.py:196-205`). Three verdicts; `unknown` is its own state and the JSON
block reports `stale_known: false` with it. The walk carries file and wall budgets and degrades to
`unknown` on `ScanBudgetExceeded`.

Letter-of-the-record note, not a fold: the scanner still opens every kept file for a binary sniff
(`scanner.py:_is_binary` 8 KB, `_looks_utf8ish` 4 KB) before the stat comparison, so the walk is not
literally "stat calls only". On the live 445-file project the cost is noise; the record's sentence is
what I would tighten, not the code.

### ADR-0003 section 3 — how staleness is reported

**Holds**, with one defect and one wording gap.
- The JSON block carries exactly the six fields, `schema` stays 1, and it is additive on every read
  answer I checked (`overview`, `find-symbol`, `index-status`, `list-projects`, `lookup-project`,
  `semantic-search`, `deps --format json`, `changed-symbols`).
- The stale text line matches the record word for word, including the plural and the AST-free
  `_counts` ordering. A fresh answer prints nothing; `unknown` prints the "did not run" variant.
- **Defect (FOLD-8):** an `unknown` verdict drops `last_indexed` and `index_revision` that the
  manifest holds. `index-status --json --project liveproj` returns both as `null` while
  `list-projects` prints `last_indexed=2026-10-03 20:43:17` for the same project — because
  `Core.staleness` returns at the path-absent check before it opens the manifest.
- **Wording gap:** the record says "Text mode prints exactly one stderr line". A fan-out read prints
  one per project (`_read_staleness_many`, `cli.py:347-349`); `list-projects` on this host prints
  eleven. The behaviour is right and the record's sentence should say "one line per project".

### ADR-0003 section 4 — refresh as the only read-to-write trigger

**Holds.** `--refresh` takes the flock through `run_index`, and a miss raises `LockedError` (exit 5)
with nothing on stdout — I re-ran the gate's held-flock shape and read its capture; the flock-miss
rule is also asserted in `tests/test_read_contract.py::test_refresh_on_a_held_flock_is_exit_5_with_no_answer`.
`--skip-stale-check` is inert (the stale notice still prints). `get-code-context --fresh` writes
nothing. No separate `refresh` command exists; `code-indexer refresh` exits 2.

One deviation I name rather than fold as behaviour: `--refresh` on a fan-out runs one pass **per
project**, not one pass — `doctor --refresh` refreshes every registered project (`cli.py:178-182`),
and `semantic-search --all-projects --refresh` likewise (`cli.py:339-341`). Neither is bounded by the
query wall, since the wall starts after the refreshes. Opt-in and honest in effect, but the shared
flag help says "one incremental index pass" (
FOLD-7).

### ADR-0003 section 5 — exit taxonomy

**Holds in code.** `errors.py` maps the taxonomy; the last-line seam in `main()` turns any uncaught
exception into one `ErrorType:` line and the mapped code, so nothing escapes as a traceback. Verified
by running: unknown command → 2, dead backends → 3 with empty stdout and one `BackendError:` line,
needs-reindex search → 4 with `RebuildRequiredError:` and empty stdout, held flock → 5, unregistered
project → 1, zero hits → 0. The two look-alike cases are separated as the record requires: absent
extension/service is 3, and a working provider that does not implement the operation stays a
usage-class 2 through `ArgumentError`.

Two gaps:
- **`SKILL-CLI.md:477` states the wrong refusal**: "`search` fails with `ConfigError` naming the
  difference (one line on stderr, exit 1)". It ships as `RebuildRequiredError` and exit 4 (FOLD-4).
- **"vectors absent" has no path to 4.** Nothing maps a missing collection to
  `RebuildRequiredError`; a project whose store cannot answer is caught per project and, when nothing
  answers, surfaces as `BackendError` exit 3. Round 1 has no vectors-off state, so the branch is
  unreachable rather than wrong — but it must be an acceptance criterion for round 2, which owns
  `vectors=off` (ADR-0001 section 3/5).

### ADR-0003 section 6 — bounded cost

**Holds by construction.** One embedding attempt (`_query_vector`, `embed_query_once`), one vector
reused across projects, the 30 s wall checked before each project, remaining projects reported with
`reason=budget`, `partial: true`, exit 3 when none answered. `--mode lexical` skips the embedding
entirely.

**One place the cost is needlessly doubled (FOLD-5):** `semantic-search` computes the staleness
verdict through `_read_staleness` / `_read_staleness_many` and then calls `core.staleness()` again for
every project to build the warning lines (`cli.py:831-832` and `cli.py:844`). On a fan-out that is two
full tree walks per project. The helper already has the verdict; it should return the warning line or
the verdict instead of throwing it away.

### ADR-0003 section 7 — `--json` on the five commands

**Holds.** `list-projects`, `lookup-project`, `index-status`, `find-definition` and `find-references`
all accept `--json`; `add-project`, `index-more`, `remove-project` and `reindex-project` do not
take it, which is what the plan schedules (the four pass commands' mode is still owed by
ADR-0003 section 7 — see the deferral list). `schema` stays 1 everywhere I looked.

### ADR-0001 section 1 — what the core installs with

**Holds.** `__init__.py` imports no submodule; `mcp`, `ollama` and `qdrant-client` are extras imported
only at the point of use; availability is answered by `importlib.util.find_spec`, never an import;
an absent extra is one `ConfigError:` line and exit 3; no read probes a service for a verdict the
manifest answers — `fingerprint_status` uses `probe_free_mismatches` and reports the dimension half
`unverified`. The MCP extra is imported by `server.py`, which only the `code-indexer-mcp` entry point
imports.

**But the extra does not install correctly off the lockfile (FOLD-9).** `pyproject.toml`'s comment
already knows the fact — "numpy 1.26.4 is the only numpy whose x86 baseline imports on this VM" — yet
that constraint exists only in `uv.lock`. A fresh resolve takes numpy 2.x and `qdrant_client` then
raises the baseline-optimization `RuntimeError`. `uvx --from "code-indexer[mcp]"` (the documented
client line) and `pip install code-indexer[semantic]` are both affected. The CLI's honest exit 3 is
why the gate's bar passes by its letter; it is not a reason to ship a broken install path.

### ADR-0001 section 5 — per-side staleness, as far as round 1 can hold it

**Holds for the part round 1 owns.** A missing or unverified embedding identity does not block row
surfaces; `semantic-search` refuses a mismatched identity before embedding. What does not hold yet is
the `extraction_version` half: a needs-reindex index still answers row queries with old rows and exit
0 (gate F-02, ruled below — round 2's register owns the remedy).

### ADR-0007 section 1 — the eight kept tools and the eleven removed

**Holds.** Eight registered tools; `semantic_search` is registered only when the semantic extra is
importable (`server.py:277`), so a core-plus-MCP install lists seven and says why. Eleven names left
the surface: the ten in the record's list plus `find_definition`, which section 1's "may trim further"
grant covers — the leg record names it twice, once in the removal bullet and once as a carried open.

### ADR-0007 section 2 — the relay

**Holds.** `_run_cli` keeps the streams apart; success returns stdout, failure returns stderr with
`isError: true`, and `exit_code` plus `ErrorType` ride in the structured result; nothing is
synthesized over a nonzero exit. All eight tools carry `readOnlyHint`, `idempotentHint`,
`destructiveHint` and `openWorldHint` as explicit booleans — a claim that is now true because both
preconditions (ADR-0003 landed, the relay reports failure as failure) hold. argv is a list, never a
shell string, so a tool argument cannot reach a shell.

**One lie the rewrite left in place (FOLD-6):** `semantic_search`'s `mode` argument cannot select
`dense`. `server.py:304` adds `--mode` only `if mode != "dense"`, but the CLI's own default is
hybrid, so a client asking for `dense` silently gets hybrid. The tool's docstring advertises all
three modes. One line: pass `--mode` unconditionally.

### ADR-0007 section 3 — the usage record

**Holds.** One JSON line per invocation under the index root, tool/outcome/exit_code/response_bytes/
duration/timestamp and no arguments; written by the MCP process only; a failed write logs at debug and
returns False without failing the call; the path is documented (`README.md:115`).

### ADR-0007 section 5 — what a caller experiences

**Holds.** README's MCP section, parity table and install line were rewritten in the same commit as
the narrowing; `code-indexer-mcp --help` prints the registered set and the removals from the registry
rather than a hand-written count; the CHANGELOG carries the removal with the migration line; a removed
tool answers the protocol's unknown-tool error; `tests/test_mcp_server.py::test_registered_tool_set_equals_the_documented_list`
is the test that closes the "all 14 tools" defect class.

One count to fix in the CHANGELOG's own favour: "Ten MCP tools removed, and `find-definition` with
them" followed by eleven names under "Removed:" is self-consistent but reads as a miscount. Not a
finding — a sentence I would rephrase to "Eleven MCP tools removed".

### ADR-0008 section 2 — the description

**Holds.** `pyproject.toml`'s description is corrected in the round that made it false; the README's
opening paragraph is untouched (the diff starts at `## Installation`). The CLI's own tagline was not
corrected with it (FOLD-3).

## Axis 2 — repo standards

### Readability reachability

Covered above: roster, constructors, lazy clients, `--refresh` plumbing, the watcher's flock (the
watcher calls `run_index`, the same flocked path), and every helper a read command calls. The one
path the classification still misses is the row surface on a needs-reindex index, which is round 2's
register to close (F-02).

### Tests assert behaviour

- **The documentation test is no longer a false green.** I injected a bogus command line into the
  command block of `SKILL-CLI.md`; `tests/test_docs.py` failed on two checks
  (`test_every_documented_command_exists`, `test_documented_flags_exist_in_cli`), and I restored the
  file byte-for-byte. The gate's flag-matrix capture shows the same for two content drifts.
- **Full suite at the tip:** `609 passed, 2 deselected` under `pytest -q -W error`. The suite was
  green at the tip before I touched anything; it went red only because my own skeleton record carried
  a board-card id and an internal profile name, which the publishing-hygiene test catches — evidence
  that the hygiene gate bites, and my own file is corrected.
- **The new module `test_probe_free_reads.py` guards a real path**, not a stub: it replaces
  `Core._make_store` / `_make_embedder` with a raiser, so any read that constructs a client fails.
  The remaining hole is a read that dials without constructing a client; `doctor` is the only such
  surface and it is by design.
- Deleted coverage follows the narrowing: the ten removed tools' tests went with the tools, the
  isolation and handshake tests stayed, and the registered-set test replaced the count.

### House conventions

- **Terminology rule — violation on the gate's own record.** The gate record uses `B1`…`B12` both as
  prose shorthand for its bars and as the names of its capture files and directories
  (`runs/round1-gate/B1-pytest.txt`, `…/B4/`, `…/B5/`, and so on). A bare letter-plus-number standing
  in for a concept is what the rule bans in docs and file names. The two dev records are clean. This
  project ships no terminology lint, so this is reviewer judgment rather than a mechanical FAIL — but
  it is a violation of a normative owner directive, and the correction is a rename of the capture
  paths plus concept wording in the record ("the pytest bar", "the taxonomy bar"). Owner: the gate
  record. I did not edit it.
- Commit identity and signing: every round commit reads `G` in `git log --format='%h %G?'`.
- Publishing hygiene over authored surfaces: the committed tree was clean at the tip; the lint caught
  my own skeleton within one run, which is the behaviour I want from it.
- Dead surface left behind: three instances — the `cli.py` and `core.py` module docstrings describe
  the removed `STALE_TTL` probe, `core.py:42`'s `STALE_TTL_DEFAULT` is referenced nowhere, and the
  CLI tagline still sells a semantic core (FOLD-1, FOLD-2, FOLD-3).

### Docs truth

Every scoping word I checked against the registry and the code: the `--refresh` roster (true as
written), the `--fresh` set in the global-flags paragraph (matches the real option set), the MCP parity
table (matches the registered set), the env-var list (matches the code). The two false statements are
`SKILL-CLI.md:477`'s exit code and the flag help's "one incremental index pass" for fan-out commands.
The CHANGELOG states the visible breaking change (reads stop repairing; a failed search moves off 0)
and the usage-record path is documented.

## Ruling on the gate's five findings

**F-01 — upheld. FOLD (fix, packaging).** The comment in `pyproject.toml` knows the constraint and
leaves it to the lock. Add `numpy<2` to the `semantic` extra (or `[tool.uv]
constraint-dependencies = ["numpy<2"]`), refresh the lock, and update the comment that says numpy is
not listed. This does not weaken ADR-0001 section 1: the core still declares no numpy. **Blocking if
left** — it is the install surface the round created.

**F-02 — upheld on the facts; deferred to round 2.** Only `core.py:832` calls `assert_fingerprint_ok`;
row surfaces answer from a needs-reindex index with exit 0. The reason this is not a round-1 fold is
written in the plan's own reindex register: refusing to answer from the old rows is the round-2
change ("the tool stops answering from the old rows, refuses with exit 4 until each project has been
passed once"), and ADR-0001 section 5 explicitly protects row surfaces from an *embedding* mismatch.
**Blocking if round 2 ships without it**; it must appear as an acceptance criterion in the round-2
implementation record, naming the four row surfaces (`find-symbol`, `outline`, `skeleton`,
`get-code-context`) plus the graph surfaces that read the same rows.

**F-03 — upheld. FOLD (fix, small).** Read `last_indexed` / `index_revision` from the manifest before
the path-absent and no-manifest early returns in `Core.staleness`, and pass them to
`staleness.unknown(...)`. Three lines or so; the manifest is already opened later on every other path.

**F-04 — not upheld as stated.** The leg record does name `find_definition`: in the removal bullet
("plus `find_definition`, whose query shape `find_symbol` already answers") and again as carried open
item 4 ("an eleventh removal"). What remains is a record correction to ADR-0007 section 1, whose own
removal list names ten and does not list `find_definition`; the code, help text, CHANGELOG and leg
record all agree on eleven. Owner: the ADR, not the fix leg.

**F-05 — upheld as measured; deferred, and filed as an issue.** `doctor` answering no index query may
dial for reachability, so this is not a read-class failure — I agree with the gate there. 185.84 s of
silence is 6.19 times the 30 s wall the round introduces for query cost, and the leg record already
assigns doctor's probe budget to a follow-up card. File it as an issue ("doctor: bound the
reachability probes and stream the checks") rather than folding it into a fix leg; not blocking.

**One gate correction of my own:** the exit-taxonomy bar's table row labelled `code4-rebuild` shows
exit **0** for `find-symbol` on the needs-reindex project — it is a demonstration of F-02, not of code
4. Exit 4 is real and I reproduced it (`semantic-search --project` on the same project returns
`RebuildRequiredError: …`, exit 4, empty stdout), so the bar still passes; the row's label is wrong
and the evidence for 4 is elsewhere. Owner: the gate record.

## Folds for the fix leg

| id | file:line | exact change | kind |
|---|---|---|---|
| FOLD-1 | `src/code_indexer/cli.py:1-8` | Replace "The `--skip-stale-check` flag covers startup-cost-sensitive calls; staleness is normally enforced by the core's `STALE_TTL` probe guarded by the per-project flock (concurrent invocations serialize; exactly one indexer pass runs)." with "Reads never refresh: an answer describes the index as of `meta.last_indexed` and reports `fresh`, `stale` or `unknown`. `--skip-stale-check` is accepted and inert; `--refresh` is the only flag that makes a read write, and it takes the per-project flock." | docs |
| FOLD-2 | `src/code_indexer/core.py:1-7` and `core.py:42` | Drop "no watcher on the default path — staleness is enforced via `STALE_TTL` probes on search/status, guarded by the per-project flock" and say the read contract instead (detect, never repair; `unknown` when the check cannot run). Delete `STALE_TTL_DEFAULT = 60`, which now has no reference in `src/`. | docs + dead code |
| FOLD-3 | `src/code_indexer/cli.py:47` | `help="Semantic code index via Ollama + Qdrant (one-shot CLI; no daemon)."` → a description that is true of a core install, e.g. "Code intelligence for agents: index a repository into SQLite and answer structural, lexical and optional semantic queries (one-shot CLI; no daemon)." | docs |
| FOLD-4 | `SKILL-CLI.md:477` | "`ConfigError` naming the difference (one line on stderr, exit 1)" → "`RebuildRequiredError` naming the difference (one line on stderr, exit 4)". | docs (blocking if left) |
| FOLD-5 | `src/code_indexer/cli.py:831-832`, `:844` | Have `_read_staleness` / `_read_staleness_many` return the verdict(s) (or the warning line) alongside the block and print that, instead of calling `core.staleness()` a second time per project. | fix (cost) |
| FOLD-6 | `src/code_indexer/server.py:304` | `if mode != "dense":` → `if mode:`, so `mode="dense"` actually reaches the CLI. Pre-existing, but on a surface this round rewrote and whose docstring advertises the parameter. | fix (one line) |
| FOLD-7 | `src/code_indexer/cli.py:145-150` (`RefreshOpt`) | Say what a fan-out does: "Run an incremental index pass first (one per project for `--all-projects` / `doctor`), then answer from the result." | docs |
| FOLD-8 | `src/code_indexer/core.py:324-330` | Open the manifest for `last_indexed` / `index_revision` before the path-absent and no-manifest early returns, and pass both to `staleness.unknown(...)`, so an `unknown` verdict still reports the fields the manifest holds. | fix (gate F-03) |
| FOLD-9 | `pyproject.toml:26-32` | Add `"numpy<2"` to the `semantic` extra (or a `[tool.uv]` constraint), refresh `uv.lock`, and correct the comment that says numpy is not listed. | fix (gate F-01, blocking if left) |

Record corrections, owned by their authors, not by me and not by the fix leg: ADR-0007 section 1
adds `find_definition` to its removal list; the gate record relabels its exit-4 row and rewrites the
twelve bar labels as concepts (capture paths included).

## Deferred, with the record that owns each

1. **Row surfaces on a needs-reindex index (F-02)** — round 2, owner-approved reindex register;
   becomes an acceptance criterion there.
2. **"vectors absent" → exit 4, and `vectors=off` refusing `semantic-search` with the remedy** —
   round 2, ADR-0001 sections 3 and 5.
3. **`--json` on the four pass commands** — ADR-0003 section 7 decides it; the plan defers it out of
   round 1. The round-2 record should carry it so the decision is not orphaned.

## Filed as an issue

- `doctor`'s reachability probes: bounded wall and streamed output (F-05).

## Not reviewed

- Round 2–5 surfaces: the `chunks` table and vectors-off mode, the three version axes and per-side
  fingerprint comparison, symbol identity and structural filters, the plan lifecycle, the provider
  surface. I read ADR-0001 sections 1 and 5 only, as the round scopes them.
- The watcher's debounce and sweep policy, the eval harness, ranking/reranking behaviour, and the
  Qdrant collection hygiene beyond the round's own captures.
- Performance past the round's bars: I did not re-measure the 804 s baseline or the import time; the
  gate owns those numbers and I ruled on them rather than re-running them.
- No CodeQL scan was run for this leg. The round's Python diff touches no parser, auth or network
  code beyond the local subprocess relay, which I read directly (list argv, no shell).

## Evidence produced by this review

- Suite at the tip: `uv run pytest tests/ -q -W error` → 609 passed, 2 deselected (before my record
  was committed; after it, the hygiene test flagged my own file, which I then corrected).
- Documentation-test truth: a bogus command line injected into `SKILL-CLI.md`'s command block made
  `tests/test_docs.py` fail on two checks; the file was restored byte-for-byte (`cmp` clean).
- Exit taxonomy, run live: unknown command 2; dead backends 3 (empty stdout, one `BackendError:`
  line); needs-reindex search 4 (`RebuildRequiredError:`, empty stdout); unregistered project 1;
  zero hits 0.
- Write-intent probe: `manifest.db` / `registry.db` mtimes unchanged across `overview`, `find-symbol`
  and `lookup-project`.
- Unknown-verdict probe: `index-status --json --project liveproj` returns `stale_known: false` with
  `last_indexed` / `index_revision` null while `list-projects` prints the recorded timestamp.
