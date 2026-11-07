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
