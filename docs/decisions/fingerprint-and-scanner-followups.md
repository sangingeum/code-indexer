# Fingerprint verdict surfaces and the two scanner follow-ups

Seven questions were measured and left unruled by earlier rounds: five about
what `index-status` says beside its own verdict line, and two about how the
ignore composer treats a nested ignore file and the directory it prunes. They
are decided together here because each is a choice about the contract rather
than a defect with one obvious repair, and each was measured at the tip this
record lands on rather than quoted from the round that raised it.

Every measurement below was taken on this tree with an offline index (a
scratch index root built by a vectors-off pass, then edited only in its
manifest), or with a probe tree compared against `git check-ignore`. No
service was contacted, and no product code, test or other document changed in
this pass: each ruling carries an implementation brief for a later leg.

Three of the seven were already closed before this leg. They are recorded here
with the pin that holds them, so the batch is answered as a batch and not
half-answered by history.

## The stale marker no pass writes

### Measured

The manifest marker has four accepted values and three writers:

| value | written by | where |
|---|---|---|
| `current` | a vectors-on pass that lost no chunks | `src/code_indexer/indexer.py:641`, `:743` |
| `error` | a vectors-on pass that lost chunks | `src/code_indexer/indexer.py:641`, `:743` |
| `absent` | a vectors-off pass on a project that has no marker yet | `src/code_indexer/indexer.py:636-637` |
| `stale` | no writer | declared at `src/code_indexer/vectors.py:33`; assigned only at read time, `src/code_indexer/core.py:343-347` |

Nothing in this repository's history ever wrote `stale`: across every commit
that touched the module, the only occurrences of the constant are its
declaration and the read-time assignment that derives it when the recorded
embedding identity is not the one this build would write. A manifest that
records `stale` is therefore readable but never produced.

A probe on a real offline index confirms the read path, and the sentence it
prints beside the marker:

| manifest state | `index-status` line | the `fingerprint meaning:` sentence |
|---|---|---|
| `vectors_state=error` plus one recorded chunk error | `… vectors=on vectors_state=error … last_errors=a.py#0: embed failed: timeout …` | the identity-matched sentence; no mention of the error |
| `vectors_state=error`, no chunk-error rows | `… vectors=on vectors_state=error …` | the identity-matched sentence |
| `vectors_state=stale`, identity matching | `… vectors=on vectors_state=stale …` | `separately, the manifest records the vector collection itself as stale, and a vector rebuild is what clears that` |

Every row above is honest in print. The false claim is in the documents that
describe the marker: `docs/json-schema.md:404` says `stale` "is the marker
an earlier build left when a vector rebuild was interrupted", and `vectors.py:12`
says the marker is "the state the LAST write recorded". Neither is supported by
the history or the writers above. `skill/references/indexing.md:246` already
states the measured version ("written by no pass in this build").

### Ruling

**Keep `stale` read-only and say so** -- in the measured words, not the
provenance story.

### Why

Deleting the value would delete the verdict the read itself derives: when the
recorded identity differs from this build's, `core.py:347` sets the state to
`stale` and the verdict line prints it, which is the fix an earlier round made
precisely so the line and its sentence could not disagree. Loading the value
off a recorded manifest is honest too -- the manifest says stale, and the read
repeats it -- and silently re-inferring `current` over a recorded `stale` would
replace a recorded fact with a guess.

Making a pass write it is worse. A pass records what it did; staleness is a
comparison an idle reader makes between the recorded identity and the current
configuration, and a pass that has just written vectors is by construction
current. The cost of all this is one clause in two documents.

### To land

- `docs/json-schema.md:404` -- replace the "earlier build ... interrupted"
  clause with what is measured: no pass in this build writes `stale`; it is the
  value the read derives from the identity comparison, and a manifest that
  records it is reported as it stands.
- `src/code_indexer/vectors.py:8-14` -- the module docstring says `vectors_state`
  is "the state the LAST write recorded"; state that `current`, `absent` and
  `error` are recorded, and that `stale` is derived at read time.
- Acceptance test: a pass never records `stale`. In `tests/test_fingerprint.py`
  or `tests/test_probe_free_reads.py`, assert the marker after a stubbed
  vectors-on pass that succeeds (`current`) and after one whose embedder fails a
  chunk (`error`); and a read-side test that a manifest recording `stale` still
  prints `vectors_state=stale` with the marker sentence.

### What it means for an index on disk

Nothing. No value, row, answer or exit code changes.

### Follow-up

None.

## The error verdict beside the fingerprint sentence

### Measured

Both places that record `error` do so in the same step that records each lost
chunk: `src/code_indexer/indexer.py:482` and `:691` write an `index_errors` row
per failed chunk, and only then does `:641`/`:743` set `vectors_state=error`. So
a state produced by a pass always has at least one error row, and `index-status`
prints them (`src/code_indexer/cli.py:1630`) as `last_errors=`, capped at three.

The probe's three shapes are in the table above. Two measured facts follow:

- the round's observation that "neither says anything about the lost chunks" is
  true only of a manifest whose error rows are gone; a state a pass produced
  carries its chunk names on the same line;
- the cap is the real gap. `last_index_errors(3)` prints three rows and no
  total, and `manifest.count_index_errors()` (`src/code_indexer/manifest.py:972`)
  has no caller. A pass that lost forty chunks prints three and its count
  nowhere on the read surface -- only once, at pass time, on the pass's own
  stderr (`indexer.py:555-557`).

The silence of the sentence is not an oversight: `docs/json-schema.md:404-407`
says the error marker "is not a fingerprint branch, and the sentence does not
speak to it". The sentence describes the key comparison, and every word of it is
true in the error states.

### Ruling

**Each surface names its own fact** -- the sentence keeps describing the key
comparison, and the line keeps naming the lost chunks -- **but the sample must
carry its total**: the read surface states the number of recorded chunk errors
beside the three examples it shows.

### Why

An error branch in the sentence would make it speak for a marker whose fact is
already on the line, and would grow the branch table the documentation test
derives from `MEANING_BRANCHES` for a sentence that is not currently false.

The incomplete fact is the count. Three samples with no total is the shape that
reads as "three chunks": the reader cannot tell a small loss from a large one,
and the only place the true number appeared was a line printed hours earlier by
the pass. A count is the smallest addition that makes an already-correct fact
whole, and it is already computed.

### To land

- `src/code_indexer/cli.py:1625-1636` -- print the total from
  `Manifest.count_index_errors()` beside the capped sample (one field, e.g.
  `last_errors=` naming the total and the number shown, or a separate
  `error_count=`), and carry the same number in the JSON answer.
- `docs/json-schema.md` (`index-status --json` field list and the paragraph
  after the branch table) and `skill/references/indexing.md` (the status
  paragraph) -- name the field and that the sample is capped while the count is
  not.
- Acceptance test: a manifest with five recorded chunk errors prints the total
  and three examples in text, and the JSON carries the total; a project with no
  error rows prints neither field.

### What it means for an index on disk

Nothing. No marker, row or verdict changes; one more field is printed.

### Follow-up

None.

## The stderr channels outside the enumeration

### Measured

A mechanical scan of the package for `err=True` and `sys.stderr` at this tip
finds 23 sites: 20 text writers (19 in `cli.py`, one in `watcher.py`), one
stream redirect (`watcher.py:324`, the daemon), `errors.fail()`'s single
failure line (`src/code_indexer/errors.py:407`), and the logging handler
(`src/code_indexer/logsetup.py:34`, WARNING by default). The plan commands print
their own failure line directly (`cli.py:3222`), so `fail()` owns the CLI seam,
not every failure.

The contract's case list (`docs/json-schema.md:741-765`) now names six read
cases, including the log note, plus the two channels outside the enumeration,
and `tests/test_docs.py` pins both. One clause is still false: `docs/json-schema.md:769`
says "every read-side line above is a `typer.echo(..., err=True)` call, except
two writers that pick their stream per answer" -- and the log-note case is in
that same list and is a logging record, not an echo.

### Ruling

**Name the rule and keep the enumeration of cases.** The rule: a line reaches
stderr from one of three kinds of writer -- a read's own note (an echo whose
stream the case chooses), the one failure line from `errors.fail()`, or a log
record from the package's handler at WARNING by default. A line with neither a
documented prefix nor an `ErrorType` is a log note. The mechanism clause that
claims every line is a `typer.echo` call is deleted.

### Why

An enumeration of mechanisms is a second list the code can outgrow without
anything going red; the rule is checkable from the line a reader actually has
in hand, by its prefix. The enumeration of cases stays because each case needs
its condition and its stream, which is what a caller branches on -- and because
the doc test already derives six bullets from it.

### To land

- `docs/json-schema.md:769-775` -- replace "In this build every read-side line
  above is a `typer.echo(..., err=True)` call, except two writers ... and two
  channels reach stderr outside that enumeration" with the rule, keeping
  `errors.fail()` and the logging handler as the two named instances.
- `tests/test_docs.py::test_the_success_stderr_contract_is_an_enumeration_not_an_exclusivity_claim`
  -- keep its current assertions and add one that the mechanism claim is
  absent, so the false clause cannot be reintroduced by a later edit.
- No code change.

### What it means for an index on disk

Nothing.

### Follow-up

None.

## Two corrections already landed

### Measured

- The `sweep-orphans` row of the reference's write-command table
  (`skill/references/indexing.md:103`) now names the shared
  `Warning: index is stale: ...` line first, then the two notes. Measured on a
  real dry run at exit 0 over a stale tree: stderr carried the warning line and
  none of the notes (there were no orphans). The row is the whole stderr of a
  successful sweep, not only its own notes.
- The graph-notes bullet of the error contract now names the third stream case:
  the notes go to stdout when the answer is `truncated` **or empty**
  (`docs/json-schema.md`, the bullet `find-callers`/`find-callees`), which is
  the rule the coverage bullet already stated.

Both are pinned: `tests/test_docs.py::test_the_sweep_orphans_cell_names_every_line_a_success_writes`
and `::test_the_graph_notes_bullet_names_the_empty_answer_stream`.

### Ruling

**No ruling needed; closed.** Both were corrected before this record and each
correction is held by a test, so nothing here is left for a later leg.

### To land

Nothing.

## An anchored pattern inside a nested ignore file

### Measured

The scanner hands every spec the same root-relative path: `_ordered_specs`
(`src/code_indexer/scanner.py:105`) collects the specs from the root outwards,
and `_ignored` (`:117`) asks each of them about the same `rel_path`. A spec that
lives in a subdirectory therefore matches with the wrong origin, and git's
anchoring rule (`/x` means "here, beside the file that says it") cannot be
expressed below the root.

A probe tree with the same rules written to both ignore files, compared against
`git check-ignore`: git

| path | rule that decides it | scan | git |
|---|---|---|---|
| `top.txt` | root `/top.txt` | dropped | ignored |
| `sub/secret.txt` | `sub/`'s `/secret.txt` | **kept** | ignored |
| `sub/inner/secret.txt` | none | kept | not ignored |

The anchored root pattern works; the anchored nested pattern silently does
nothing. The reference teaches the anchored spelling for the repository's own
rules (`/runs/`, `/eval/`, `skill/references/indexing.md:364-372`) without
saying that it stops working one directory down, and the scanner's docstring
(`scanner.py:177`) claims the specs "compose the way git composes them".

### Ruling

**Anchor-resolve nested patterns.** Each spec is asked about the path relative
to the directory that carries it; a pattern's anchor is that directory, as in
git.

### Why

An anchored pattern in a nested ignore file is the ordinary way for a
directory to say "this file here", and this repository's own rules use the
anchored form. Documenting "only unanchored patterns work below the root"
would codify a silent no-op for a standard spelling, in a scanner whose stated
contract is git's composition, and it would preserve exactly the divergence the
composition ruling removed: callers reason with `git check-ignore`, and the
staleness check shares this scan, so one file leaves two surfaces at once --
and in the opposite direction from that ruling, invisibly to coverage, because
the scan set is the N in its own count.

The change is contained: the chain already knows each spec's directory, so only
the path handed to `check_file` changes, and the last-matching-rule order is
untouched.

### To land

- `src/code_indexer/scanner.py` -- `_ordered_specs` returns each spec with its
  directory (root first); `_ignored` computes the path relative to that
  directory before `check_file`. Both the file loop and the directory prune use
  the same helper.
- `tests/test_repo_exclusions.py` -- extend
  `test_the_scan_agrees_with_git_check_ignore` with an anchored nested rule
  (`src/.gitignore`: `/secret.txt`) and assert the scan drops `src/secret.txt`
  while keeping `src/nested/secret.txt`; add the same case to a direct scan test
  so the guarantee holds where git is absent.
- `skill/references/indexing.md` -- the exclusion paragraph says an anchored
  pattern is anchored to the directory of the file that carries it, as in git.

### What it means for an index on disk

A project that used anchored patterns in nested ignore files now scans **fewer**
files. Rows those files already have become `deleted` staleness reasons on the
first read after the upgrade and are dropped by the next pass
(`reindex-project`). No manifest schema change and no extraction-version bump.
This is the opposite direction from the composition ruling, whose scan set could
only grow, so the release note must say so.

### Follow-up

None.

## The prune's earlier stopping point

### Measured

The prune now consults the specs from the root down to the directory's parent
(`src/code_indexer/scanner.py:209-218`), so a root-level directory exclusion
stops the walk instead of being applied file by file. Probe: a tree with a root
`skipdir/` exclusion and files three levels down.

| walk | file set | directories walked |
|---|---|---|
| pruned (shipped) | `.codeindexignore`, `e.py`, `keep/d.py` | 3 |
| unpruned (walk everything, skip each file by the same verdict) | same set | 5 |

A second probe puts a negation inside the excluded directory
(`skipdir/.codeindexignore`: `!a.py`): git reports `skipdir/a.py` ignored by
the root rule, the unpruned walk drops it, and the pruned walk never reaches it.
The three agree, which is git's own rule -- nothing inside an excluded directory
can be re-included, because there is no walk left to re-include in.

### Ruling

**Confirmed, and pin the file set rather than the walk.** The only behavioural
delta is that fewer directories are read; the rows are identical. The pin is the
agreement with git on the file set, including a nested negation inside the
excluded directory.

### Why

The file set is the contract: it decides which rows exist and what the
staleness check compares against. A walk-length assertion would pin an
implementation detail and go red on an unrelated fixture change, while telling
a reader nothing about correctness. The case that could regress is a nested
negation resurrecting rows inside a pruned tree, and comparing the set with
`git check-ignore` is exactly the check that catches it.

### To land

- `tests/test_repo_exclusions.py` -- one test: a root directory exclusion with a
  nested negation inside the excluded directory; assert the scan set equals what
  `git check-ignore` reports unignored, and assert directly that the file is
  dropped so the guarantee survives where git is absent. Its docstring says the
  prune is git's rule, not an optimisation a later change may remove.

### What it means for an index on disk

Nothing. Same rows, same scan set, longer or shorter walk only.

### Follow-up

None.

## What this record does not decide

- **Any architectural record.** Nothing here changes ADR-0003, ADR-0004 or
  ADR-0007, so no ruling is marked for the owner's ratification. ADR-0003
  section 3 binds the staleness check to "the scanner's existing include and
  exclude rules", and the anchored-pattern ruling moves that scanner without
  changing the binding; ADR-0001's list of vector states stays as written,
  because none of the four values is removed or renamed.
- **A vectors-off pass leaving an earlier marker in place.**
  `src/code_indexer/indexer.py:636-637` writes `absent` only when no marker
  exists, so a project that was vectored once and is now kept off prints the
  earlier `vectors_state` beside `vectors=off`. That is pre-existing and outside
  the batch measured here; it wants its own reading before it wants a ruling.
- **The scoped-indexing include and priority patterns.** They are matched
  against the project-relative path by design and are not part of the ignore
  composition.
