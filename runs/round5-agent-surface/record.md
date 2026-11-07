# Round 5, leg 2 -- the agent surface: an entry document, five references, a generated roster and the drift checks

Card id withheld (an authored record is hygiene-governed; the dispatch brief carries it). Base `b918fe1`. Tree `<project-root>`.
The installed skill directory was neither read as a source of truth nor edited: the release step refreshes it after this round's gate, and the drift check that pins it was proved against a throwaway skills root under the test runner's temporary directory instead.

## Commits

| SHA | slice |
|---|---|
| `aaea01b` | the generator, the entry document, the five references with their generated command blocks, and `tests/test_docs.py` carrying the six checks — one commit because the split is red in any smaller piece: an entry document whose triggers point at files that do not exist fails the link check, and a reference whose block is empty fails the generated-block check |
| `021585c` | the README rewritten around the new behaviour, carrying the MCP parity list alone |
| the commit that carries this line | the CHANGELOG entry under `## [Unreleased]` and this record |

Every commit is signed (`git log -1 --format='%h %G?'` -> `G`). The prescribed
slice order (generator, then indexing/query, then plans/machine-output, then
failures, then README, then the record) was followed as an authoring order,
but the first four slices could not be separate green commits: the entry
document's reference index, its links and the index-closure check make the
split an all-or-nothing tree. That is a declared deviation from the commit
slicing in the brief, not a held-back tree: the authoring was committed as
soon as it built, per the loop guard.

## What was built

`SKILL-CLI.md` is the entry document of ADR-0009 section 1 and carries
nothing else: the decision tree (15 question shapes), the generated command
roster, the ADR-0003 section 5 exit table with the `ErrorType` rule, the
staleness rule in three sentences, the read-only guarantee in one sentence,
and the reference index with one trigger per reference. Per-command flags,
examples, the plan workflow in full, provider behaviour and the MCP parity
table all left it.

`skill/references/` holds the five files ADR-0009 section 2 names, each
opening with the records that govern it.

`scripts/generate_skill_docs.py` is the only writer of a generated region
(marker-delimited): the entry document's roster, each reference's
per-command sections (name, arguments, flags with defaults, read-or-write
class, JSON mode), and the JSON roster in `machine-output.md`. All of them
come from the typer registry the CLI builds its commands from, plus
`command_classes`, so a class cannot drift from ADR-0003 section 1. It offers
`--check` for drift. Prose around the blocks is written by hand.

## Budgets, as measured

Entry document (`SKILL-CLI.md`), budget 120 lines / 8 KiB:

| lines | bytes | budget |
|---|---|---|
| 118 | 6,506 | 120 / 8,192 |

References, budget 400 lines / 24 KiB, and the commands each one covers with
generated sections:

| Reference | lines | bytes | commands covered |
|---|---|---|---|
| `indexing.md` | 287 | 12,911 | 11 |
| `query.md` | 309 | 14,563 | 16 |
| `plans.md` | 204 | 10,595 | 6 |
| `machine-output.md` | 141 | 6,224 | 0 command sections; the 33-row JSON-mode roster (35 lines, 1,275 bytes) |
| `failures.md` | 108 | 6,404 | 0 command sections; the remedy tables per exit code |

Coverage adds up to the registry exactly: 11 + 16 + 6 = 33, and each of the
33 appears in exactly one reference. The whole surface is 1,167 lines and
57,203 bytes, against the 1,090 lines / 69,800 bytes of the single file it
replaces; the entry document alone is 6,506 bytes against 39,797.

## The drift checks, as run

`uv run pytest tests/test_docs.py -q` -> `34 passed, 1 skipped`.

| Check | Result |
|---|---|
| 1 roster equality with the registry, classes included | passes: 33 names, all three class spellings (`read`, `write`, `write (with --apply)`) agree with `command_classes` |
| 2 index closure | passes: every named reference exists, every file under `skill/references/` is named, every command is covered by exactly one reference |
| 3 budgets | passes at the sizes above; the failure message prints measured lines and bytes against the budget |
| 4 taxonomy coverage | passes: codes 0-5 in the entry table and in `failures.md` |
| 5 generated blocks equal the generator | passes: five regions (roster, JSON roster, and three command blocks) equal `blocks()`; a hand edit inside one fails here |
| 6 installed-copy digest | **skips** in this environment, reason printed: the installed skill directory is not in the new shape (a directory holding `SKILL.md` and `references/`) -- a legacy single-file copy cannot carry the references the entry document's triggers resolve to. That is the reading the brief asks to state: a legacy single-file installed copy is "not installed in this shape" |

The fenced-block parser and the three tests keyed on it are gone from the
module in the same commit that rendered the roster, so the gate cannot fail
for a parser the record did not anticipate. Two checks took their place and
are not weaker: every `--flag` mentioned anywhere in the agent surface must
exist on a registered command (keeper's own `--expected-hash`,
`--expected-content`, `--task-id` and click's `--help` are the declared
external set), and the global-flags section is still compared with the real
`--fresh` set.

Check 6 against a throwaway skills root: the entry document was copied to
`<skills-root>/code-indexer/SKILL.md` with `skill/references/` beside it under
the runner's temporary directory, and the suite was pointed at it with
`CODE_INDEXER_SKILLS_ROOT`. The three installed-copy tests passed with no
skip -- equal digest on that exact copy, unequal after a one-byte tamper, and
the legacy-shape skip case. The real skills root was not touched.

## Gates at the tip

```
uv run pytest tests/ -q -W error        -> 884 passed, 1 skipped, 2 deselected (base: 869 passed, 2 deselected)
uvx ruff check src tests scripts        -> All checks passed!
uv run python scripts/publishing_hygiene_lint.py -> violations=0
uv run python scripts/generate_skill_docs.py --check -> exit 0 (no drift)
```

The suite gained 15 tests (the six checks, their independence from the
generator, and the flag/installed-copy helpers) and one skip (check 6 in this
environment).

## No behaviour change

The diff of `b918fe1..tip` outside `SKILL-CLI.md`, `skill/references/`,
`README.md`, `tests/test_docs.py`, `scripts/generate_skill_docs.py` and
`runs/` contains exactly one path: `CHANGELOG.md`, which the leg's own slice
six requires (the entry under `## [Unreleased]`). Outside that set too, the
diff is **empty**:

```
git diff b918fe1 --stat -- . ':(exclude)SKILL-CLI.md' ':(exclude)skill/references' \
  ':(exclude)README.md' ':(exclude)tests/test_docs.py' \
  ':(exclude)scripts/generate_skill_docs.py' ':(exclude)runs' ':(exclude)CHANGELOG.md'
  -> (no output)
git diff b918fe1 --stat -- . ':(exclude)SKILL-CLI.md' ':(exclude)skill/references' \
  ':(exclude)README.md' ':(exclude)tests/test_docs.py' \
  ':(exclude)scripts/generate_skill_docs.py' ':(exclude)runs'
  -> CHANGELOG.md | 32 +
```

No command was added or removed (the registry is still 33 names), no CLI flag
moved, no exit code changed, and no test was weakened. The registry-equality
check is what proves it: the roster is generated from the same registry the
CLI builds its commands from, and it equals it.

## What is not done here

- The installed copy is not refreshed. That is the release step's job, run
after this round's gate; this leg deliberately did not install or edit the
household skill directory.
- No provider work and no `--json` work: those were leg 1's, and this leg
touches neither.
