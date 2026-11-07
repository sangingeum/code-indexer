# Fix round — the narrowed conflict rule and the wide parity test

Base `6430168` (`G`), tip as recorded at the end of this file. Every number
below was produced by a command this round ran; the captures sit beside this
record, masked (`<index-root>`, `<home>`).

## The rule, narrowed

`_output_format` accepts `--json` with `--format` only when the two spellings
agree. `--json --format json` asks for the same answer twice and is rendered
as `json`, byte-identical to `--json` alone. `--json` with any other format is
still one `ArgumentError` line naming the contradiction and exit 2:

    ArgumentError: --json and --format text disagree: --json is an alias of --format json; pass one of them

The silent precedence is still gone; the false conflict is. `FormatOpt`'s help
states the narrowed rule, and the flag table states it once.

## The four combinations, measured at the tip

Through the CLI seam the suite uses (`CliRunner`, stub embedder and store, one
indexed project), so `semantic-search` answers a payload instead of refusing
on this host's vectors-off sandbox. `runs/r3-fix/combinations.txt`.

| command | `--json` | `--json --format json` | `--json --format text` | `--json --format compact` | `--format json` | `--format text` | (none) |
|---|---|---|---|---|---|---|---|
| `overview` | 0, 656 B | 0, 656 B, identical | 2, stdout empty | 2, stdout empty | 0, identical | 0, 186 B, differs | 0, same as `--format text` |
| `find-symbol helper` | 0, 761 B | 0, 761 B, identical | 2, stdout empty | 2, stdout empty | 0, identical | 0, 149 B, differs | 0, same as `--format text` |
| `semantic-search helper` | 0, 1361 B | 0, 1361 B, identical | 2, stdout empty | 2, stdout empty | 0, identical | 0, 359 B, differs | 0, same as `--format text` |

Every JSON payload parses; every agree-pair is byte-identical to `--json`
alone; every disagree-pair is exit 2 with an empty stdout and one message
naming the contradiction. That is the base-decisive invocation
(`--json --format json`, exit 0 at base) answering again.

## The tests

- **Four combinations per command** — `overview`, `find-symbol`,
  `semantic-search` (through the stub rig): agree-pair exit 0 and identical to
  `--json`; `--format text` alone unchanged and different from JSON; `--json`
  alone unchanged; `--json --format text|compact` exit 2, stdout empty, message
  naming the contradiction. Unchanged is pinned per command: `--format text`
  equals the no-flag invocation byte for byte.
- **The mutation the gate ran still goes red.** `--format` silently winning
  (`return fmt` in place of the refusal) turned the three four-combination
  cases red — 3 failed — and the mutation was reverted by undo, not by a
  rewrite.
- **The parity test covers the whole read roster.** The roster derives from
  the typer registration intersected with `READ_COMMANDS` — the same sources
  the CLI uses — and a drift test refuses a roster member with no argv entry,
  so a read command that gains the flag pair without a parity case fails the
  suite. Count: **19 read commands** (aliases `map` and `file-outline`
  included). The three reads that do not take the pair stay outside it by
  construction: `deps` (its own `--format tree|edges|json`), `eval` and
  `eval-compare` (text only).
- **No command is skipped for want of a payload.** `plan-show`, `plan-validate`,
  `changed-symbols`, `lookup-project`, `doctor` and `semantic-search` are
  measured on their refusal/answer path as they come; parity is asserted on
  stdout, stderr and exit together.

## Documentation

- `SKILL-CLI.md` flag table: the conflict rule once (accepted when the pair
  agrees, exit 2 when it does not), and compact labels are normalized field
  names. Entry document unchanged in line count (120 lines, 7810 bytes of the
  8 KiB budget).
- `skill/references/machine-output.md`: the narrowed rule, plus the mapping
  `loc`/`LOC`, `directory`/`directories:`, `share`/`(100.0%)`,
  `hotspot-fan-in`/`hotspots (fan-in):`, `hotspot-size`/`hotspots (size):`.
  No emitted label changed — documentation only.
- `CHANGELOG.md` under `## [Unreleased]`: the entry now describes what the tip
  does, including that `--json --format json` keeps its answer.
- The read/write scope is unchanged: the eight write commands still do not
  take `--format` (finding 2 accepted as-is).

## Verification at the tip

`runs/r3-fix/verification.txt` carries all three commands in one capture:

    uv run pytest tests/ -q -W error        1067 passed, 2 deselected (base 6430168: 1049 passed, 2 deselected)
    uvx ruff check src tests scripts        All checks passed!
    uv run python scripts/publishing_hygiene_lint.py
                                            scanned=1241 skipped=0 violations=0

This record does not claim warn-clean: five `watcher.py:343` fork warnings under
CI's own command are outside this round.

## The installed skill copy

The digest test `tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`
went red after the docs edit, as expected for a local-only reason: the
installed copy at `<home>/skills/code-indexer` predated the change. It was
synced from the tracked copy (`SKILL.md` from `SKILL-CLI.md`, `references/`
from `skill/references/`), and the whole suite is green above with the digest
test passing. The repository was not touched by the sync.

## Commits

| commit | %G? | what |
|---|---|---|
| `099c896` | G | the narrowed rule and its four-combination tests |
| `4257200` | G | parity pinned on the whole read roster |
| `43fec69` | G | docs and changelog follow the code |
| `3bff398` | G | `--format text` pinned against the no-flag invocation |
| (this record) | | `runs/r3-fix.md` and its captures |

## Open

- Nothing carried open from this round. Gate findings 1, 3 and 4 are
  addressed; finding 2 remains accepted (reads only, by card, docs and
  changelog); finding 5 and 6 were informational.
