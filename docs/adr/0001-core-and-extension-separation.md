# ADR-0001: core and extension separation

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

Parsing, indexing, lexical and structural query, graph navigation and context retrieval are the core product: they install and run with no service of any kind. Embedding and vector storage become an optional extension (`code-indexer[semantic]`); the MCP server becomes a separate extension (`code-indexer[mcp]`) that the package no longer imports. An index pass can build a complete index with vectors off, because the manifest stores each chunk's text and hash: Qdrant becomes a vector store only, and a later vectors-on pass embeds straight from the manifest without reparsing a file. Three version axes -- extraction, embedding, index schema -- carry a policy matrix saying which bump invalidates what, and every collection is homogeneous in one embedding identity and is never shared across two.

## Context

1. Every CLI start pays for both optional services. `code_indexer/__init__.py:8` imports the MCP server, `mcp` is imported at module level by `src/code_indexer/server.py:83-84`, and `qdrant_client` by `src/code_indexer/store.py:13`. Measured `python -m code_indexer.cli --help` over five runs: median 0.931 s, of which `qdrant_client` is 355-389 ms and `mcp` 284 ms -- about two thirds of the wall time, on surfaces that need neither (audit section 4, gate F4).
2. A pass cannot begin without both services. `src/code_indexer/indexer.py:105-106` asks Qdrant for a collection and the embedder for a dimension before a single file is scanned; on a throwaway root, `add-project` with both unreachable exits 1 and leaves `state=never-indexed` (audit section 4, `index-without-vectors.txt`). No key, flag or variable disables embedding.
3. Chunk text has no home in the manifest. `chunks_fts` holds tokenized text with no chunk index and no line (`src/code_indexer/fts.py:79-83`); the Qdrant payload carries a `snippet` cut at 500 characters (`src/code_indexer/indexer.py:54-55`). Neither is re-embeddable, because the embedded text prepends a path and symbol header to the raw chunk. So requirement 1's "fill only the vector side without reparsing" is unfunded today: a vectors-on pass would re-read and re-chunk every file.
4. The three concepts are one fingerprint. `FINGERPRINT_KEYS` mixes `embed_model`, `embed_dim`, `embed_text_version`, `chunker_version` and `extraction_version` into one comparison (`src/code_indexer/fingerprint.py:36-38`), and `embed_dim` comes from a live Ollama round trip. That probe is why `index-status`, `lookup-project` and `list-projects` cost 34 s and 5-15 connections offline (audit section 4).
5. Two facts this record's own measurement adds. `numpy` is a declared hard dependency (`pyproject.toml:17`) that nothing under `src/` imports. And on the live index, 445 files summing to 2,522,004 bytes hold 2,198 chunks, so a per-chunk text store costs at most the indexed source volume -- about 2.4 MB against a 21,557,248-byte manifest dominated by 113,751 reference rows (artifacts `runs/milestone0/probe-symbol-identity.txt`, `runs/milestone0/probe_identity_and_chunk_storage.py`; audit section 3).

## Decision

### 1. What the core installs with

| Package | Today | After |
|---|---|---|
| `typer`, `pathspec`, `tree-sitter`, `tree-sitter-language-pack`, `watchdog` | core | core, unchanged |
| `ollama` | core | extra `semantic`; imported only in the embedder's constructor |
| `qdrant-client` | core | extra `semantic`; imported only in the store's constructor |
| `mcp` | core, imported by the package `__init__` | extra `mcp`; imported only by the `code-indexer-mcp` entry point |
| `numpy` | core | not core; arrives with the `semantic` extra as a `qdrant-client` dependency |

Rules an implementer follows:

- `code_indexer/__init__.py` imports no submodule: importing the package costs no client library and no service configuration.
- A core command constructs neither client; the eager construction in `Core.__init__` (`src/code_indexer/core.py:98-112`) moves behind lazy properties.
- An absent extension produces one actionable line and no traceback: `ConfigError: the semantic extension is not installed; install code-indexer[semantic]` -- exit code per ADR-0003.
- A core command never probes a service for a verdict the manifest already answers. The embedding dimension is read from the collection when writing, never probed when reading.

### 2. Three version axes and the policy matrix

| Axis | Recorded value | Covers |
|---|---|---|
| `extraction_version` | integer in `meta` | parser, chunker, reference and import extraction -- one number for the one pass that writes rows |
| `embedding_version` | one composed string, e.g. `qwen3-embedding:8b/4096/contextual-header-v1` | model, dimension and embedded-text format, compared as a whole |
| `index_schema_version` | integer in `meta` | the manifest DDL only |

What each bump invalidates, and what the user then runs:

- `extraction_version` -- invalidates every row parsed from source (symbols, refs, imports, chunks) and each file's points, because chunk boundaries and point ids move with them. Leaves the embedding identity, the collection and the dimension intact. The user runs one pass per project; unchanged chunk text re-embeds from the content-addressed cache, not from the model.
- `embedding_version` -- invalidates the collection as a whole and leaves every manifest row intact. A new collection is built under the new identity and swapped in; the old one is dropped after the swap.
- `index_schema_version` -- invalidates the manifest tables the change touches and leaves the collection intact whenever chunk text and chunk identity are untouched. Migrated on open; reparse only where the change requires it.

Two rules follow and are not negotiable. **Vectors are never mixed across models**: a collection holds exactly one embedding identity, recorded in `meta` and stamped into the collection name, and a changed identity means a new collection, never an append. **Rows survive an embedding bump and vectors survive a schema bump**: a model change never forces a reparse, a manifest migration never forces a re-embed. An implementation coupling them has misread this record.

### 3. What a vectors-off pass produces

The manifest gains one table and becomes authoritative for chunk content: `chunks(file, chunk_index, start_line, end_line, symbol, symbol_type, chunk_hash, text)`, keyed on `(file, chunk_index)`, where `chunk_hash` is the sha256 of the chunk text that the chunker already computes.

- A pass writes files, symbols, refs, imports, `chunks`, `chunks_fts` and `index_errors` with no service reachable, and records `meta.vectors=off`.
- Qdrant is a vector store only. The payload keeps what a ranked hit needs to render, but no read path takes text from it.
- A vectors-on pass reads `chunks.text` and `chunks.chunk_hash`, embeds and upserts. It reparses nothing and reads no file; a cached hash costs nothing.
- A chunk removed from a file leaves `chunks` and the collection under the same per-file purge that exists today.

### 4. CLI shape

- One option, not a second command: `--vectors auto|on|off` on `add-project`, `index-more` and `reindex-project`.
- `auto` means the project's recorded mode; a new project records `off`, so `auto` never silently enables embedding on a fresh install.
- An existing vectors-on project stays `on` across upgrades: working behaviour is preserved.
- `watch` follows the recorded mode and never turns vectors on by itself.
- `index-status` reports both sides: `rows=current|absent`, `vectors=on|off`, `vectors_state=current|absent|stale|error`.

### 5. Staleness with only one side present

The fingerprint comparison becomes per side, not one combined string.

- A missing or mismatched `embedding_version` says nothing about the rows: row surfaces answer normally and the answer says so (ADR-0003).
- `semantic-search` on a project with `vectors=off` fails with `ConfigError` naming the remedy (`--vectors on`). It never degrades silently to lexical and never returns an empty successful answer.
- `semantic-search` on an identity mismatch refuses, as the gate already does for a wrong-model index (`src/code_indexer/core.py:413`, called at `:679`).

## Requirements this record resolves

- **Requirement 1** in full. Its three versions are the three axes above; its demand for a policy matrix is that matrix; its demand that vectors-off be an explicit mode writing a complete manifest is section 3; its condition that a later vectors-on pass reparse nothing is why the `chunks` table exists. Its own stated policy ("embedding change -> re-embed all chunks") is honoured as "replace the collection", which says the same thing in a form the store can enforce.
- Requirement 2's locator is ADR-0002's; requirements 3 and 5 (multi-file recovery, one language server) belong to the successor record set. None is dropped.
- **Disagreement with the reviewed position on requirement 1:** a staleness verdict must not require an embedding-dimension probe. The identity string is recorded at write time and compared as a string, which removes the 34 s offline cost of three read commands. A dimension probe on a read path puts a network dependency inside a staleness verdict.
- **Disagreement with the proposal's `auto` default:** proposal section 4.3 forbids automatic embedding during ordinary indexing while its CLI sketch implies the opposite. Section 4 above resolves the conflict in favour of 4.3.

## Alternatives considered, and why rejected

- **Keep both services as core deps and only make imports lazy.** Saves most of the latency but still makes `pip install` pull a vector client and an HTTP client for a tool whose core reads SQLite. Install footprint is a product property.
- **One `semantic` extra bundling MCP.** MCP is an interface choice, not a retrieval capability; bundling forces an MCP user to install a model client and an embedding user to ship tool schema it cannot use.
- **Chunk text only in Qdrant, with a vectors-off flag.** A lexical answer then has no line range and no re-embeddable text, and the vectors-on pass re-reads every file: today's state with a flag on it.
- **`auto` meaning "on when the services are reachable".** Makes indexing depend on network weather: one repository would index differently on two hosts.

## Consequences

- Core CLI start loses about two thirds of 0.931 s, and `overview`, `skeleton`, `outline`, `find-*`, `deps`, `changed-symbols` and lexical search work on a machine that never installed Ollama or Qdrant.
- The manifest grows by roughly the indexed source volume (about 2.4 MB on the sample). FTS keeps its own tokenized copy; the duplication is accepted here, and a later record may convert `chunks_fts` to an external-content table.
- The 31 test modules with their own `StubEmbedder`/`StubStore` seam (audit section 5) keep working, but a core test must import no extension; two tests will assert the absent-extra message instead of a connection error.
- Migration is observable and small: upgrade, then one pass per project (`code-indexer reindex-project <path>`). The pass sees an empty `chunks` table, reprocesses each file once, finds every chunk already in the embedding cache, so no vector changes and no collection rebuilds. `index-status` reports `rows=absent` until then. **No owner-approved reindex is needed** and no vector data is lost; the cost is one full parse per project.
- `pyproject.toml:4`'s description ("Semantic code search ... over Qdrant + Ollama") becomes false and must change when this lands -- deliberately not changed by this record.

## What this record does not decide

- Whether the MCP server is deployed, kept, rewritten or removed. This record says only that the core must not import it.
- Whether semantic search later moves to another distribution or is deprecated.
- Multi-file recovery and the filekeeper apply path (requirement 3), and which single language server is validated first (requirement 5).
- The project's name.
- The edit-plan format, including how a plan records the identity it targets.
