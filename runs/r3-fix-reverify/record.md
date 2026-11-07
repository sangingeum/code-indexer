# Narrow re-verify — the narrowed conflict rule and the widened parity roster

Previous tip (the gate's record) `6430168`, fix tip `5d98f9d` (`origin/main`).
Fix commits `099c896`, `4257200`, `43fec69`, `3bff398`, `14b512d`, `5d98f9d`, all
signed `G`. Every number below came from a command this round ran; the
captures sit beside this file, masked (`<checkout>`, `<scratch>`, `<rig>`).

## Verdict

**PASS.** The regression the gate named is closed at the invocation it was
named on, the parity test really is derived from the registration, the compact
labels are documented without a single emitted label changing, and nothing
outside the round's declared scope moved.

| bar | verdict |
|---|---|
| 1 the regression is closed, six commands at the CLI seam | PASS |
| 2 the parity roster is the whole roster, and the derivation bites | PASS |
| 3 findings 3 and 4 answered, no label renamed | PASS |
| 4 nothing else moved | PASS |
| 5 standing bars at `5d98f9d` | PASS |

Carried open: nothing from this bar set. Two measurements are stated with
their volatility named (bar 3's `index-status` compact line, and the payload
timestamps), not smoothed over.

## Bar 1 — the regression is closed

Method: the suite's own seam (`typer.testing.CliRunner`, stub embedder and
stub store, one indexed project), the same rig the fix's tests use, run at
three trees in one interpreter each — the pre-round base `105484f`, the gate's
tip `6430168`, and the fix tip `5d98f9d`. The rig is pinned to one scratch path
so a payload comparison compares payload, not temp-dir names.
`runs/r3-fix-reverify/scripts/measure_flag_matrix.py`, captures
`captures/flag-matrix-{preround,gatebase,tip}.txt`.

Measured at `5d98f9d`, one row per command:

| command | `--json --format json` | `--json` alone | identical | `--json --format text` | `--json --format compact` | `--format json` alone | `--format text` alone | no flag |
|---|---|---|---|---|---|---|---|---|
| `overview` | 0, 656 B | 0, 656 B | yes, byte for byte | 2, stdout 0 B | 2, stdout 0 B | 0, 656 B | 0, 188 B | 0, 188 B |
| `find-symbol helper` | 0, 761 B | 0, 761 B | yes | 2, 0 B | 2, 0 B | 0, 761 B | 0, 152 B | 0, 152 B |
| `find-definition helper` | 0, 778 B | 0, 778 B | yes | 2, 0 B | 2, 0 B | 0, 778 B | 0, 122 B | 0, 122 B |
| `semantic-search helper` | 0, 1367 B | 0, 1367 B | yes | 2, 0 B | 2, 0 B | 0, 1367 B | 0, 365 B | 0, 365 B |
| `skeleton` | 0, 1806 B | 0, 1806 B | yes | 2, 0 B | 2, 0 B | 0, 1806 B | 0, 308 B | 0, 308 B |
| `outline mod.py` | 0, 903 B | 0, 903 B | yes | 2, 0 B | 2, 0 B | 0, 903 B | 0, 146 B | 0, 146 B |
| `index-status` | 0, 1043 B | 0, 1043 B | yes | 2, 0 B | 2, 0 B | 0, 1043 B | 0, 605 B | 0, 605 B |

`find-definition` is the seventh command measured; the six the bar names are
rows 1, 2, 4, 5, 6 and 7. Both disagree-pairs are **one** stderr line, the
stdout is empty, and the message names the disagreement (`captures/conflict-messages.txt`):

    ArgumentError: --json and --format text disagree: --json is an alias of --format json; pass one of them
    ArgumentError: --json and --format compact disagree: --json is an alias of --format json; pass one of them

`--json` alone, `--format json` alone, `--format text` alone, `--format
compact` alone and the no-flag invocation are unchanged from `6430168`: masked
0 B on all five spellings for all seven commands (35 of 35 rows `yes`),
`captures/unchanged-vs-gatebase.txt`; the seven remaining rows are the
`--json --format json` pair, which is the fix (0 B at `6430168`, the payload at
the tip). The mask covers two cells the payload cannot hold still across two
runs — the `last_indexed` timestamp and the throughput rate — and nothing else.

**The base-success → tip-failure pair is now base-success → tip-success.**
`semantic-search helper --json --format json`:

| tree | exit | stdout |
|---|---|---|
| `105484f` (pre-round base) | **0** | 1367 B, the JSON payload |
| `6430168` (the gate's tip) | **2** | 0 B, `--json and --format both given` |
| `5d98f9d` (the fix tip) | **0** | 1367 B, byte-identical to `--json` alone |

The other five commands did not accept `--format` at `105484f` at all (`exit
2`, `No such option`), so the base-success the gate named belongs to
`semantic-search` alone; all six it refused at `6430168` now answer.

**The `--format text` pin exists.** `3bff398` added
`test_plain_text_is_unchanged_by_the_flag_existing` (four ids: `overview`,
`find-symbol`, `find-definition`, `skeleton`), asserting
`explicit.stdout == plain.stdout` and equal exit 0 — `--format text` measured
against no flag, not against a second `--format text`.

## Bar 2 — the parity roster is the whole roster

(a) The parameterised ids and the CLI agree. `captures/roster.txt`,
`captures/help-count.txt`, `captures/help-flags.txt`:

    registered_commands=33
    read_roster=22
    derived_pair_roster=19
    read_commands_without_the_pair=['deps', 'eval', 'eval-compare']

`code-indexer --help` lists **31** commands in its panel; `map` and
`file-outline` are registered aliases the panel does not list (both answer
`--help` exit 0). Reading the flag blocks out of each command's own `--help`:
**18** panel-listed commands carry both `--json` and `--format`, plus the two
aliases = 20, minus `sweep-orphans` (it holds the pair but is not in
`READ_COMMANDS`) = **19** — the same set the parameterised test enumerates:

    changed-symbols, doctor, file-outline, find-callees, find-callers,
    find-definition, find-references, find-symbol, get-code-context,
    index-status, list-projects, lookup-project, map, outline, overview,
    plan-show, plan-validate, semantic-search, skeleton

(b) The derivation bites. In a throwaway worktree at `5d98f9d` I added a fake
read command (`zz-fake-read`, taking `--json` and `FormatOpt`) and put its name
in `READ_COMMANDS`; nothing else changed. Two tests went red
(`captures/fake-command-red.txt`):

    E  AssertionError: no parity argv for ['zz-fake-read']
       test_every_read_command_that_takes_the_flag_keeps_a_parity_case
    E  KeyError: 'zz-fake-read'
       test_format_json_equals_the_json_flag[zz-fake-read]
    2 failed, 62 deselected

The first red is the derivation itself: the roster is recomputed from the
typer registration at import, so a new holder cannot stay untested. The
worktree was removed by `git worktree remove --force` and the repository was
never touched by the exercise.

(c) No roster member is skipped for want of a payload. `plan-show`,
`plan-validate` and `semantic-search` are ids in the same parameterised test
(`test_format_json_equals_the_json_flag[plan-show]`, `[plan-validate]`,
`[semantic-search]`), and its assertion is parity of all three channels:

    assert by_format.stdout == by_flag.stdout
    assert by_format.stderr == by_flag.stderr
    assert by_format.exit_code == by_flag.exit_code

measured here on the refusal path they do have (`captures/refusal-paths.txt`),
each row `two spellings agree on stdout+stderr+exit = True`:

| command | `--json` | `--format json` |
|---|---|---|
| `plan-show` (no ref, the suite's argv) | 2, stdout 0 B | 2, stdout 0 B |
| `plan-show no-such-plan` | 1, stdout 120 B | 1, stdout 120 B |
| `plan-validate` (no ref) | 2, stdout 0 B | 2, stdout 0 B |
| `plan-validate no-such-plan` | 1, stdout 120 B | 1, stdout 120 B |
| `semantic-search helper` | 0, stdout 807 B | 0, stdout 807 B |

## Bar 3 — findings 3 and 4, and no label renamed

The compact spellings are stated in the flag table (`SKILL-CLI.md`, line 74):

    | `--format compact` | `overview`, `index-status`, `skeleton`, `outline`, `find-symbol`, `find-definition`, `semantic-search` | one line per record built from normalized field names (`loc`, `directory`, `share`, `hotspot-fan-in`), not the text form's own labels; refused elsewhere (exit 2) naming text\|json |

and mapped in `skill/references/machine-output.md` (lines 66–69):

    Compact labels are normalized field names, not the text form's own spellings:
    `loc` prints as `LOC`, `directory` as `directories:`, `share` as `(100.0%)`,
    `hotspot-fan-in`/`hotspot-size` as `hotspots (fan-in):`/`hotspots (size):`,
    and `fan_in` has no separate text label. Same field, one spelling per form;
    no emitted label changes.

The last sentence is measured, not taken on trust. Compact stdout at `6430168`
vs `5d98f9d` (`captures/compact-labels.txt`):

| command | bytes `6430168` | bytes `5d98f9d` | raw | masked |
|---|---|---|---|---|
| `overview` | 185 | 185 | 0 B | 0 B |
| `skeleton` | 175 | 175 | 0 B | 0 B |
| `outline` | 117 | 117 | 0 B | 0 B |
| `find-symbol` | 29 | 29 | 0 B | 0 B |
| `find-definition` | 29 | 29 | 0 B | 0 B |
| `index-status` | 604 | 605 | **differs** | 0 B |

The one raw difference is not a label: `index-status --format compact` prints
the pass throughput inside the same line, and the two runs measured 9436.0 vs
8920.89 chunks/s. With that one numeric cell masked the line is byte-identical,
and the text of the line (`project=… state=… rows=… schema=…`) is unchanged.
No emitted label was renamed.

## Bar 4 — nothing else moved

`git diff --name-status 6430168..5d98f9d` — 8 files, every one inside the
declared set:

    M  CHANGELOG.md
    M  SKILL-CLI.md
    M  skill/references/machine-output.md
    M  src/code_indexer/cli.py
    M  tests/test_output_format.py
    A  runs/r3-fix.md
    A  runs/r3-fix/combinations.txt
    A  runs/r3-fix/verification.txt

`317 insertions, 37 deletions`. No `docs/adr/` path. No schema file and no
`EXTRACTION_VERSION` change: neither `vectors.py`, `fingerprint.py` nor
`graph_extract.py` is in the diff, so no reindex is implied. No positional
moved: the whole `cli.py` diff is the conflict rule in `_output_format`, its
comment block and the `FormatOpt` help string — no `typer.Argument` line
appears in it.

`--format` is still absent from the eight write commands. Spot-check on the
two the bar names (`captures/write-commands-no-format.txt`):

    add-project x --format json        exit 2  No such option: --format
    reindex-project x --format json    exit 2  No such option: --format
    add-project x --format compact     exit 2  No such option: --format
    reindex-project x --format compact exit 2  No such option: --format

## Bar 5 — standing bars at `5d98f9d`

`captures/pytest.txt`, `captures/standing-bars.txt`:

    uv run pytest tests/ -q -W error          1067 passed, 2 deselected, exit 0 (115.24 s)
    uvx ruff check src tests scripts          All checks passed!
    uv run python scripts/publishing_hygiene_lint.py
                                              scanned=1241 skipped=0 violations=0
    uv run python scripts/generate_skill_docs.py --check   exit 0
    git log --format='%h %G?' 6430168..5d98f9d   six commits, all G
    git status --porcelain --untracked-files=no  0 lines
    git ls-remote origin refs/heads/main       5d98f9d223a9ddfa6768326d85f0e5f6e59448a7
    gh run list --commit 5d98f9d               ci / push / completed / success (run 42371298186)

This record does not claim warn-clean. The local `-W error` run is green; the
`watcher.py` fork warnings under CI's own command are outside this round, as
the gate said.

## Captures

`captures/flag-matrix-{preround,gatebase,tip}.txt` (56 rows each),
`captures/unchanged-vs-gatebase.txt`, `captures/unchanged-vs-preround.txt`,
`captures/conflict-messages.txt`, `captures/compact-labels.txt`,
`captures/refusal-paths.txt`, `captures/roster.txt`,
`captures/help-count.txt`, `captures/help-flags.txt`, `captures/cli-help.txt`,
`captures/fake-command-red.txt`, `captures/write-commands-no-format.txt`,
`captures/standing-bars.txt`, `captures/pytest.txt`.
The scripts that produced them are beside the capture tree in `scripts/`; each
takes the tree to measure as an argument, so they contain no host path.
The pristine bodies (252 raw stdout/stderr files) are kept outside the
repository in the local evidence store, not committed.

## Open

- Nothing carried from this bar set. The mapping of `index-status --format
  compact` to a byte-identical line needs the throughput cell masked; if a
  future round wants the compact line diffable without a mask, the rate belongs
  outside it — that is a documentation/design remark, not a defect found here.
- The gate's finding 2 (eight write commands refuse `--format`) remains as the
  round scoped it; not re-opened by this re-verify.
