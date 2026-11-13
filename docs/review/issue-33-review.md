# Issue 33 review — the repair command owns the repair

Two-axis review of the fix round for the staleness banner that could not be
cleared. Base `8e16515`, tip `c72af0a`, six commits: `7729421` (the cheap arm
of `reindex-project` gated on the tree diff), `0f11a65` (an unenterable root
reads `unknown`), `1d3d377` (an existing unreadable file is not `binary`),
`55c4e76` and `c72af0a` (the prose sweeps).

Reviewed at tip `c72af0a`, tracked tree clean. The two gates are of record and
**their bars were not re-run**: `runs/issue-33-gate/VERDICT.md` (FAIL, 7 of 8)
and `runs/issue-33-reverify/VERDICT.md` (PASS, 7 of 7) both stand as written.
What this review adds is a code-and-design ruling on the shipped path, and
five small probes of its own (`runs/`-free: fixtures under the scratch
directory, nothing registered, no live pass touched).

**Verdict: changes requested.** One measured route of the very defect the
round set out to close survives — one directory down from where it was fixed —
and it deletes rows silently. Everything the round claims about the paths it
touched is true as claimed; that is the whole difficulty: the round fixed a
class of defect at the root of the walk and left the class itself in place.

---

## Axis 1 — standards: is the tree-match rule at the right seam?

### The seam is right, and it is one seam

`Core.verdict_against_tree` (`src/code_indexer/core.py:633`) is the only
rows-versus-tree computation in `src/`: one definition, two readers —
`Core.staleness` (`:627`) and `Core.tree_needs_a_pass` (`:691`). `staleness.detect`
has one caller (`core.py:686`), and both `scan_project` call sites
(`core.py:671` for the diff, `indexer.py:390` for the pass) read the same
scope, the same `previous` stat map and the same `max_file_bytes`, because all
three come from the manifest and the config rather than from the caller. That
is the correct seam: the question "do these rows describe this tree" belongs
to the module that owns the rows, not to the staleness renderer and not to the
command.

No pass path reads `rows_current` as a statement about the tree any more.
The two gates that decide a parse (`core.py:506-514` in `run_index`,
`core.py:1044-1046` in `reindex_with_swap`) both conjunct
`not tree_needs_a_pass(...)` onto the bookkeeping predicate. The remaining
reader, `core.py:1178` in the point repair, asks whether the stored rows carry
a stable numbering — a question about rows, correctly answered by a fact about
rows.

### S1 (blocking) — a subtree this process cannot enter is still read as a tree that emptied

`src/code_indexer/scanner.py:477` walks with `os.walk(root, topdown=True)` and
no `onerror`. Python's `os.walk` ignores a `scandir` failure on a subdirectory
and simply yields nothing for it. The root probe added at `scanner.py:442`
catches the root and nothing else, so the fix covers exactly one level of a
class of failure that has no natural depth.

Measured (fixture: `a.py`, `sub/b.py`; `chmod 000` on `sub`; real scanner and
real `staleness.detect`):

```
first  paths: ['a.py', 'sub/b.py']
second paths: ['a.py']
second accounting: could_not_read=[]  skipped={}  walked=1
diff:  state=stale  reasons=[('deleted', 'sub/b.py')]
pass:  deleted = ['sub/b.py']
```

The pass's deletion list is `indexer.py:601-607` (`old_files` minus
`scanned_map` minus `could_not_read`); with the subtree missing from all
three, the row is purged from the manifest and its points from the collection,
no `index_errors` row is written, and the next `index-status` reads `fresh`
because the walk no longer lists the name. That is "fresh by forgetting" —
the exact phrase the CHANGELOG uses for the defect this round fixed — applied
to a whole directory instead of a file.

Why it matters beyond the fixture: a stale mount, a directory whose group
membership changed under a running watcher, a `chmod` on a vendored tree. The
round's own bar asked whether a scan set a pass could not see can lose rows;
this is the remaining yes. It is also the same class the first gate failed the
round on (`F1`), reached through the walk rather than through the root.

No test in the round pins a subtree: `tests/test_unenterable_root_is_unknown.py`
pins the root, `tests/test_unreadable_file_keeps_its_row.py` pins a file.

Remedy (one place, both readers): give `os.walk` an `onerror` that reports the
failed directory, and answer it as *no evidence* rather than as an empty
subtree — the diff has no names to list under it, so `unknown` is the honest
read-side answer (not `unreadable` per path, which would claim the check
enumerated what it could not read), and a pass must keep the rows for that
subtree. Because both readers consume the same accounting object, routing the
condition into that object fixes the banner and the pass together.

### S2 (minor) — `skipped.unreadable` and `could_not_read` are one fact in two spellings, and one site forgets the second

`scanner.py:528-533`: a path that exists but is not a regular file is counted
`SKIP_UNREADABLE` and not added to `could_not_read`. Measured (a file replaced
by a FIFO):

```
skipped={'unreadable': 1}  could_not_read=[]
verdict=stale  reasons=[('deleted', 'a.py')]  unreadable=[]
```

The control is clean: a broken symlink fails at `os.stat`, goes through
`keep_unreadable`, and reads `fresh` with `unreadable=['a.py']` — so the round
fixed the realistic case and left the pathological one split. The consequence
is not silent data loss (a path that is no longer a regular file arguably has
no row to keep), it is that a consumer reconciling `scan_accounting.skipped`
against `unreadable_count` sees 1 and 0 for the same path, and the two
readers this round unified disagree about whether it is unreadable or deleted.
Either route it through `keep_unreadable`, or give it its own bucket
(`skipped.not-a-file`) so `unreadable` stops meaning two things.

### S3 (minor) — the ADR still promises a vectors-on pass reads no file

`docs/adr/0001-core-and-extension-separation.md:62` states, in the present
tense, that a vectors-on pass "reparses nothing and reads no file". The tree
diff now walks and reads a bounded prefix of every file, which is what the
comments, the CLI help and the skill reference were corrected to say. See the
ruling on Q1: this is owed a correction, not a CHANGELOG entry.

### S4 (minor) — the CHANGELOG contradicts itself about the vectors-off arm

`CHANGELOG.md:28-29` says the arm "cannot parse a tree it cannot see either,
and it deletes nothing on the way"; `CHANGELOG.md:36` then says "The
vectors-off arm is untouched". Both cannot hold: at the base an unenterable
root walked to nothing and the arm deleted every row (measured at the fold
base by the narrow re-verify, bar 5), and at the tip it exits 1 with the rows
kept. "Untouched" should read "unchanged on a readable tree".

### The cheap vector-only path is still justified — and it is correct on the arms that matter

- **Justified.** What the arm avoids is embedding; what the diff costs is a
  walk plus two prefix reads per file (the binary sniff and the utf-8 sniff;
  the first gate measured 8 `open` calls for two files). Walk cost measured
  here for scale: this repository, 27,287 walked entries in 0.03 s; the whole
  development tree, 37,813 entries in 0.58 s. A walk is not the expensive half
  of a rebuild.
- **Vectors-off arm:** `reindex_with_swap` never reaches the gate on that arm
  (`core.py:1073-1078` always calls `index_project` with `force_full=True`),
  so it always parses and needs no tree proof. Correct.
- **Pre-versioning manifest:** `vectors.rows_current` (`vectors.py:74-88`)
  requires a recorded `extraction_version`, so such a project never takes the
  cheap arm; it parses. There is no "cheap arm on no evidence" path here.
- **Changed scope patterns:** the diff reads `scope_include` from the manifest
  (`core.py:653-654`) and the pass reads it from the same manifest
  (`indexer.py:382-384`), so a narrowed scope purges out-of-scope rows on both
  sides with the same word. The readers cannot disagree about scope; they can
  only both use a harsh word (see N2).

### Terminology and docs

No coordinate labels, no round or gate references in code, comments or test
names; the new prose names concepts ("tree", "rows", "unreadable", "no
evidence"). The `reindex-project` help, `skill/references/indexing.md:133-140`
and `:288-296`, `docs/json-schema.md:283-304` and the CHANGELOG all describe
the shipped rule, with the two accuracy defects above (S3 outside the diff's
surfaces, S4 inside it) and the scope limit in S1 not stated anywhere.

---

## Axis 2 — architecture: does the repair command own the repair it advertises?

Yes, for every tree it can finish walking. `reindex-project --vectors on` on a
rows-current project that has moved on now parses (first gate bar 2 end to end:
`files_indexed 2`, banner clears, revision advances), and the same command on a
matching tree stays cheap (bar 3). The two places that could still take the
repair's name without doing the repair are the two findings below: a walk it
cannot finish, and a walk it cannot enter everywhere.

### A1 (design) — the pass decides a write under the read side's budget

`tree_needs_a_pass` inherits the staleness budget through
`verdict_against_tree` (`core.py:665-666`: `SCAN_BUDGET_FILES = 200_000`,
`SCAN_BUDGET_SECONDS = 5.0`), and a budget stop is `unknown`, and `unknown` is
"keep the rows, parse nothing". On a tree the diff cannot finish walking, the
repair command therefore returns `files_indexed: 0` and does nothing, with no
diagnostic saying why. The mechanism is measured — the first gate's bar 4a set
the budget to one entry and watched the cheap arm run with zero parses.

The budget is the right instrument for a read (a status call must answer
promptly) and the wrong one for a pass (a rebuild may take minutes, and its
cost is bounded above by the parse it is deciding whether to skip). A 5-second
walk on this host covers several hundred thousand entries, so the case needs a
large tree or a slow mount — but on a slow mount it is exactly the case where
the banner says `unknown` forever and the documented repair cannot change that.

Remedy: `tree_needs_a_pass` should ask with the pass's own budget (no entry
cap, generous seconds), and a stop it does hit should be named in the result
rather than reading as "nothing to do".

### A2 (design) — one boolean where the honest answer is two questions

`core.py:718` reduces the verdict to `state == STALE`. On the read side
`unknown` is a real third state (the banner says the check did not run,
`coverage.freshness=unknown`, `stale_known=false`); on the pass side it
collapses into `fresh`, because both mean "do not parse". That collapse is
sound for deletions and over-broad for reparsing: the parser does not need the
whole-tree diff to know what to re-parse — a path the walk *did* see as added
or modified can be parsed safely and additively — while the delete decision
does need full evidence and must be withheld when the verdict is `unknown`.

Separating the two would repair A1's case (parse what was proved changed,
delete nothing), and it is the natural home for S1 (a subtree the walk could
not enter is an evidence gap for deletion, not a reason to skip every parse in
the project). This is a deepening, not a defect: the round's boolean is honest
about what it knows, and the cost of the coupling is a repair that is
all-or-nothing. Worth its own piece of work, not a fold into this round.

### Is `could_not_read` the right primitive?

Yes — one fact with two readers, owned by the walk that produces it, threaded
as a set rather than re-derived by the reader that needs it. It is deliberately
outside `ScanAccounting.as_dict()` (`scanner.py:371-381`), so the stored
accounting schema and every version axis stay put; the narrow re-verify
confirmed the manifest meta is unchanged except for the pass's own stamps.

The residual smell is S2: the set and the `skipped` count must be kept in step
by hand at four call sites, and one of them misses. That argues for making
`keep_unreadable` the only writer of both, not for a different primitive. The
third reader of the same word — `staleness._classify_withheld` re-opening
rowless paths — is inherent: the pass never sees those paths.

### Can a pass still delete rows a scan could not see?

Route by route, against `scan_project` as shipped:

| route | outcome |
|---|---|
| root absent or unenterable | `OSError` from the probe at `scanner.py:442` → `unknown` → rows kept |
| **subdirectory unenterable or vanishing mid-walk** | **silently absent from the scan set → rows deleted (S1)** |
| `stat` fails on a listed name | `keep_unreadable` → row kept, counted, reported `unreadable` |
| binary sniff or utf-8 sniff raises | `keep_unreadable` → row kept, never `binary` again |
| hash read fails | `keep_unreadable` → row kept |
| path is not a regular file | counted `unreadable`, not in the set → row deleted (S2) |
| walk budget stop | `unknown` on the read side; the pass walks unbudgeted |
| symlink loop | `os.walk` does not follow links (`followlinks` default) — no loop, and no rows can exist beneath one |
| linked worktree / submodule `.git` file | named skip, by design |
| ignored subtree | pruned and counted as ignored-directory, by design |
| file gone by the read | `FileNotFoundError` → out of the scan set, row purged, no error row, `walked` decremented |

S1 is the one row that matters, and it is the class this round named.

---

## Rulings

### Q1 — an ADR's false behaviour claim is a correction, not a CHANGELOG entry

**Owed to the ADR.** The household rule to apply:

> An ADR's decision, its rationale and its stated consequences are frozen. A
> present-tense claim about shipped behaviour inside an ADR is corrected in
> place — in the same commit as the change that falsified it — marked
> `(amended YYYY-MM-DD: what changed)`. History prose ("we chose X because") is
> never rewritten.

Why not the CHANGELOG: ADRs are cited from code as normative — `indexer.py`
cites "ADR-0001 section 2" when it decides a collection swap — so a false
sentence in one is read the way a false docstring is read. The CHANGELOG records
what changed between releases; it cannot correct a record a reader is sent to
for what is true now. The amendment marker is what keeps the ADR honest as a
record: the sentence is repaired, and the date of the repair is visible.

### Q2 — the pass-side class should be one a caller can act on

**Yes, change it.** `InternalError` is this codebase's "something we did not
foresee" bucket (`errors.py:134-136`, rendered with a `logger.exception`
traceback), and a directory permission is neither unforeseen nor a bug; the
read side already names the same condition. The class is the only channel a
caller has.

Name: `TreeUnreadableError(IndexerError)` with `exit_code = EXIT_FAIL` (1).
The exit code must not move — ADR-0003 section 5 fixes the taxonomy and 1 is
"any other runtime failure" — so the change is a name and a render (one
`ErrorType:` line, no traceback), plus a mapping in `errors.classify`
(`errors.py:381`) so an `OSError` escaping the scan becomes that class at the
indexer boundary. Message shape: the path, the errno text, and the remedy
("fix the permissions or the mount and re-run"). Own piece of work; it changes
no rows and no answer, only the diagnosis.

### Q3 — keep the row on a transient stat miss, and let the next pass purge it

**Right trade.** The two errors are not symmetric: purging a live file's row
makes the index claim a file is gone and costs a full reparse when it comes
back, while keeping the row can only make the banner say "could not read",
which is true. One pass of a slightly wrong classification that heals itself
beats one pass of a false deletion that the tree never asked for — and the
`unknown`-keeps-rows rule this round established is the same judgement.

Boundary to pin (one test, two consecutive passes):

- **pass N** — name listed by the walk, `os.stat` raises: the row survives, the
  path is counted `unreadable` and named, `walked == indexed + uncommitted + skipped`
  still holds, and the verdict for that project is not `stale` with a `deleted`
  reason;
- **pass N+1** — name absent from the walk: the row is purged, no `index_errors`
  row is written, and the banner reads `fresh` with no reason naming the path.

That pins the delay as *one* pass and forbids it from becoming a permanent
claim, which is the property that makes the trade safe.

---

## Notes (no behaviour change)

- **N1.** The other skip classes are unchanged: oversized files,
out-of-scope files and ignored subtrees are still purged with a `deleted`
reason on both sides. Consistent readers, imperfect word — nothing this round
introduced.
- **N2.** The diff's sniff reads could be skipped for a file whose stat triple
already matches its row (the hash comes from the fast path anyway). It would
trim two opens per unchanged file on a large tree; it would also split the
classification rule between the two readers. Not worth it.
- **N3.** An unenterable root now makes `index` fail (`InternalError`) where it
used to empty the index. That is better, and it is the same diagnosis problem
Q2 rules on.
- **N4.** The vanished-path bookkeeping (decrement `walked`, pop the scanned
row, purge the older row) reconciles: the first gate measured
`walked 1 == indexed 1 + 0 + 0` on the vanished case, because `indexed` is
recomputed from `committed` at `indexer.py:1021`.

---

## What was measured here, and what was read

Measured (fixtures under the scratch directory, none registered, no live index
touched):

1. `os.walk` subtree hole — scan + `staleness.detect` + the pass's deletion
  predicate, with `chmod 000` on a subdirectory (S1).
2. The non-regular-file split — a file replaced by a FIFO (S2), with a broken
  symlink as the control (clean: `unreadable`, `fresh`).
3. Walk throughput on two trees (27,287 entries in 0.03 s; 37,813 in 0.58 s),
  to size A1 against the 5-second budget.
4. The four new test files plus `tests/test_docs.py` at the tip:
  `62 passed in 2.25s`, warn-clean.
5. `wc -lc skill/references/indexing.md` → `395 21720`, inside 400 / 24576.

Read (not re-run): the two gate verdict records; `core.py` (the gate, the two
diff readers, the swap), `scanner.py` (walk, accounting, the probe),
`indexer.py` (scan call, sensitive filter, deletion lists, the read and vanish
paths), `staleness.py`, `vectors.py`, `errors.py`, the four new test files,
`CHANGELOG.md`, `docs/json-schema.md`, `docs/adr/0001-*`, `AGENT.md` and
`skill/references/indexing.md`.

## Documentation Impact

- **Updated:** this review document (`docs/review/issue-33-review.md`), the only
  file this commit touches.
- **Created:** nothing else.
- **Deleted:** nothing.
- **No doc changes needed because:** the round's own doc surfaces were checked
  and are true of the shipped path within the scope they state.
- **Open items:** S3 (the ADR's present-tense claim, per Q1), S4 (the
  CHANGELOG's "untouched" sentence), and — if S1 is folded — the docs should
  say what an unenterable *subtree* answers, not only an unenterable root
  (`skill/references/indexing.md:139-140`, `:395`, `docs/json-schema.md:296`).

---

# Re-review (fold response)

The original section above stands unchanged: it is the record of the first
review. This section rules on the fold that answered it.

Reviewed at tip `064e9d1` (fold base `c6b7f52`, where the first review is
committed), six commits: the walk's `onerror` hook and its two readers, the
`not-a-file` skip reason, the ADR correction and its register line, the
CHANGELOG correction, and the two-pass transient-stat test. Tracked tree
clean, everything pushed. The gates' numbers (suite at the tip, hygiene, the
replay of the three new test files at the fold base) are of record and were
not re-run; what this section adds is my own reading of the code and six
probes of its own, all on fixtures under the scratch directory, nothing
registered, no live index or watcher touched.

**Verdict: changes requested** — one finding, one line, at the one depth the
hook cannot name. Everything the fold was asked to answer is answered, and
answered as ruled: the blocking finding is closed at every depth under the
root, the two minor findings are closed in the shape I asked for, and the
ruling below on the verdict shape is recorded so it is not re-argued. What
blocks is the depth-0 case: the walk's hook can record the ROOT as `.`, and
the one predicate both readers use matches no path under `.`, so in that case
the pass still purges every row while the round's own prose says it cannot.

## Ruling — the verdict shape: accepted as directed, and better than my first remedy

**Ruling: the shape is right; it supersedes the plain `unknown` my first
review asked for, and it is the round's ruling from here.**

The refinement is defensible against my own remedy, and on one point it is
better than it. My remedy's operative requirement was that the gap must never
be read as evidence of a deletion and must never be read as `fresh`; both
hold. My reason for naming `unknown` was that "the diff has no names to list
under it" — but that is an argument about the `deleted` reasons, not about
the STATE. When something else provably moved, the check did answer the
question the state carries (do these rows describe this tree: no), and
answering `unknown` there would suppress the repair of a tree that provably
needs it — and the failure mode this whole round exists to end is a banner
that names no clearable repair. So: `unknown` with the count and the capped
paths when nothing else is proven, `stale` with the proven reasons and the
gap beside them when something is, and never `fresh` while any directory
could not be entered. That is the shipped behaviour, measured below, and it
is the ruling.

## Bars

### Bar 1 — the property holds at any walk depth: PASS at every depth under the root

`os.walk` recurses, so the hook fires wherever the descent fails; the hazard
the bar names is a partial or absolute name reaching a project-relative
predicate. Measured on a fixture with `a.py`, `sub/c.py`,
`sub/inner/b.py`, `sub/inner/deep/d.py`, `vendored/e.py` and `chmod 000` on
`sub/inner` (two and three levels deep, real scanner, real diff):

```
unenterable_dirs: ['sub/inner']          could_not_read: []
scanned: ['a.py', 'vendored/e.py', 'sub/c.py']
verdict: unknown  unenterable=('sub/inner',)  count=1
        detail='1 directory could not be entered (sub/inner), so not every row could be checked'
block:  {'stale': False, 'stale_known': False, 'unenterable_count': 1,
         'unenterable_dirs': ['sub/inner'],
         'unenterable_summary': '1 directory could not be entered (sub/inner)'}
```

The join is correct: the hook's `exc.filename` is absolute (the walk is
seeded with `root = os.path.abspath(root)`), `relpath` against that root
yields `sub/inner`, and both rows under it — including the one three deep —
survive. The same was measured with a race instead of a `chmod`
(`os.scandir` raising for `sub/inner` after the parent listed it):
`unenterable ['sub/inner']`, `deleted: 0`, both rows kept. The depth-0
variant fails; see the finding.

### Bar 2 — no new route of row loss: PASS on every named sub-case, FAIL at depth 0

The single exclusion set (`unseen_paths`, `indexer.py:607`) is used by the
incremental arm and re-derived for the `force_full` arm (`indexer.py:618`),
and both were measured on the same chmod'd fixture:

```
incremental pass: idle, files_deleted 0, rows {a.py, sub/c.py, sub/inner/b.py,
                  sub/inner/deep/d.py, vendored/e.py}
force_full pass:  idle, files_indexed 3, files_deleted 0, rows unchanged
```

- **Prefix boundary.** With `vendor` unenterable and `vendored/e.py` a live
  row, the verdict names two directories and `vendored/e.py` keeps its row
  (`under_unentered('vendored/e.py')` is false for `vendor`) — the predicate
  matches on a path boundary, not on a string prefix.
- **A path under an unenterable directory that also has a `could_not_read`
  entry.** Impossible by construction (the directory blocks the stat that
  would record it); measured `overlap: []`, and the union at
  `indexer.py:607` is a set, so a hypothetical overlap would be counted once.
- **A directory that becomes unenterable partway through the walk.** PASS for
  every subdirectory (measured above, `deleted: 0`), FAIL for the root — the
  finding below. A directory that is simply GONE before the walk starts is
  not this case: it takes no `onerror` and its rows are purged as ordinary
  deletions (`deleted: 1`, measured), which is correct.

### Bar 3 — the accounting identity and the stored payload: PASS

```
incremental: {'walked': 3, 'indexed': 3, 'uncommitted': 0, 'skipped': {},
              'skipped_total': 0, 'skipped_directories': 0, 'examples': {},
              'counted_exactly': True}
identity: walked == indexed + uncommitted + skipped_total → True (3, 3, 0, 0)
force_full: the same identity, True
```

The stored payload's key set is exactly the eight keys it had before the
fold (`unenterable_dirs` is outside `as_dict`, confirmed by comparing the
written record against the pre-fold key set, not by reading the docstring).
A new skip reason (`not-a-file`) can appear as a `skipped` entry, which is
vocabulary inside an existing key, not a schema change and not a version
axis. Row axes: a `force_full` pass with an unenterable directory logs
"row axes stay at the recorded values: 0 file(s) could not be reparsed and 1
directory(ies) could not be entered" and does not stamp; the embedding side
is still written. `write_embedding_side` + `write_row_side` write exactly the
six items `write_fingerprint` wrote, so the uncoupled normal path records what
it recorded before.

### Bar 4 — the ADR correction and the two-pass test: PASS

- `docs/adr/0001-core-and-extension-separation.md:62` is corrected in place
  with an `(amended 2026-11-14: ...)` marker; the decision paragraph and the
  rationale in the diff are untouched, and the file's diff is the header line
  plus that one bullet. `:7` registers the amendment in the ADR's existing
  `Amended:` header beside the earlier one. A present-tense claim was
  corrected, not history rewritten — the ruling on the first review's Q1.
- `tests/test_a_transient_stat_miss_survives_one_pass.py` pins pass N
  (`files_deleted 0`, row kept, `skipped['unreadable'] == 1`, the path named
  in `examples`, identity true, `FRESH` with `unreadable ['a.py']` and no
  `deleted` reason) and pass N+1 (`files_deleted 1`, row purged, no
  `index_errors` row, `FRESH` with no reason naming the path and no warning
  line naming it). One pass of delay, then the purge — exactly the boundary
  the ruling asked for.

### Bar 5 — nothing else in the round became false: PASS except the claim the finding falsifies

- `CHANGELOG.md:71` now reads "unchanged on a readable tree", and the
  surrounding paragraph states what each arm does with a tree it cannot see
  (vectors-off: fails before writing anything, exits 1, every row kept; a
  subtree it could not enter excluded from its deletions). The second clause
  is true at every depth ≥ 1 and false at depth 0 — the other half of the
  finding.
- `docs/json-schema.md` describes the three new fields, the `not-a-file`
  reason and the `indexed` exclusion, and all of it matches the code.
- The CLI: `index-status` names the directories in its text line (measured:
  `1 directory could not be entered (sub/inner) — not walked; fix the
  directory's permissions and re-read`) and in `--json`
  (`unenterable_count 1`, `unenterable_dirs ['sub/inner']`, the summary), with
  `stale_known false` and `coverage.freshness unknown`. The `reindex-project`
  help's "a tree this process cannot enter ... keeps every row and purges
  nothing" covers the new case without a docstring change.

### Bar 6 — the first round's minor findings, answered as ruled: PASS

- **The one-fact-two-spellings finding.** Answered the second way I named:
  its own bucket. `SKIP_NOT_A_FILE = "not-a-file"` (`scanner.py:316`), counted
  there and nowhere else, with the docstring stating that a row for such a
  path MAY be deleted as a decision. Measured and pinned: a FIFO reads
  `skipped['not-a-file'] == 1`, `could_not_read == set()`, the diff reads
  `stale [deleted a.py]` with no `unreadable`, the pass deletes the row, and
  the control (a `chmod 000` file) still reads `unreadable` and
  `could_not_read {a.py}`.
- **The CHANGELOG's "untouched" sentence.** Answered as ruled, word for word.

## Finding to fold (blocking, one line)

**A root that fails inside the walk is recorded as `.`, and no row is under
`.`.** `src/code_indexer/scanner.py:524` computes the project-relative name of
the failed directory; when that directory IS the root, `relpath` yields `"."`.
The one predicate (`scanner.py:387`, mirrored at `staleness.py:328`) tests
`path == d or path.startswith(d + "/")`, so `"."` matches nothing that is not
literally `"."`. The pass's exclusion set (`indexer.py:607-610`) is therefore
empty and every row is deleted. Measured, with the root's scandir succeeding
for the probe at `scanner.py:472` and failing for the walk's own scandir a
few microseconds later (the window a concurrent unmount, an autofs expiry or
a stale mount produces):

```
cannot enter directory .: [Errno 13] Permission denied: <scratch>/project
scanned: []  unenterable: ['.']
unseen_paths: []   deleted: ['a.py', 'sub/c.py']
```

Why it matters: this is the defect class the round set out to close — a whole
project's rows read as a tree that emptied — reached at the one depth the hook
cannot name, and it falsifies two sentences the fold itself wrote:
`CHANGELOG.md:16` ("a row under such a directory ... is never deleted by a
pass, on the incremental and the `force_full` arm alike") and the
`on_walk_error` docstring's promise that a failure is never left standing for
an empty subtree. It is not a regression — the base deleted the same rows and
said nothing, and the fold at least logs the line and turns the verdict
`unknown` — but the guarantee as stated is not yet true.

Remedy, one line, no new state: treat the root as the root. In `on_walk_error`,
after computing `rel`, `if rel == ".": raise exc` — the same answer the root
probe already gives (an `OSError` the caller answers as `unknown`, and a pass
that writes nothing), which also folds it into the branch that already refuses
to let "could not look" become "empty". One test: the same fixture with a
walk-time root failure, asserting the rows survive and nothing is purged.

## Notes (no behaviour change)

- **N1.** `skill/references/indexing.md` is at exactly its ceiling again —
  400 lines / 22,119 bytes against 400 / 24,576 — and the fold's diff there
  carries four prose compactions outside the round's subject (the
  `sweep-orphans` comment, "(the total)" on `error_count`, "(an approximation,
  not a measured threshold)", the bolded consumer rule). Each is smaller than
  what it replaced; none is false; the new rules fit without crowding
  anything out. Note, not a finding — but the document now has no headroom for
  the next round, which will have to choose between a trim and a split.
- **N2.** The `unknown` branch's lead still reads "index staleness check did
  not run" (`staleness.py:482-484`) for a check that DID run and could not
  look everywhere, with the detail immediately correcting it ("so not every
  row could be checked"). `stale_known=false` carries the same extension and
  `docs/json-schema.md` documents it. Wording, inherited from the
  unenterable-root case.
- **N3.** The transient-stat test does not run a third pass. Nothing could
  resurrect the row (the name is absent from the walk and the row is gone), so
  the property holds by construction, but it is pinned by absence rather than
  by an assertion.
- **N4.** `aggregate` sums `unenterable_count` across projects while merging
  the directory names into one capped list, so two projects holding a
  directory of the same name report `2 ... (vendor)` beside one name. Same
  shape as `unreadable_summary`, so consistent; noted for a reader who tries
  to reconcile the count with the list.
- **N5.** "The one predicate both readers use" is one FACT with two spellings
  of the same one-line test: the pass calls `ScanAccounting.under_unentered`,
  the banner takes the set and applies `_under_directory`. `staleness` must
  not import the scanner, so this is the right split; the docstring's `ONE`
  overstates it slightly.
- **N6.** A directory removed while the walk is running is recorded
  unenterable and its rows survive one pass, then are purged on the next —
  the same one-pass delay the transient-stat ruling accepted, for the same
  reason.

## What was measured here, and what was read

Measured (fixtures under the scratch directory, none registered, no live index
or watcher touched):

1. A nested unenterable subtree, two and three levels deep, through the real
   scanner, the real diff and both deletion arms (bars 1-3).
2. The same subtree failing through an `os.scandir` race rather than a
   `chmod`, to exercise the hook's mid-walk path.
3. The prefix-boundary case (`vendor` closed, `vendored/e.py` live) and the
   vanished-directory case (purged, correctly).
4. The depth-0 case: the walk's root failing after the probe, run through the
   diff and through the pass's own deletion predicate (the finding).
5. A real `index-status`, text and `--json`, against a temporary index root.
6. The three new test files plus `tests/test_docs.py`: `63 passed in 2.35s`,
   warn-clean.

Read (not re-run): the fold's six commits in full; `scanner.py` (the walk,
the accounting, `on_walk_error`), `staleness.py` (detect, `block`,
`warning_line`, `aggregate`), `indexer.py` (the exclusion set, the accounting,
the fingerprint stamp), `fingerprint.py` (the two sides against
`write_fingerprint`), `core.py` (the diff's call and its docstring), `cli.py`
(the `index-status` line and the `reindex-project` help), the three new test
files, `CHANGELOG.md`, `docs/json-schema.md`, `docs/adr/0001-*` and
`skill/references/indexing.md`.

## Documentation Impact (this re-review)

- **Updated:** this review document — the appended re-review section only; the
  first review's record is untouched.
- **Created:** nothing.
- **Deleted:** nothing.
- **No other doc changes needed because:** the round's doc surfaces were
  checked against the shipped code and are true of it, except the one sentence
  at `CHANGELOG.md:16`, which the finding asks the code to make true rather
  than the prose to weaken.
- **Open items:** the depth-0 finding; and, on the next touch of
  `skill/references/indexing.md`, its ceiling (N1).
