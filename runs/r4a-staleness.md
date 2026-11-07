# A file the sensitive layer withholds is not a staleness reason

Base tip: `143d85f` (signed). Implementation tip: `e2bb054` (signed); docs
and this record follow it. Nothing here changes the manifest schema, the
extraction version, or an ADR.

## The defect, reproduced first

A file whose content matches the sensitive detector's patterns is dropped by
the index pass on purpose and gets no `files` row. It stays in the SCAN set,
so the tree-vs-manifest diff saw a scanned path with no row and counted it as
`added` on every read. On this checkout that file is the detector's own test
module -- its fixtures are an AWS key id and a PEM header -- so every read
carried:

```
Warning: index is stale (1 file changed since 2026-11-07T08:44:02Z: 1 added);
answers describe the index, not the tree.
Refresh: code-indexer reindex-project <checkout>
```

with `exit 0`, before the read, after the read, and after a full reindex. The
manifest agreed with the detector and disagreed with the banner: 177 `files`
rows and zero of them for that path (also zero `symbols`, `refs`, `imports`
and `chunks` rows). The capture is `runs/r4a-staleness/base-repro.txt`.

That one permanent reason is what made the banner wallpaper; the cost is the
case where the banner is real.

## The design, and the alternative rejected

**Chosen: re-apply the detector inside the staleness diff.** `detect()` takes
the scanned paths with no manifest row -- the exact set it previously turned
into `added` reasons -- and asks the same `sensitive.file_is_sensitive`
decision the pass asked. The filename layer is answered from the path alone
(no read); the content layer reads the same 256 KiB prefix the pass reads, so
the two cannot disagree about a file. A withheld path becomes a `skipped`
entry (path + the layer that fired), and only the rest are `added` reasons.

The decision is therefore *derived*, not stored: an existing manifest answers
correctly with no migration, and a file whose content stops matching the
detector becomes a real `added` reason on the next read -- the honest outcome,
and one a persisted skip list could not produce without re-reading the file
anyway.

**Rejected: persist skipped paths with their reason in the manifest.** It
needs a new manifest key (or table) and a write on the pass path, and it is
stale by construction: a withheld file that later becomes non-sensitive keeps
its persisted skip until the next pass, so a genuinely new indexable file
could read as `fresh` -- a false negative in exactly the direction the defect
taught readers to distrust. It would also need its own schema change and
`EXTRACTION_VERSION` handling, both out of scope for this leg.

**Rejected: keep the file out of the scan set.** The scanner does not own the
sensitive policy (it is a content decision, and only an indexed-scope /
ignore-file rule belongs there), and teaching it the detector would make the
read path skip files the pass might index under `--allow-sensitive`.

### What the reader sees

`index-status` text:

```
project=<checkout> state=idle rows=current ... parse=parsed:122,parse_error:0,parse_unavailable:56 1 file skipped (sensitive content)
```

`index-status --json` (excerpt):

```json
"stale": false, "stale_known": true, "stale_reasons": [],
"stale_reason_count": 0, "skipped_count": 1,
"skipped_files": [{"file": "tests/test_sensitive_exclusion.py",
                    "reason": "sensitive content"}],
"skipped_summary": "1 file skipped (sensitive content)"
```

The fields ride on the shared staleness block, so every read answer that
carries the block carries them (`list-projects` aggregates them too). The
banner is quiet because the verdict is genuinely fresh; the skip is visible
because it is counted, named by path and layer, and never named by material.
No row, snippet or symbol was written for the file: after the change the
manifest still holds zero `files`, `symbols`, `refs`, `imports` and `chunks`
rows for the path. Across the whole manifest (2,831 chunk texts) zero chunks
match the sensitive detector, and zero chunk texts contain any of the skipped
module's fixture strings (PEM header, AWS key id, GitHub token, Slack token).
Neither the text line nor the JSON object names material -- only the path and
the layer. The capture is `runs/r4a-staleness/after-status.txt`.

### Bound on the read path

Only scanned paths with no manifest row are opened, up to the same 256 KiB
prefix, inside a total 8 MiB budget per check. A path past the budget keeps
its `added` reason: reporting a file stale when the detector was not
re-applied is conservative and honest; reporting it fresh would not be.

## Cost

Same tree, same manifest, base code vs tip code (a worktree of `143d85f` run
against this checkout's project):

| measurement | base | tip |
|---|---|---|
| `index-status --json`, 3-run median (wall) | 157 ms | 158 ms |
| `Core.staleness()` in process, 9-run median | 42.64 ms | 42.96 ms |
| re-applying the detector, 9-run median | -- | 0.027 ms |

The full-check delta (+0.32 ms) is inside the run-to-run spread of the walk
itself; the added work is one 9,961-byte read plus the regexes, 27 us. The
budget above is what keeps the worst case (a whole new subtree) from growing
with the tree: it reads at most 8 MiB and then stops classifying.

## Tests

`tests/test_sensitive_skip_staleness.py` (6 new, real index passes with
vectors off against a tree that carries a sensitive module):

1. `test_a_skipped_file_is_fresh_after_an_index_pass` -- no row exists, the
   verdict is fresh, one skip with reason `sensitive content`, and the block
   prints `1 file skipped (sensitive content)`.
2. `test_a_changed_sensitive_file_is_not_stale` -- the withheld file is
   rewritten with another secret; still fresh, still skipped.
3. `test_a_genuinely_added_file_still_reports_stale` -- the negative control:
   `c.py` is still an `added` reason, and the skip rides beside it.
4. `test_a_removed_file_still_reports_deleted` -- a removed indexed file is
   still `deleted`.
5. `test_a_filename_skip_needs_no_content_verdict` -- a `.pem` path is
   classified `sensitive filename` from the path alone.
6. `test_the_json_block_carries_the_count_and_no_secret_material` -- count,
   path and reason present; `AKIA`, `PRIVATE KEY` and the key id absent.

Existing staleness, sensitive-exclusion, staleness-reporting and
scoped-indexing suites were re-run unchanged and pass.

## Verification at the tip

- `uv run pytest tests/ -q -W error`: **993 passed, 1 failed, 2 deselected**.
  The single red is `test_installed_copy_digest_matches_the_tracked_copy`, the
  expected red: the installed skill copy is refreshed by the release step, not
  by this leg. No other test fails and no new warning is raised.
  (`tests/test_docs.py` alone: 37 passed, that one red; the entry document is
  120 lines, inside its budget.)
- `uvx ruff check src tests scripts`: **All checks passed!**
- `uv run python scripts/publishing_hygiene_lint.py`: **PASS** --
  `publishing hygiene: scanned=834 skipped=0 violations=0`, run at the tip
  after this record and both captures were committed.

## Open, deliberately outside this leg

- The installed skill copy (and its digest test) is refreshed by the release
  step.
- The running watcher on this host is the installed build, so until that
  build is updated the same checkout will still be re-passed by older code;
  the verdict a read reports is always the reading code's own, so the fix does
  not depend on the watcher.
- The five `watcher.py:343` fork warnings under CI's command belong to a
  separate card; this leg does not claim a warn-clean run.
