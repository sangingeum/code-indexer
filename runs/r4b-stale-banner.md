# The staleness banner states its count, and the JSON keeps the reasons

Tip left by this leg: `7bccc54` (base) -> `93e9a57` (implementation and its
tests) -> `a9fc264` (entry document, reference, changelog).

## The problem

Every read of a genuinely stale project printed the same multi-clause banner
on stderr, per invocation. A working session read the tree many times, so the
paragraph repeated until it stopped being read at all -- the one time it
mattered, the reader had already learned to skip it. The banner was true and
visible and therefore useless.

## The chosen shape: a stateless one-line count

```
Warning: index is stale: 1 added file -- <path>; refresh with code-indexer reindex-project <path>
```

- Always visible: it prints on every read, because the process computing it
  just walked the tree and knows the verdict. Nothing has to be remembered and
  nothing can go stale about the truthfulness of the line.
- Cannot lie: the count is derived from the verdict of that call, so it always
  describes the tree as it is now. It cannot say "already reported" about
  something it did not report.
- Short: the kinds and their counts, the project, and the repair command. The
  per-file reasons, the revision and the pass timestamp are NOT in the line;
  they remain in the JSON block on every call, and `index-status` prints them
  on request, so the brevity never reaches the machine-readable answer.
- Per kind, with plurals: `1 added file`, `2 modified files, 1 deleted file`,
  fixed order (modified, added, deleted), so two stale reads of the same tree
  are byte-identical and a reader can compare them at a glance.
- One line, unchanged exit code (0). The documented exception to
  stderr-empty-on-success keeps its new shape: exactly one stderr line, no
  stdout, exit 0.
- The same function renders it on every surface that prints it -- the scoped
  reads, `get-code-context`, and the fan-out loop `--all-projects` uses.

A file the sensitive layer withholds is still a SKIP, never a stale reason,
and on a project that is both stale and holds skipped files the same line
carries both facts, as two clauses:

```
Warning: index is stale: 1 added file; 1 file skipped (sensitive content) -- <path>; refresh with code-indexer reindex-project <path>
```

`index-status` states the skip in its own stdout sentence, so its warning
omits the clause (`include_withheld=False`) and the fact prints exactly once in
that command's output. Every other read prints it in the warning.

## The rejected shapes and why

1. **Once per project per session, keyed on an explicit environment token.**
   Rejected. It makes the first read of a stale tree print a different line
   from the second through nth, for a tree that has not changed: the text a
   reader sees then depends on plumbing outside the command rather than on the
   tree. It also requires the caller to set the token (with none set it must
   talk on every read, so the feature does nothing for callers that do not
   know about it), and the token cannot actually distinguish "the same reader"
   from "the same shell environment" -- which is the fact the suppression
   would be asserting. A CLI cannot know it is the same reader; a token is a
   weaker stand-in for that knowledge, and the count already makes repetition
   tolerable, so the mechanism buys nothing but a second code path.
2. **A state file, marker or recorded timestamp (including the manifest's
   mtime) that silences later reads.** Rejected outright. It lets a LATER,
   unrelated process believe it already told someone something that process
   never saw -- including a different reader in a different terminal. Basing
   the silence on the manifest's mtime is worse: that stamp says when the
   index was written, not who read the banner, so it ties silence to an
   unrelated event.
3. **Suppressing the first occurrence, or making a stale read look fresh.**
   Rejected outright: the first occurrence is exactly the one that matters,
   and a stale read that looks fresh is a lie in the dangerous direction.
4. **Changing exit codes.** Not done: a stale read is exit 0 with one stderr
   line; a schema-version refusal stays exit 4; a failed command still prints
   no answer on stdout.

## Bar 1 -- a session-shaped sequence, base against tip

Throwaway project: two files indexed, then one file added (`gamma.py`). Base
(`7bccc54`), five reads -- `runs/r4b-stale-banner/base-five-reads.txt`:

```
== read 1 ==
exit=0
Warning: index is stale (1 file changed since 2026-11-07T10:18:26Z: 1 added); answers describe the index, not the tree. Refresh: code-indexer reindex-project <scratch>/proj

== read 2 ==
exit=0
Warning: index is stale (1 file changed since 2026-11-07T10:18:26Z: 1 added); ...
```

All five reads printed that identical paragraph, exit 0.

Tip (`a9fc264`), the same five reads --
`runs/r4b-stale-banner/after-five-reads.txt`:

```
== read 1 ==
exit=0
Warning: index is stale: 1 added file -- <scratch>/proj; refresh with code-indexer reindex-project <scratch>/proj

== read 2 ==
exit=0
Warning: index is stale: 1 added file -- <scratch>/proj; refresh with code-indexer reindex-project <scratch>/proj

... reads 3, 4 and 5 print the same line ...
```

Five one-line counts, byte-identical, exit 0. No session token exists and none
is read: the same file also runs a sixth read with the token variable absent
from the environment, and it prints the same count -- there is no "full banner
first" case to fall back to, because nothing is ever suppressed.

The fan-out surface (`list-projects`, which runs the same per-project loop
`--all-projects` uses) prints one count line per stale project, and none for
the fresh one:

```
Warning: index is stale: 1 added file -- <scratch>/proj; refresh with code-indexer reindex-project <scratch>/proj
Warning: index is stale: 1 added file; 1 file skipped (sensitive content) -- <scratch>/proj2; refresh with code-indexer reindex-project <scratch>/proj2
```

`semantic-search --all-projects` shares that loop but refuses before it on
these fixtures (vectors=off, exit 4, `ConfigError`), so the fan-out loop is
demonstrated through the list surface rather than claimed for a command that
could not reach it here.

## Bar 2 -- JSON completeness

Same stale project, five `--json` reads at base
(`base-json.txt`) and five at the tip (`after-json.txt`). Every read carries:

```
{"stale":true,"stale_known":true,"stale_reasons":[{"kind":"added","file":"gamma.py"}],
 "stale_reason_count":1,"withheld_count":0,"withheld_files":[],"withheld_summary":null,
 "withheld_unexamined_count":0,"index_revision":"1","last_indexed":"2026-11-07T10:18:26Z"}
```

A line-by-line `diff` of the two captures' JSON lines is empty: identical to
base, on every read. No JSON key was added, removed or renamed, so
`docs/json-schema.md` is untouched and no manifest schema or
`EXTRACTION_VERSION` change was made.

## Bar 3 -- the fresh case

On this checkout, `overview` and `index-status` each exit 0 with **0 bytes on
stderr** (`after-fresh.txt`). The checkout also holds one file the sensitive
layer withholds; because the verdict is fresh, `index-status` prints the skip
on stdout (`1 file skipped (sensitive content) -- privacy; override with
--allow-sensitive on add-project`) and nothing on stderr -- a fresh verdict is
still audibly fresh.

## Bar 4 -- the double fault (stale AND skipped)

One project holds an added file (`extra.py`) and a withheld file (`notes.md`,
sensitive content). `overview` (`after-double-fault.txt`):

```
exit=0
Warning: index is stale: 1 added file; 1 file skipped (sensitive content) -- <scratch>/proj2; refresh with code-indexer reindex-project <scratch>/proj2
```

One line, two distinct clauses: the skip is not counted as a stale reason, and
the stale verdict does not hide the skip. `index-status` prints the skip once
(its stdout sentence) and its warning carries only the count, so the fact is
never duplicated. JSON carries both, distinctly:

```
{"stale":true,"stale_reasons":[{"kind":"added","file":"extra.py"}],"stale_reason_count":1,
 "withheld_count":1,"withheld_files":[{"file":"notes.md","reason":"sensitive content"}],
 "withheld_summary":"1 file skipped (sensitive content)"}
```

## Implementation notes

- `staleness.warning_line(verdict, project_path, *, include_withheld=True)`
  renders the count; the per-kind phrase is pluralised and ordered
  (modified, added, deleted). The `unknown` line is unchanged: it already said
  the check did not run, and that is not repetition of the stale verdict.
- `cli._read_staleness(..., announce_withheld=...)` passes the flag through;
  `index-status` is the only caller that sets it False.
- `get-code-context` now reuses the verdict it already computed (the discarded
  line from `_read_staleness`) instead of walking the tree a second time to
  rebuild the same warning.
- No file, marker, environment variable or manifest field records that a
  warning was shown. The three tests that pin this are
  `test_stale_answer_prints_one_count_line_on_every_read`,
  `test_the_stale_banner_keeps_no_state_file` and
  `test_json_carries_every_reason_on_every_read`; the double fault is
  `test_a_stale_and_skipped_project_reports_both_states`.

## Verification at the tip

- `uv run pytest tests/ -q -W error`: **1001 passed, 1 failed, 2 deselected**
  (106 s). The single red is
  `tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`:
  the installed skill copy differs from the tracked copy until the release
  step installs it. It is the only red and it is the expected one.
- `uvx ruff check src tests scripts`: All checks passed.
- `uv run python scripts/publishing_hygiene_lint.py`: 0 (run after this
  record was written).
- The five `watcher.py:343` fork warnings under the CI command are untouched
  by this leg; "warn-clean" is not claimed for them.

## Captures

| file | what |
|---|---|
| `base-five-reads.txt` | base tip, five reads, five full banners |
| `base-json.txt` | base tip, five `--json` blocks |
| `base-double-fault.txt` | base tip, stale + skipped project |
| `after-five-reads.txt` | tip, five one-line counts, token-absent read, fan-out, fresh project |
| `after-json.txt` | tip, five `--json` blocks (identical to base) |
| `after-double-fault.txt` | tip, both states distinctly, text and JSON |
| `after-fresh.txt` | tip, fresh checkout: 0 bytes on stderr |
| `base-index.txt`, `base-fresh-status.txt`, `base-fresh-overview.err` | base setup and the fresh read used to seed the fixtures |

## Fix round

The QA gate on this round failed two rows (its record is the verification
document under `runs/` beside this one); everything else it measured was
already green. Both rows are repaired here.

Tip: `a80c9ae` (dispatch) -> `abd6540` (the banner and its pinning test) ->
`d0210fc` (the documents).

### The stale line carries its meaning again

The stale branch dropped `answers describe the index, not the tree`, so the
count read as a fact about the tree rather than about the answer. It is back,
beside the refresh hint, on the same one stateless line -- count, project,
guidance, repair:

```
Warning: index is stale: 1 added file -- <scratch>/proj; answers describe the index, not the tree; refresh with code-indexer reindex-project <scratch>/proj
```

One stale read of a fresh throwaway project (two files indexed, one added
afterwards): exit 0, the answer on stdout, this single line on stderr
(`captures/bar1-text.stderr`). The previous code tip `7704364` printed the
same line without the sentence (`captures/base-text.stderr`).

Nothing stateless was traded away to make room: no session token is read, no
marker file is written, no first occurrence is suppressed, and the line is
rendered from the verdict of the call. The other shapes keep their clauses:

- three kinds at once (`captures/three-kinds-text.stderr`):

  ```
  Warning: index is stale: 1 modified file, 1 added file, 1 deleted file -- <scratch>/proj5; answers describe the index, not the tree; refresh with code-indexer reindex-project <scratch>/proj5
  ```

- stale and holding a withheld file (`captures/double-fault-text.stderr`):

  ```
  Warning: index is stale: 1 added file; 1 file skipped (sensitive content) -- <scratch>/proj2; answers describe the index, not the tree; refresh with code-indexer reindex-project <scratch>/proj2
  ```

- every one of those reads is `exit=0` (`captures/*.exit`).

The pin is `test_the_stale_line_states_what_a_stale_answer_means` in
`tests/test_read_contract.py`: on a one-reason verdict it requires the count,
the guidance sentence and the refresh hint, requires the guidance to come
before the repair, and requires the line to hold no newline. Removing either
clause fails it.

### Every document quotes the shipped line

- `README.md` -- quotes the shipped line verbatim in a fenced block, and the
  same block now names `withheld_count`, `withheld_files`, `withheld_summary`
  and `withheld_unexamined_count` beside the six fields it already listed.
- `SKILL-CLI.md` -- quotes the shipped line and nothing else; the prose about
  what the line does no longer lives there.
- `skill/references/machine-output.md` -- the one document that describes the
  line in prose. Its quote is updated and so is the prose: the line is a
  count with its meaning and its repair, not "the kinds of change and the
  project, and nothing else".
- `CHANGELOG.md` -- the Unreleased entry quotes the shipped line.
- `skill/references/indexing.md` describes withheld reporting, not the banner;
  it already names `withheld_count` / `withheld_files` and needed no change.
- `docs/adr/0003-read-only-and-staleness-contract.md` is untouched: its
  quoted example is the owner's call and is carded separately.

The installed copy of the entry document and its references is refreshed to
the tracked documents, so the digest check is green at this tip.

### The measurements

1. **The line.** A fresh throwaway project, one read -- pasted above. It
   carries the count phrase, the guidance sentence and the refresh hint.
2. **The JSON is unchanged.** `overview --json` from this tip and from a
   worktree at `7704364` over the same index produce byte-identical stdout:
   `captures/bar2-json-diff.txt` is empty, the base worktree head is in
   `captures/bar2-base-sha.txt`. The block still carries all ten fields, with
   `stale_reasons` `[{kind: added, file: gamma.py}]`, `stale_reason_count` 1,
   `index_revision` `1` and a `last_indexed` stamp; the double-fault and
   three-kind captures carry every reason and the withheld fields
   (`captures/double-fault-json.stdout`, `captures/three-kinds-json.stdout`).
3. **Two shells, one line, no state file.** Shell A (`env -i` with the index
   root and a UTC clock) and shell B (a different working directory, `TZ`,
   `LANG`, `TERM`, `SHELL` and a bogus stale-token variable) print
   byte-identical lines; `diff` against the first read is empty
   (`captures/bar3-shellA.stderr`, `captures/bar3-shellB.stderr`). Files in
   the index root and the project tree before and after those reads
   (`captures/state-before.txt`, `captures/state-after.txt`, diff in
   `captures/state-diff.txt`): `manifest.db` is unchanged in size and mtime,
   and the only new entries are SQLite's own sidecars -- an empty
   `manifest.db-wal` and a 32 KiB `manifest.db-shm`. No file named like a
   marker, session, seen, stamp or token exists anywhere in the throwaway
   root (`captures/state-marker-files.txt` is empty).
4. **The grep sweep.** `git ls-files | xargs grep -n "Warning: index is
   stale"` (`captures/bar4-grep.txt`). Every current surface -- the code, the
   tests, `README.md`, `SKILL-CLI.md`, `machine-output.md`, `CHANGELOG.md` --
   carries the shipped text or asserts it. The remaining hits are dated
   evidence under `runs/`: earlier rounds' captures print the shape the tool
   printed then, and the sections above this one record the shape this round
   shipped before the fix. No current-surface document quotes an old shape.
5. **The suite.** `uv run pytest tests/ -q -W error`: **1003 passed, 2
   deselected** in 106.50 s, zero red (`pytest-tip.log`) -- one more test
   than the gate's 1002, the new pin. The installed-copy digest is green
   because the mirror was refreshed to the tracked documents.
6. **Lint.** `uvx ruff check src tests scripts`: All checks passed
   (`ruff.txt`). Publishing hygiene runs after the last capture: see below.

### Captures

| file | what |
|---|---|
| `capture.sh`, `redact.py` | the fixture, the reads and the redaction of host prefixes |
| `bar1-text.*`, `bar1-text-again.*` | the two identical one-reason reads: stdout, stderr, exit |
| `bar2-json.stdout`, `base-json.stdout`, `bar2-json-diff.txt` | the JSON read at this tip and at `7704364`, and their diff |
| `base-text.stderr` | the same read at `7704364`, without the sentence |
| `bar3-shellA.*`, `bar3-shellB.*` | the two unrelated environments |
| `state-before.txt`, `state-after.txt`, `state-diff.txt`, `state-marker-files.txt` | what the reads left behind |
| `double-fault-text.stdout`, `double-fault-json.stdout` | stale and withheld, both clauses |
| `three-kinds-text.stdout`, `three-kinds-json.stdout` | modified, added and deleted at once |
| `bar4-grep.txt` | every tracked quote of the banner |
| `pytest-tip.log`, `ruff.txt` | the suite and the linter at this tip |
