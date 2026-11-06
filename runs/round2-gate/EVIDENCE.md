Round 2 verification gate — capture index

Each file in this folder is fresh output produced by the gate at cf6af65, named for the
bar it evidences. Commands and exit codes are quoted in the bar table of `record.md`.

Absolute host paths in these captures were normalised to `<home>`, `<checkout>`,
`<index-root>` and `<scratch>` so the tracked tree satisfies the publishing hygiene
lint; the substance of every capture is unchanged.

  b1-pytest.log                     the full suite, -W error
  b1-named-tests.log                the round's named test files
  b1-ruff.log                       ruff over src tests scripts
  b1-hygiene.log                    publishing hygiene over the tracked tree
  b2-schema-per-commit.txt          SCHEMA_VERSION and the DDL block, commit by commit
  b2-ddl-frozen.txt                 CREATE/ALTER lines changed after the revision bump
  b2-manifest-diff-*.txt            the manifest diff the schema-revision ruling reads
  b3-service-free-add-project.txt   add-project with both service URLs closed
  b3-manifest-dump.json             tables and counts from that throwaway manifest
  b3-index-status.json              rows/vectors/vectors_state for the same project
  b4-named-test.log                 the named no-source-read test
  b4-setup-rows-only.txt            rows-only index used for the unreadable-source probe
  b4-vectors-on-unreadable-sources.txt  chmod 000, then a vectors-on pass
  b4-manifest-dump.json             the manifest the probe produced
  b5-chunk-maintenance-reindex.txt  chunk purge and fts agreement after a reindex
  b5-chunk-maintenance.txt          the first attempt (add-project short-circuits)
  b6-policy-matrix-tests.log        policy matrix, identity and docs tests
  b6-collections-by-identity.txt    live collection names carrying identity digests
  b7-per-side-fingerprint.txt       embedding-only and extraction mismatch cases
  b8-semantic-search-vectors-off.txt  default and --mode lexical on a vectors-off project
  b9-refusal-live-spotcheck.txt     three refusing surfaces and the exempt commands
  b10-migration-probe.txt           copied manifest opens to 7, second open is a no-op
  b10-rekey-pass.txt                a real pass on the copy re-keys the rows
  b11-identity-line-shift.txt       identity across a five-line shift
  b12-docs-sweep.txt                the four-document sweep
  b13-live-state-after.txt          the live root after the gate
  b13-manifest-shas-after.txt       per-manifest hashes after the gate
  live-index/before.txt             the live root at the start of the gate
  live-index/collections-before.json  Qdrant collections at the start
  live-index/collections-after.txt  Qdrant collections at the end
  live-index/collections-after-cleanup.txt  after removing the gate's own collection
  live-index/list-projects.json     the 12 registered projects
  live-index/status-all-projects-before.json  per-project status at the start
