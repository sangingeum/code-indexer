# Round 2 verification gate — record

- Card: identifier withheld (the authored `runs/*.md` surfaces are hygiene-governed and a board id is a prohibited token there; the dispatch brief carries it)
- Repo: `code-indexer`, branch `main`, tip under test `cf6af65`, round base `a22769d`
- Gate role: household QA/test engineer
- Date: 2026-11-07
- Verdict: **PASS with two findings**

## Method

Every bar below is ruled on fresh output this gate produced at `cf6af65`, captured in
this folder beside its command and exit code. The two dev legs' figures were treated as
claims and re-measured. Migration, fingerprint and chunk work all ran on **copies** in a
throwaway index root; no pass was run against a live project.

Reading caveat: the house index over this checkout is at extraction 4 while the tip writes
extraction 5, so its row surfaces refuse with exit 4 by design. Exploration therefore used
`rg`/`fd` and direct file reads where the indexer refuses, as the dispatch brief allows.

## Bar table

| Bar | Verdict | Command (all at `cf6af65`) | Exit | Observed / evidence |
|---|---|---|---|---|
| suite green and warn-clean | PASS | `uv run pytest tests/ -q -W error` | 0 | 661 passed, 2 deselected — `b1-pytest.log` |
| ruff clean | PASS | `uvx ruff check src tests scripts` | 0 | no findings — `b1-ruff.log` |
| hygiene | PASS | `uv run python scripts/publishing_hygiene_lint.py` | 0 | `scanned=552 violations=0` — `b1-hygiene.log` |
| one schema revision | PASS | per-commit probe of `SCHEMA_VERSION` + DDL digest | 0 | `"6"` at base, `"7"` from `4e5201a` onward, no second bump; structural `CREATE`/`ALTER` lines frozen after `4e5201a` — `b2-schema-per-commit.txt`, `b2-ddl-frozen.txt`, `b2-manifest-diff-4e5201a-0102410.txt` |
| a pass that needs no service | PASS | `INDEX_ROOT=<tmp> OLLAMA_URL=http://127.0.0.1:9 QDRANT_URL=http://127.0.0.1:9 code-indexer add-project <tmp>` | 0 | complete manifest: files 5, symbols 14, refs 38, imports 4, chunks 15, chunks_fts 15, index_errors 0; `index-status --json` → `rows=current, vectors=off, vectors_state=absent` — `b3-service-free-add-project.txt`, `b3-manifest-dump.json`, `b3-index-status.json` |
| vectors-on pass reads no source file | PASS | named test `pytest tests/test_vectors_on_reads_no_sources.py`; direct `chmod 000` + `reindex-project --vectors on` | 0 | named test 1 passed; direct pass exit 0, `chunks_embedded=13`, `files_indexed=0` with the source tree unreadable — `b4-named-test.log`, `b4-vectors-on-unreadable-source.txt` |
| chunks maintained | PASS | `reindex-project --vectors off` after deleting a chunked symbol | 0 | residue query empty; chunks 14 = fts 14 = sum(chunk_count) 14; per-file indices contiguous — `b5-chunk-maintenance-reindex.txt` |
| axes and policy matrix | PASS | `pytest tests/test_vector_policy_matrix.py tests/test_identity.py tests/test_docs.py -q -W error` | 0 | 31 passed; collection name carries the identity digest (`idx_<slug>__<8 hex>`), observed live in the Qdrant list — `b6-policy-matrix-tests.log`, `b6-collections-by-identity.txt` |
| per-side fingerprint | PASS | copies of a complete manifest, embedding-only bump then extraction bump | 0 | case A: rows answer (exit 0) and `vectors_state=stale`; case B: exit 4, stdout empty, `RebuildRequiredError` naming `extraction_version: 999 -> 5` — `b7-per-side-fingerprint.txt` |
| vectors-off search refusal | PASS with finding | `code-indexer semantic-search <proj> --json` with vectors off | 4 / 3 | with a reachable embedder: exit 4, stdout empty, `ConfigError` naming `--vectors on`; with an unreachable one: exit 3 `BackendError` first — `b8-semantic-search-vectors-off.txt` |
| refusal matrix still holds | PASS | live `find-symbol`, `find-references`, `overview` + the four exempt commands | 4 / 0 | three surfaces exit 4 with empty stdout and one `RebuildRequiredError` each; `index-status`, `list-projects`, `lookup-project` answer; `doctor` exits 1 only because the gate's own dead `OLLAMA_URL` fails its checks — `b9-refusal-live-spotcheck.txt` |
| migration honest and idempotent | PASS | copied v6 manifest opened, then a real pass on the copy | 0 | opens to 7; `chunks 0, symbols 0` after open (old-key rows not carried); second open is byte-identical (`IDEMPOTENT: True`); pass re-keys to 891 files, 2123 symbols, 3209 chunks, 3209 fts — `b10-migration-probe.txt`, `b10-rekey-pass.txt` |
| identity survives a line shift | PASS | scratch repo, shift definitions down five lines, reindex | 0 | `start_line` 3→8, 7→12, 11→16; `qualified_name` and `decl_hash` unchanged; three overloads remain three identities — `b11-identity-line-shift.txt` |
| docs truth | PASS with finding | `pytest tests/test_docs.py`; four-doc sweep against the CLI | 0 | 31-test run includes `test_docs.py`; documented flags exist (`--vectors` on `index-more`/`reindex-project`, `--mode` on `semantic-search`); `index-status` field list matches; ADR-0003's "a read command never writes the manifest" is contradicted — `b12-docs-sweep.txt`, finding F2 |
| nothing live mutated | PASS with finding | live root inventory and registry hash, before and after | — | registry hash identical; 12 projects all `needs-reindex`; collection list 13 after removing this gate's throwaway collection; manifest bytes changed for 12 of 13 projects when a read command opened them — `live-index/before.txt`, `b13-live-state-after.txt`, `b13-manifest-shas-after.txt` |

## Rulings owed

### 1. Explicit `--mode lexical` on a `vectors=off` project

**Inside the grant; the record forbids silent degradation, not the explicit mode.**
ADR-0001 section 5 says `semantic-search` on a vectors-off project "fails with
`ConfigError` naming the remedy (`--vectors on`)" and "never degrades silently to
lexical". The prohibition is on the silent fallback and on an empty successful answer.
An explicit `--mode lexical` is a user-chosen retrieval mode that needs no vectors —
the same record's consequences promise "`overview`, `skeleton`, `outline`, `find-*`,
`deps`, `changed-symbols` and lexical search work on a machine that never installed
Ollama or Qdrant". Measured: default invocation exits 4 with `ConfigError`; explicit
`--mode lexical` exits 0 with FTS results. Consequence: no violation and no change
needed in behaviour; the only obligation is that no doc sentence claims *every*
semantic-search invocation refuses without naming the mode. The sweep found none that
does — the changed lines say "never a silent lexical answer".

### 2. Row surfaces refusing only on an extraction/chunker difference

**That is section 5's first bullet, not a looser reading.**
ADR-0001 section 5: "A missing or mismatched `embedding_version` says nothing about the
rows: row surfaces answer normally and the answer says so." The implementation matches
the record: an embedding-only mismatch answered rows and reported `vectors_state=stale`;
an extraction mismatch refused with exit 4. One observation, not a bar failure: "the
answer says so" is satisfied by `index-status`, while `find-symbol --json` carries the
row-staleness block but no `vectors_state` field — the row surface answers without
repeating the vector verdict in its own payload. The record does not require the field
on every row surface, so the ruling stands; it is worth a sentence in the docs if the
intent was per-surface reporting.

### 3. The extraction bump refusing this checkout

**Expected post-gate state, and no surface claims otherwise.**
The live index was written at extraction 4 and the tip writes extraction 5, so all 12
registered projects report `needs-reindex`; `find-symbol`, `find-references` and
`overview` exit 4 naming the remedy, while the four exempt commands answer and
`index-status` says `rows=absent`. The operational reindex is a post-gate step and is
not part of this code delivery; nothing in the CLI or docs claims the live index is
current.

## Findings

### F1 — the vectors-off refusal runs after embedder initialisation (moderate)

`semantic-search` on a `vectors=off` project refuses with the contract's `ConfigError`
and exit 4 only when the embedding service is reachable. With no reachable service the
command exits 3 with `BackendError` before the vectors-off guard fires, so the promised
remedy line is never printed. The refusal is still a refusal (never an empty success),
but the class and exit code are the wrong ones for the documented contract, and the
most likely real-world case — a machine with no Ollama — gets the wrong error.

Reproduce: point `OLLAMA_URL` at a closed port (or run with no Ollama), then
`code-indexer semantic-search <vectors-off-project> --json`; observe exit 3 and
`BackendError` instead of exit 4 and `ConfigError` naming `--vectors on`. Evidence:
`b8-semantic-search-vectors-off.txt`.

### F2 — read commands open the manifest read-write and migrate it in place (moderate)

`Manifest.__init__` opens every manifest with `sqlite3.connect` and WAL, so a read
command writes the file's header/checkpoint on open; opening a pre-revision-7 manifest
runs the migration and **drops its old-key symbol rows** on a read. ADR-0003 states "a
read command never writes the manifest", which this tip contradicts. Observed on the
live root: 12 of 13 manifests changed bytes between the gate's before capture (04:18:01)
and the end of the gate; the migrated copy's hash matches the hash a deliberate copy
produced in the migration probe, so the change is the migration, not a pass. Logical
content checks: registry hash identical, collection list identical, file sizes
identical, live `code-indexer` manifest now files 609 / symbols 0 / chunks 0.

Reproduce: take a v6 manifest, open it through any read command (`index-status`,
`find-symbol`), and compare the file's sha256 before and after — the first open changes
it and empties the old-key rows; the second is a no-op. Evidence:
`b10-migration-probe.txt`, `b13-manifest-shas-after.txt`, `live-index/before.txt`.

Severity note: the migration-on-open is documented in ADR-0001's consequences
("Migration is observable and small ... `index-status` reports `rows=absent` until
then"), so the behaviour is intended; the contradiction is between that intent and
ADR-0003's absolute sentence. A reader who believes ADR-0003 will not expect a read to
prune an index.

## Live index at the end of the gate

- 12 registered projects, all `needs-reindex`; the reason on this checkout is
  `extraction_version: 4 -> 5`.
- Live Qdrant collections: 13, the same set as at the start. The gate's own throwaway
  collection from the unreadable-source probe was deleted again, so the list is
  restored.
- `registry.db` sha256 unchanged (`3c9d80bc…`); manifest file sizes unchanged. Manifest
  byte hashes changed on open as F2 describes.
- The operational reindex of these projects has **not** been run and is a post-gate
  step, not part of this code delivery.

## What this gate does not verify

- The later rounds' scope: structural query surfaces, plans and apply, and the provider
  and agent surfaces.
- The operational reindex of the live projects, and the correctness of their content
  after that reindex.
- An embedding bump against a real Qdrant collection: the policy-matrix tests use stub
  stores, and the gate observed only the naming rule in the live collection list.
- The claim that a read command's migration leaves no orphaned vector payloads in the
  store; the gate confirmed row behaviour and collection identity, not store hygiene.
