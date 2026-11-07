# Verification gate — withheld files are skips, not staleness

Base `143d85f`, candidate `219fd61` (5 commits, all signed, pushed).

**Reading builds.** A verdict is a property of the build that reads, so every
read below was run through one harness that puts a chosen source tree first on
`sys.path`: candidate reads use the checkout's own `src`, base reads use the
base tip's `src` in a detached worktree. The manifest read is the live index of
this checkout (178 files, revision 739, built by the installed tool), so the
defect's own reproduction is the subject.

**Verdict: PASS.** No bar row needs a fix.

## Bar rows

1. **Freshness after a pass — PASS.** `bar1-base-json.txt`: `stale: true`,
   `stale_reasons: [{"kind": "added", "file":
   "tests/test_sensitive_exclusion.py"}]`. `bar1-cand-json.txt` on the same
   index: `stale: false`, `stale_reasons: []`, `skipped_count: 1`,
   `skipped_files: [{file: tests/test_sensitive_exclusion.py, reason: sensitive
   content}]`. Text mode: `bar1-base-text.txt` prints the one stale warning
   line, `bar1-cand-text.txt` prints none and appends the skip phrase. The
   readings were repeated after an independent watcher pass rewrote the
   manifest at 09:04:21Z and came out the same (`bar1-repeat-base-json.txt`,
   `bar1-repeat-cand-json.txt`) — a pass cannot repair the base defect.

2. **No leak, no rows — PASS.** Seven fixture patterns (the key id, the PEM
   header, its body line, the GitHub and Slack token prefixes) counted zero
   times in three candidate answers (`bar2-leakscan.txt`). The withheld path
   has no `files`, `symbols`, `chunks` or `refs` row (`bar2-db-check.txt`: four
   counts, all 0), so no snippet can be served. The new state names the path
   and the reason, nothing else.

3. **Changed sensitive file is not a reason — PASS.** In a throwaway project a
   content-withheld file grew 117 → 147 bytes
   (`sha256 6eed2604… → 4ae0001f…`), a pass ran (`index_revision` 1 → 2,
   `bar3-pass-cand.txt`), and the verdict is still `fresh` with the file as a
   skip (`bar3-after-pass-cand.txt`). At base, the same index reports both
   withheld files as `added` (`bar3-after-pass-base.txt`).

4. **Negative controls — PASS.** In the same throwaway project: an added
   non-sensitive file still reports `added` (`control-added-cand.txt`), a
   modified one `modified` (`control-modified-cand.txt`), a removed one
   `deleted` (`control-deleted-cand.txt`); the skip list stays at its two files
   in each answer, and restoring the tree returns `fresh`
   (`control-restored-cand.txt`). A skip swallows none of the three.

5. **The skip is stated, in both surfaces — PASS.** Text: `… 1 file skipped
   (sensitive content)` (`bar1-cand-text.txt`). JSON keys on the fresh verdict,
   in order: `project state rows vectors vectors_state manifest_schema
   fingerprint reason languages_without_ast parsed parse_error
   parse_unavailable stale stale_known stale_reasons stale_reason_count
   skipped_count skipped_files skipped_summary index_revision last_indexed
   schema`; a `skipped_files` element carries exactly `file reason`
   (`bar5-keys.txt`).

6. **The read stays a read — byte level — PASS.** `bar6-hashes-cand.txt`: the
   manifest hash (`f9bf4e3f…`) and its `-wal` hash (`e3b0c442…`) are identical
   before the batch and after each of `index-status`, `find-symbol`,
   `semantic-search`. Repeated on a throwaway manifest that no daemon covers
   (`bar6-throwaway-hashes.txt`), identical again. Exit codes: the whole batch
   exits 0 on both builds (`bar6-base-*.txt`, `bar6-cand-*.txt`, each with its
   stderr separated); the base stale read keeps exactly one stderr line; a
   manifest recorded at an older schema refuses with `RebuildRequiredError`,
   exit 4, on base and candidate alike (`bar6-schema-BASE.txt`,
   `bar6-schema-CAND.txt`).
   *Caveat, measured not assumed:* a background `watch --all` daemon writes
   this project's manifest on its own sweep — the hash changed across a 150 s
   window in which no read command ran (`bar6-watcher-window.txt`). The
   byte-level claim above therefore holds over the batch window; the
   throwaway-manifest repeat is the unconfounded proof.

7. **The read stays bounded — PASS.** Budget 8,388,608 bytes; the rig holds
   11,546,797 added bytes over 49 added files. The sensitive file the budget
   reaches is skipped; the eight sensitive files past the budget
   (`zz_sensitive_01..08`) report `added`, not `skip`
   (`bar7-full-verdict-cand.txt`: 48 added, 1 skipped), and a direct probe
   confirms those files are content-sensitive, i.e. they would be skips if
   examined (`bar7-probe.txt`). Examined 30 candidates (1 skip + 29 honest
   adds), unexamined 19, all added. Wall time, 3 runs each: median 136 ms base,
   151 ms candidate (+11.03 %), `bar7-timings.txt`.

8. **Cost — PASS.** 3-run median `index-status` on this checkout: base 147 ms,
   candidate 148 ms — +0.68 % (`bar8-timings.txt`). Not materially slower.

9. **Suite and hygiene — PASS.** `uv run pytest tests/ -q -W error`: 994
   passed, 2 deselected, exit 0 (`bar9-pytest.txt`). Base was 988; the new
   test file carries 6 tests, so 988 + 6 = 994 and the delta is measured, not
   assumed. No red at all — the installed-skill digest test passes, so the
   expected-RED condition did not arise. `uvx ruff check src tests scripts`:
   `All checks passed!` (`bar9-ruff.txt`). Publishing hygiene: 0 violations.
   All 5 commits in `143d85f..219fd61` signed (`G`); `origin/main` held that
   exact tip when the gate began, and this report is pushed on top of it.

10. **Scope — PASS.** `git diff --name-only 143d85f..HEAD` is confined to
    `src/code_indexer/{cli,sensitive,staleness}.py`, `tests/`, `SKILL-CLI.md`,
    `skill/references/indexing.md`, `docs/json-schema.md`, `CHANGELOG.md` and
    `runs/`. `manifest.py` and `graph_extract.py` are untouched, so manifest
    schema stays 7 and `EXTRACTION_VERSION` stays 5 — no schema change. No
    `docs/adr/` edit.

## Findings (non-blocking, none of them a bar row)

- A refreshed `index-status` in text mode prints its pass summary as
  `last_pass=*** 0, 'chunks_embedded': 0, …` — a truncated dict representation
  with a literal `***` where the opening brace should be. The base build prints
  the identical line (`bar3-pass-base.txt`), so this predates the round.
- The aggregate line reads `2 files skipped (2 sensitive content)` when both
  skips are content skips (machine-wide `list-projects`); the singular form is
  right, the plural noun is clumsy.
- The staleness read path can now read up to 8 MiB of file prefixes per
  verdict. Bounded, honest, and invisible on this checkout; the cost shows only
  on a tree of all-new files (+11 % on the pathological rig above).

## Evidence

`runs/r4a-gate/` — 63 captures (commands and their full output) plus this
report. Host paths in the captures were replaced by `<checkout>`, `<scratch>`,
`<index-root>` and `<home>`; the unmodified captures are kept outside the
repository, and the redaction was proven by diff (513 changed lines, every one
of them a header line or a declared substitution, zero unexpected).
