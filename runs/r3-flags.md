# One flag and argument shape across the read commands

Base tip: `105484f` (signed). Purely additive: one new top-level flag, one
format flag on the read commands with `--json` kept as its exact alias, and
one documented argument shape for the three symbol reads.

## Commits

| commit | content |
|---|---|
| `ddf5521` | the top-level `--version` |
| `feed305` | `--format` parity and compact forms, the symbol argument shape, the new tests, the regenerated command blocks |
| `c5e5570` | the flag table, the argument-shape section, the reference detail and the changelog entry |

## What changed

1. `code-indexer --version` prints `code-indexer <version>` on one line from
the installed package metadata and exits 0. The option is eager on the app
callback, so it needs no command and no project; no subcommand gained or lost
an option.
2. `--format text|json` is accepted by every read command that took `--json`
(`doctor`, `overview`, `changed-symbols`, `find-callers`, `find-callees`,
`list-projects`, `lookup-project`, `semantic-search`, `index-status`,
`sweep-orphans` in its read shape, `skeleton`, `outline`, `find-symbol`,
`find-definition`, `get-code-context`, `find-references`, `plan-validate`,
`plan-show`). `--json` remains an EXACT alias: both spellings render the same
bytes, which the parity tests compare directly.
3. `--format compact` renders one line per record from fields the text form
already prints, on `overview`, `index-status`, `skeleton`, `outline`,
`find-symbol`, `find-definition` and `semantic-search`. `index-status` was
already one line, so its compact form is that line. On every other read,
`compact` is REFUSED with `ArgumentError`, exit 2, naming the formats that
command does take; nothing falls back silently.
4. One conflict rule, tested: `--json` together with `--format` is a usage
error (exit 2, `--json and --format both given`), never a silent precedence.
This is the one behaviour change in the round: `semantic-search --json
--format X` used to let `--json` win and now refuses. It is documented in the
entry document, `machine-output.md` and the changelog.
5. `get-code-context --symbol <name>` with no FILE resolves the name
project-wide through the manifest: one definition answers that file's span,
several are refused (`AmbiguousSymbolError`, an `ArgumentError`, exit 2) with
every candidate listed, none is `NotFoundError`, exit 1. The FILE positional
keeps its meaning, and `find-symbol` / `find-definition` accept `--symbol` as
the alias of their positional NAME. `deps --format` (`tree|edges|json`) is a
different flag and is untouched.

No manifest schema change, no `EXTRACTION_VERSION` change, no reindex.

## The flag, in one table

The table the entry document now carries:

| Flag | Where | Meaning |
|---|---|---|
| `--version` | top level | the installed package version, one line, exit 0 |
| `--format text\|json` | every read command that took `--json` | `--json` is the exact alias; both together is exit 2 |
| `--format compact` | `overview`, `index-status`, `skeleton`, `outline`, `find-symbol`, `find-definition`, `semantic-search` | one line per record of fields the text form prints; refused (exit 2) elsewhere, naming text\|json |

Compact shapes, as the tests pin them:

| Command | One compact line |
|---|---|
| `overview` | `<tag> <fields>` for `project`, `language`, `directory`, `entry-point`, `tests`, `test-file`, `config`, `hotspot-fan-in`, `hotspot-size` |
| `index-status` | the text form's own single `key=value` line, unchanged |
| `skeleton` | `file:start-end name (kind)`; `--tree` records a directory |
| `outline` | `file:start-end name (kind)` |
| `find-symbol` | `file:start-end name (kind)`, one per row |
| `find-definition` | `file:start-end name (kind)` |
| `semantic-search` | `path:start-end  symbol  score` (unchanged) |

## The argument shape

Documented once in the entry document and expanded in `query.md`:

- `get-code-context --symbol <name> --project <p>` -- no FILE, resolved
  project-wide; ambiguous names list every candidate and exit 2.
- `get-code-context <file> --symbol <name>` -- the FILE positional keeps its
  meaning and scopes the symbol to a range inside that file.
- `find-symbol <name>` / `find-definition <name>` -- the positional stays;
  `--symbol <name>` is its alias, and two disagreeing spellings are refused.

## The ten invocations, base vs tip

`runs/r3-flags/capture-invocations.sh` builds one small project, indexes it
once, and runs the same ten working invocations with the base tree's `src` and
the tip tree's `src` through the same interpreter, moment by moment. Base side:
`runs/r3-flags/base-invocations.txt`; both sides and the diffs:
`runs/r3-flags/tip-invocations.txt` and `runs/r3-flags/captures/`.

| # | Invocation | base exit | tip exit | stdout diff | stderr diff |
|---|---|---|---|---|---|
| 1 | `overview --project P` | 0 | 0 | 0 B | 0 B |
| 2 | `overview --project P --json` | 0 | 0 | 0 B | 0 B |
| 3 | `index-status --project P` | 0 | 0 | 0 B | 0 B |
| 4 | `index-status --project P --json` | 0 | 0 | 0 B | 0 B |
| 5 | `skeleton --project P` | 0 | 0 | 0 B | 0 B |
| 6 | `outline mod.py --project P` | 0 | 0 | 0 B | 0 B |
| 7 | `find-symbol helper --project P` | 0 | 0 | 0 B | 0 B |
| 8 | `find-definition helper --project P --json` | 0 | 0 | 0 B | 0 B |
| 9 | `get-code-context mod.py --project P --symbol helper` | 0 | 0 | 0 B | 0 B |
| 10 | `find-references helper --project P --json` | 0 | 0 | 0 B | 0 B |

Ten of ten byte-identical on stdout and stderr with equal exit codes: the new
fields are additive only where a caller asks for a new spelling. The same ten
invocations are pinned in `tests/test_output_format.py` as parameterised
cases, so a later change that alters one of these answers fails the suite.

## Tests added

`tests/test_output_format.py` (45 cases): `--version` (one line, the installed
version, no command needed); `--format json` equals `--json` for three
commands; `--format text` equals the default text for four; the compact shape
of every supporting command (including the `--tree` skeleton and the
`index-status` identity); the refused-format exit code and message; the
unknown-value refusal; the conflict rule on two commands; `compact` being a
subset of the read roster; symbol-only `get-code-context` resolving, the
ambiguous-name refusal (text and JSON, candidates included), the
neither-argument usage error and the FILE positional's unchanged meaning; the
`--symbol` alias on two commands and the disagreeing-spellings refusal; and
the ten-invocation regression table.

## Verification at the tip

| check | result | log |
|---|---|---|
| `uv run pytest tests/ -q -W error` | 1049 passed, 2 deselected, exit 0 (base 1004 + 45) | `runs/r3-flags/pytest-tip.log` |
| `uvx ruff check src tests scripts` | all checks passed | `runs/r3-flags/ruff.txt` |
| `uv run python scripts/publishing_hygiene_lint.py` | scanned=1135, 0 violations | `runs/r3-flags/hygiene.txt` |

This record does not claim warn-clean: the local `-W error` run is green,
while the five `watcher.py:343` fork warnings under CI's own command are
thread-timing dependent and unchanged by this round.

## Carried open

- The entry document is at its 120-line / 8 KiB budget: the flag table and
the argument-shape section fit only because three prose lines elsewhere in it
were tightened in the same edit. A next addition to that page needs a split,
not a bigger budget (ADR-0009 section 5).
- The installed skill copy at `<home>/.hermes/skills/code-indexer` was synced
to the new entry document and references; without that sync the local
document-digest test fails. The sync is the release step, not a repo change.
