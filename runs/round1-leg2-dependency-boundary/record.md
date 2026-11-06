# Round 1, leg 2 — the dependency boundary and the narrowed MCP surface

- Card: `<card-id>` on the code-indexer board
- Commits: `0cd1bea` (packaging split) · `3469138` (probe-free reads) ·
  `9f436a0` (MCP surface, relay, usage record, docs) · `4c1ef4d` (tests) ·
  this record's commit
- Every commit is signed (`%G?` = `G`).
- Figures below are point-in-time measurements from this leg's environment;
  each says when it was taken and with which command.

## What changed, by section

### The dependency boundary (`0cd1bea`)

- `pyproject.toml`: core keeps `typer`, `pathspec`, `tree-sitter`,
  `tree-sitter-language-pack`, `watchdog`. `ollama` and `qdrant-client` moved
  into `[project.optional-dependencies] semantic`; `mcp[cli]<2` into `mcp`.
  `numpy` is listed nowhere — it arrives transitively with `qdrant-client`
  inside the semantic extra, and the comment says so.
- Both entry points keep their names: `code-indexer` → `cli:main`,
  `code-indexer-mcp` → `server:main`.
- `src/code_indexer/__init__.py` imports no submodule (docstring and an empty
  `__all__` only).
- `Core.__init__` constructs neither client: `embedder`, `store` and `indexer`
  are lazy properties with setters, so the existing test seams still replace
  them after construction. `ollama` is imported inside `Embedder.__init__`;
  `qdrant_client` inside `Store.__init__` (via one `_load_qdrant` helper that
  publishes the names the module body uses). `indexer.py` imports the client
  types for annotations only and hands the store plain point dicts; the store
  builds the client's point type.
- `extensions.py` answers "is the extra installed" with `find_spec`, never an
  import. An absent semantic extra raises one line and exit 3:
  `ConfigError: the semantic extension is not installed; install
  code-indexer[semantic]`.
- `uv lock` regenerated: one Python branch (3.12), `numpy<2` resolved through
  `qdrant-client`, `provides-extras = ["semantic", "mcp"]`.
- Description corrected per ADR-0008 section 2: no rename, and the old
  "MCP server over Qdrant + Ollama" claim about core is gone.

### Reads stop probing for the fingerprint verdict (`3469138`)

- `fingerprint.configured_values()` states the keys configuration alone can
  know (model, embedded-text format, chunker version, extraction version);
  `probe_free_mismatches()` compares exactly those against the recorded
  values.
- `Core.fingerprint_status` no longer probes the embedder. A known-key
  mismatch is `needs-reindex` (gate unchanged, exit 4); a match on every
  comparable key is `unverified` with reason
  `embedding-dimension-not-compared`, never folded into `ok`.
- `assert_fingerprint_ok` refuses on the same probe-free comparison only.
- `list-projects` stopped asking the store for a collection listing; that
  verdict belongs to `doctor`'s `qdrant-missing` check.
- `doctor`'s per-project fingerprint check reads the manifests through a
  meta view (no migration write) and uses the same comparison.

### The MCP surface (`9f436a0`)

- Eight tools: `list_projects`, `overview` (new), `skeleton`, `find_symbol`,
  `find_references`, `find_callers`, `get_code_context`, and
  `semantic_search` registered only when the semantic extra is importable.
- Removed: `add_project`, `index_status`, `lookup_project`,
  `reindex_project`, `remove_project`, `changed_symbols`, `deps`,
  `find_callees`, `find_symbols`, `outline` — plus `find_definition`, whose
  query shape `find_symbol` already answers (see carried open).
- Relay: `_run_cli` returns a `CliOutcome` with the two streams kept apart and
  the exit code. Success returns stdout; failure returns stderr with
  `isError: true` and `exit_code` plus `ErrorType` in the structured result;
  a silent failure stays a silent failure (no synthesized message).
- Annotations: every tool `readOnlyHint=True`, `idempotentHint=True`,
  `destructiveHint=False`, `openWorldHint=False`, and the tests assert all
  four as explicit booleans on every tool.
- Usage record: `mcp_usage.record_invocation` appends one line per invocation
  to `$INDEX_ROOT/mcp-usage.jsonl` — tool, outcome, exit code, response
  bytes, duration, timestamp, never the arguments. A failed write is logged
  at debug and does not fail the call. Written only by the MCP process.
- Help: `code-indexer-mcp --help` prints the registered tools and the
  removals from the server's own registry, and points at
  `uvx --from "code-indexer[mcp]" code-indexer-mcp`.

### Tests (`4c1ef4d`)

- Removed tests of the removed tools with them; kept
  `test_server_is_thread_free_and_core_free` and the stdio handshake test.
- Added: the registered set equals the documented set, both directions, and
  the seven-tool variant in a subprocess that hides the semantic packages;
  the relay's stream split and exit code (`isError`, `ErrorType`, silent
  failure, silent success); one usage line per invocation without arguments;
  a failed record write not failing the call; the absent-extension line for
  `semantic-search` and for an index pass; probe-free `list-projects`,
  `lookup-project`, `index-status` and a core-only `doctor`.

### Documentation (`9f436a0`, `4c1ef4d`)

- `README.md`: MCP section, parity table and client configuration block
  rewritten in the MCP commit; install lines name the extras; the usage
  record path is stated; the development and constraint paragraphs were
  corrected in the tests commit.
- `SKILL-CLI.md`: the "positional forms remain the MCP call shape" note was
  replaced — the mutating commands have no tool, the surface is read-only.
- `CHANGELOG.md`: a new `## [Unreleased] — dependency boundary and the
  narrowed MCP surface` section carries the removals with the migration line
  ("use the CLI subcommand of the same name") and the extras split. No
  version bump; the named-release framing is satisfied at release time.

## Measurements

### CLI start time (point-in-time: this leg, before the push)

Command, five runs per tree, output discarded:

```
/usr/bin/time -f "%e" env PYTHONPATH=<tree>/src INDEX_ROOT=/tmp/leg2-bench/root \
  <repo>/.venv/bin/python -m code_indexer.cli --help
```

| tree | runs (s) | median |
|---|---|---|
| baseline `2f2dcd9` (worktree at `/tmp/leg2-baseline`) | 0.96 0.91 0.91 0.92 0.94 | **0.92 s** |
| this tip `4c1ef4d` | 0.14 0.14 0.14 0.14 0.14 | **0.14 s** |

Excerpt of the baseline import trace that the tip no longer produces:
`ollama._client`, `ollama._types`, `qdrant_client` and `mcp` were imported
before the CLI could print its help.

### Offline cost of the four commands (point-in-time: this leg)

Baseline figures are the audit's, recorded before this leg and quoted here as
such (`docs/overhaul-baseline-audit.md`): `list-projects` did not return
within the 75 s harness cap (15 connections), `lookup-project` 34.0 s
(5 connections), `index-status` 34.4 s (5 connections), `doctor` 39.9 s
(7 connections).

This tip, same throwaway root (`/tmp/leg2-bench/root`, one registered project
with a recorded fingerprint), both services pointed at the unroutable
`192.0.2.1`:

| command | exit | elapsed |
|---|---|---|
| `list-projects` | 0 | 0.18 s |
| `lookup-project /tmp/leg2-bench/proj` | 0 | 0.16 s |
| `index-status /tmp/leg2-bench/proj` | 0 | 0.17 s |
| `doctor` | — | did not complete inside 180 s (see below) |

`index-status` now says what it could not compare:

```
project=/tmp/leg2-bench/proj state=idle fingerprint=unverified \
reason=embedding-dimension-not-compared languages_without_ast=[...]
```

`doctor` with the extras installed still dials its two service sections, and
against a blackholed address the client's configured timeout and retry chain
dominate (measured in this session: `timeout 180` exited 124). With the ports
refusing instead of blackholing, the same run answers in **0.69 s** and exits
1 as designed:

```
fail ollama-reachable: http://127.0.0.1:1: Failed to connect to Ollama. ...
fail qdrant-reachable: http://127.0.0.1:1: [Errno 111] Connection refused
warn fingerprint: unverified — every comparable key matches for 1 project(s),
     but the recorded embedding dimension is not compared here (that needs a
     probe, and a read path never makes one)
```

So doctor's fingerprint verdict is probe-free; its reachability section is a
verdict the manifest cannot answer and is carried open below.

## The registered tool list

```
find_callers
find_references
find_symbol
get_code_context
list_projects
overview
semantic_search   (only when the semantic extra is importable)
skeleton
```

Without the semantic extra the registry holds seven names; the test that hides
the two client packages in a subprocess asserts the exact set and the help
line that says why `semantic_search` is absent.

## Extras install matrix — what this leg ran, and what it hands to the gate

Run here, in a throwaway venv and a throwaway index root
(`uv venv /tmp/leg2-core-venv --python 3.12`; `uv pip install --python
/tmp/leg2-core-venv/bin/python -e .`; `INDEX_ROOT=/tmp/leg2-core-root`), with
no extras installed at all:

```
$ code-indexer overview --project /tmp/leg2-core-proj
exit 0
project: leg2-core-proj — 0 files, 0 LOC
(stderr: Warning: index staleness check did not run (no index manifest ...))

$ code-indexer semantic-search "anything" --project /tmp/leg2-core-proj
exit 3, stdout empty, stderr:
ConfigError: the semantic extension is not installed; install code-indexer[semantic]

$ code-indexer doctor
exit 0
warn ollama-reachable: unverified: the semantic extension is not installed; ...
warn qdrant-reachable: unverified: the semantic extension is not installed; ...
```

That is the decisive core-only case: the core reads work, a semantic command
fails with the documented one line at the documented exit code, and doctor
degrades to unverified instead of dialling or crashing.

**The full three-extra matrix is the gate's, not this leg's.** The
`code-indexer[mcp]` case (eight tools, seven without semantic) and the
`code-indexer[semantic]` case (an index pass still reaches the services) were
not run as installs here; they are the gate card's acceptance and this
record does not claim them.

## Acceptance at the tip (`4c1ef4d`, before the record commit)

```
uv run pytest tests/ -q -W error
  exit 0 — 609 passed, 2 deselected in 76.32s
uvx ruff check src tests scripts
  exit 0 — All checks passed
uv run python scripts/publishing_hygiene_lint.py
  exit 0 — scanned=332 violations=0
uv run pytest tests/test_mcp_server.py -q
  exit 0 — 32 passed
python -X importtime -m code_indexer.cli --help 2>&1 | rg 'mcp|qdrant_client|ollama'
  no output (rg exit 1); the help printed and the trace names no client
```

A note on the last command: on this host bare `python` resolves to an
unrelated 3.14 environment that has no `code_indexer`, so the check was run
with the installed editable tool's interpreter
(`<installed-tool-python> -X importtime -m
code_indexer.cli --help`) — exit 0, help printed, no client module in the
trace. The bare-`python` form would fail on `ModuleNotFoundError`, not on an
import it was meant to catch.

The captured outputs of these runs are beside this record as
`pytest-tip.txt`, `ruff-tip.txt`, `hygiene-tip.txt`,
`importtime-installed-tip.txt` and `mcp-tip.txt`.

## Carried open, with the record that owns it

1. **The embedding dimension cannot be compared on a read path.** The
   fingerprint records `embed_dim`, but the configured side of that comparison
   is only knowable by asking the service. The composed `embedding_version`
   string that would make it a string comparison is ADR-0001 section 2, which
   this leg must not build, so a matched index reports `unverified` rather
   than `ok`. **Owned by round 2** (ADR-0001 section 2, the three version
   axes). The vocabulary already exists and is asserted by tests; the
   behaviour is not a silent guess.
2. **The collection-versus-registry check moved to `doctor`.** `list-projects`
   no longer lists collections, because that is a service verdict the
   manifest does not answer. `doctor`'s `qdrant-missing` check owns it, which
   means a dead registration is visible from `doctor` and not from
   `list-projects`. **Owned by ADR-0001 section 1's last rule** (recorded here
   as the consequence).
3. **doctor's reachability section still dials.** Its fingerprint verdict no
   longer probes, but `ollama-reachable` / `qdrant-reachable` are verdicts the
   manifest cannot answer, and with the extras installed they make one attempt
   each on the configured URLs (0.69 s against refusing ports; the client's
   retry chain dominates against a blackholed host, measured above). A
   core-only install makes no contact at all. **Owned by the round 1 gate /
   the follow-up card that owns doctor's probe budget** — this leg measured it
   and did not change it.
4. **`find_definition` is an eleventh removal.** The decision record's kept
   table lists eight tools and `find_definition` is not among them, while its
   removal list names ten; the eight-tool rule is what excludes it, because
   `find_symbol` answers the same query shape. This is a consequence of the
   rule, not an invention, and the help text and CHANGELOG name it as removed
   with the same migration line. **Owned by ADR-0007 section 1** if the
   removal list is ever restated.
