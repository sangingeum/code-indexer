# Fold record — documentation folds of the read-surface round

Base `5178220` (`origin/main`, `%G? = G`), tracked tree clean. Source: the
fold-now items in `runs/r3-review/record.md`. Four documentation edits landed
as the reviewer wrote them; the fifth (the derived compact cases) landed as
the reviewer specified *except* where measurement narrows it — the narrowing
is measured, below. Folds commit `16f0977` (`%G? = G`), with the installed
skill copy re-synced before the gates ran. No behaviour change: `_output_format`,
`command_classes.py` and every renderer are byte-identical to the base
(`git diff 5178220 -- src/` is empty), no schema or `EXTRACTION_VERSION`
change, no reindex, no positional change.

## The five items

| # | file:line | before | after |
|---|---|---|---|
| 1 | `skill/references/query.md:110-114` | "Passing both is `ArgumentError`, exit 2, rather than a silent precedence -- one invocation, one answer format." | "Passing both is accepted when they agree (`--json --format json` asks the same question twice) and is `ArgumentError`, exit 2, when they name different formats -- never a silent precedence, one invocation, one answer format." |
| 2 | `skill/references/semantic.md:56-58` | "`--json` is its exact alias and passing both is `ArgumentError`, exit 2 (see machine-output.md)." | "`--json` is its exact alias; passing both is accepted when they agree and is `ArgumentError`, exit 2, when they name different formats (see machine-output.md)." |
| 3 | `CHANGELOG.md:29` | "... no reindex, and every invocation that worked keeps its answer." | "... no reindex, and every invocation that worked keeps its answer except the one combination named above." |
| 4 | `skill/references/machine-output.md:59-61` | (nothing between the compact table and the refusal paragraph) | "A compact symbol record carries the file, the range, the name and the kind; the text form's `confidence`, `evidence` and signature are not part of it -- read `--format json` when those are the question." |
| 5 | `tests/test_output_format.py:420-421` (removed) → `:426-496` | `assert COMPACT_FORMAT_COMMANDS <= READ_COMMANDS` | derived cases; see below |

Item 1's paragraph was re-wrapped after the replacement so no line runs past
the file's column; the reviewer's words are unchanged. Item 3 splits onto a
second line, so the entry document goes from 1312 to 1313 lines and 86631 to
86670 bytes (+39 B); nothing else in that file changed. No budget crosses:
`SKILL-CLI.md` is untouched at 120 lines / 7810 bytes (its 95.3% byte spend
is the review's finding, not something this fold could subtract), and
`query.md` grows 323 → 325 lines inside 400 / 24576.

## Item 5 — what landed, and the two measurements that narrowed it

The reviewer's sentence "parametrise over `sorted(READ_COMMANDS)` so every
non-member must exit 2 with `has no compact form`" is false for three reads,
measured here at the base tip:

- `deps mod.py --project code-indexer --format compact` → exit 0, prints the
text tree. `deps`' `--format` is its own unvalidated vocabulary
  (`cli.py:768`), not the shared flag (`cli.py:235-258`).
- `eval-compare a.json b.json --format compact` → exit 2, `No such option:
  --format`.
- `eval` declares no `--format` at all (`cli.py:3106-3117`).

So the parametrisation runs over the reads that carry the shared flag at all —
`FORMAT_ROSTER`, already derived at import from the typer registration — and
the three reads without it are named by a separate assertion
(`READ_COMMANDS - FORMAT_ROSTER == {"deps", "eval", "eval-compare"}`).
Nothing is skipped: every one of the 19 flag holders is a case, and the 3
non-holders are pinned by name rather than assumed.

The second measurement is the review's own (a): two flag-roster reads answer
compact without being frozenset members, because the hidden aliases render
under their canonical command's name (`map` → `outline`, `file-outline` →
`skeleton`). The first run of the new case proved it rather than assuming it:

    2 failed, 81 passed — test_compact_is_offered_exactly_where_the_roster_says[file-outline]
    and [map], both exit 0 where the case expected the refusal

(`runs/r3-folds/pytest-testmod.log` holds that run.) The landed form names the
two aliases in `COMPACT_RENDERED_AS`, treats a command as offering compact only
when it or its canonical name is a member, and adds
`test_the_only_reads_that_answer_compact_unlisted_are_the_two_aliases`, which
measures the accepted-but-unlisted set and requires it to be exactly those two
names — so a third silent alias cannot appear.

The four cases at the tip:

- `test_the_compact_roster_is_a_subset_of_the_reads_that_take_the_flag` —
  the frozenset is a subset of the flag roster, and the three reads without the
  flag are exactly `deps`, `eval`, `eval-compare`.
- `test_compact_is_offered_exactly_where_the_roster_says` — parametrised over
  `sorted(FORMAT_ROSTER)`; a member must exit 0 with output and no refusal, a
  non-member must exit 2 with `<command> has no compact form; supported
  formats: text|json` and empty stdout. Both plan reads are given a plan path
  that does not exist, which is safe because both resolve the format before
  they resolve the ref (`cli.py:2916` vs `:2920`, `cli.py:2966` vs `:2971`).
- `test_the_only_reads_that_answer_compact_unlisted_are_the_two_aliases`.
- `test_the_documented_compact_roster_matches_the_frozenset` — reads the
  compact row of the flag table in `SKILL-CLI.md`, extracts the command names
  and compares the set with the frozenset, so the four doc rosters and the one
  code set cannot drift apart silently.

The test module's own docstring carried the same superseded rule ("giving both
flags is a usage error"); it now says accepted when they agree, refused when
they do not. That is the only edit in this round outside the reviewer's five.

## Mutation proof for item 5

`find-references` — a flag-roster read with no compact branch — was added to
`COMPACT_FORMAT_COMMANDS` and the module re-run:

    FAILED tests/test_output_format.py::test_compact_is_refused_where_there_is_no_compact_form[find-references]
    FAILED tests/test_output_format.py::test_the_documented_compact_roster_matches_the_frozenset
    2 failed, 82 passed in 11.10s   (exit 1)

Capture: `runs/r3-folds/mutation-unrenderer-member.log`. The mutation was then
undone (filekeeper `undo_last`, back to the authored hash of
`command_classes.py`); `git status src/` is empty and `git diff 5178220 -- src/`
produces nothing.

Honest limit of the proof: the added member goes red on its refusal case and
on the doc-parity case (the frozenset no longer matches the entry document).
The member branch alone cannot see a silently degraded renderer — a member
that accepts `compact` and prints text still exits 0. The six per-command
compact shape tests are what pin the renderer; the roster cases pin the set.

## The conflict rule — how many times the docs state it

`rg -n "exact alias|EXACT alias" skill CHANGELOG.md SKILL-CLI.md`
(`runs/r3-folds/conflict-rule-sweep-wide.txt`) finds six sites, five of which
state the rule in full and one of which states only the alias relation:

| site | what it states | verdict |
|---|---|---|
| `SKILL-CLI.md:73` | alias; accepted when it agrees; exit 2 when the two disagree | canonical statement |
| `skill/references/machine-output.md:37-41` | the same rule, fully, plus the message's reason | restatement; holds |
| `CHANGELOG.md:9-15` | the same rule plus the one behaviour that changed | restatement; holds |
| `skill/references/query.md:110-114` | the same rule | restatement; holds (was false) |
| `skill/references/semantic.md:56-58` | the same rule, with a pointer to `machine-output.md` | restatement + pointer; holds (was false) |
| `skill/references/indexing.md:203` | `--json` is the exact alias — no conflict rule | alias only; true |

So the fix commit's claim that the rule "is now stated once" is still not
literally true, and this round does not make it true: the rule proper is
stated five times and all five now agree with `_output_format`. The canonical
statement is the entry's flag-table row at `SKILL-CLI.md:73`; the four others
are restatements, and only `semantic.md` points at another page instead of
restating alone. A future round that wants "once" has to make four of these
five into one-line pointers to the fifth.

## Verification at the tip

- `uv run pytest tests/ -q -W error` → 1088 passed, 2 deselected, exit 0
  (`runs/r3-folds/pytest-tip.log`). Base `5178220`: 1067 passed, 2 deselected;
  +21 ids = 19 flag-roster cases + the alias case + the doc-parity case + the
  narrowed subset case − the one removed subset test.
- `uvx ruff check src tests scripts` → 0, "All checks passed!"
  (`runs/r3-folds/ruff.log`).
- `uv run python scripts/publishing_hygiene_lint.py` → 0,
  `scanned=1268 skipped=0 violations=0` (`runs/r3-folds/hygiene.log`).
- `uv run python scripts/generate_skill_docs.py --check` → 0
  (`runs/r3-folds/generate-check.log`).
- Installed-copy digest:
  `pytest tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`
  → 1 passed. The installed skill directory was re-synced before the run
  (`SKILL-CLI.md` → `SKILL.md`, `skill/references/*.md` → `references/`); the
  digest of `SKILL-CLI.md` and of `references/query.md` match their tracked
  copies byte for byte.

Not claimed: "warn-clean". The suite is run with `-W error`, which is a
stronger statement than clean warnings and is the only one measured here.

## Commit

`16f0977` — the four documentation edits, the derived compact cases and the
captures in this directory. This record is the commit directly on top of it.
The installed skill copy is not in the repository's diff (it lives outside the
tree); it was synced from `SKILL-CLI.md` and `skill/references/*.md` at that
commit's tree.
