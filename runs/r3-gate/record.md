# Gate record — one flag and one argument shape across the read commands

Base `105484f`, tip `9c29da3` (`origin/main`). Legs `ddf5521`, `feed305`,
`c5e5570`, `c316468`, `9c29da3`. Every number below was produced by a command
this record ran; the leg's own tables were re-run, not accepted. Captures sit
beside this file, masked.

## Verdict

**CHANGES REQUESTED.** One bar does not hold: the declared conflict rule
refuses a `semantic-search` invocation that succeeded at base, which is the
opposite of the round's `purely additive` bar. The fix is a narrowing, not a
design change. Every other bar passes, including the regression table, the
compact shapes, the argument shape, the mutation checks and all standing bars.

| bar | verdict |
|---|---|
| 1 `--version` | PASS |
| 2 `--format` parity, exhaustively | PASS on the 20 read-surface commands; 8 write commands still refuse `--format` (finding 2) |
| 3 `--format compact`, per command | PASS (labels are normalized spellings: finding 3) |
| 4 the conflict rule | FAIL — ruling **NARROW REQUIRED** (finding 1) |
| 5 the argument shape | PASS |
| 6 the regression table, ten invocations | PASS, 10 of 10 |
| 7 docs | PASS |
| 8 the tests can fail | PASS, six mutations, six named reds |
| 9 standing bars | PASS |
| 10 scope fence and the two deviations | PASS, both deviations harmless |

## Bar 1 — `--version`

`code-indexer --version` at the tip prints one line and exits 0:

    code-indexer 0.1.0
    exit=0

Measured version of the installed distribution, by import, not read from the
code: `importlib.metadata.version('code-indexer')` = `0.1.0`, distribution at
`<home>/.venv/lib/python3.12/site-packages/code_indexer-0.1.0.dist-info`. The
printed string equals it.

At the base tree the same invocation is `exit=2`, `No such option: --version`.

All 33 subcommand `--help` outputs run at both trees and exit 0; none carries
a failure marker. A random sample of three, drawn with `shuf` from the 33:
**plan-validate, file-outline, sweep-orphans**. All three differ from base and
the whole difference is the added `--format` option block (sweep-orphans also
re-wraps its existing option lines because the option column widened; nothing
removed, renamed or moved). `runs/r3-gate/help-diff.txt`.

The bar asks for a sample "unchanged from the base". That cannot hold for any
command that gained the flag — 20 of 33 helps change, all additively, and the
13 that did not gain it (`add-project`, `index-more`, the four plan writers,
`reindex-project`, `remove-project`, `watch`, `unwatch`, `deps`, `eval`,
`eval-compare`) are byte-identical. The measurement is recorded as measured.

## Bar 2 — `--format` parity, exhaustively

Method: the `--help` text of all 33 commands at both trees, because the typer
app surfaces its options through click Parameters and the help text is what a
caller reads. Count: **28 commands accept `--json`** at the tip — 19 read
commands, `sweep-orphans` (read shape) and 8 write commands. `at base
--format` existed on 2 (`deps` with its own `tree|edges|json`, `semantic-search`);
at the tip it exists on 21 (the 20 below plus `deps`).

All 20 read-surface commands, run with `--json`, with `--format json`, with
`--format text` and with no flag, on one indexed project at one moment:

    [overview] json_vs_fjson=(same/same) text_vs_ftext=(same/same) exits 0/0/0/0
    ... 20 rows, every one `(same/same)` on stdout and on stderr, equal exits

`runs/r3-gate/parity-format-flag.txt` carries all 20 rows. Three rows are
measured on a refusal path rather than a payload and are named as such there:
`plan-show` and `plan-validate` (no plan file in the sandbox → identical
`NotFoundError`, exit 1) and `semantic-search` (the sandbox index is
`vectors=off` and no embedding service is reachable here → identical
`ConfigError`, exit 4). `doctor` exits 1 for its own environment reason; all
four spellings agree byte for byte.

The 8 write commands that accept `--json` do **not** accept `--format`:
`add-project X --format json` → exit 2, `No such option: --format` (same for
`reindex-project`; the other six are `index-more`, `plan-apply`,
`plan-delete-declaration`, `plan-insert-symbol`, `plan-replace-body`,
`remove-project`). The card and the changelog scope this to reads, so the
round is honest; the bar's literal wording ("every command that accepts
`--json` today") is what those 8 rows miss. See finding 2.

## Bar 3 — `--format compact`, per command

Six named commands, one line per record, first line as documented:

| command | lines | JSON records | first line |
|---|---|---|---|
| overview | 8 | 1 object, 9 record tags | `project proj files=4 loc=18` |
| index-status | 1 | 1 | the text line itself |
| skeleton | 7 | 7 symbols | `dup_a.py:1-2 step (function)` |
| outline | 4 | 4 declarations | `mod.py:1-3 helper (function)` |
| find-symbol | 1 | 1 | `mod.py:1-3 helper (function)` |
| find-definition | 1 | 1 | `mod.py:1-3 helper (function)` |

Field names diffed against the default text rendering, mechanically, not by
eye: every compact token is either a value or a label the text form already
prints, except the label spellings `directory` (text: `directories:`),
`hotspot-fan-in` / `hotspot-size` (text: `hotspots (fan-in):` /
`hotspots (size):`), `fan_in`, `share` (text prints `(100.0%)`) and `loc`
(text: `LOC`). Same field, different spelling; no field is new. On
`skeleton`/`outline`/`find-symbol`/`find-definition` the only absent tokens are
the composite record shape (`file:1-3`, `(function)`), which the text form
prints as `function helper:1-3`. `runs/r3-gate/compact-shapes.txt`.

Refused where there is no honest compact form — tested on three commands:

    find-references --format compact   exit=2  ArgumentError: find-references has no compact form; supported formats: text|json
    get-code-context --format compact  exit=2  ArgumentError: get-code-context has no compact form; supported formats: text|json
    changed-symbols --format compact   exit=2  ArgumentError: changed-symbols has no compact form; supported formats: text|json

stdout is empty in all three.

## Bar 4 — the conflict rule (ruling of record)

Measured through the CLI seam the suite uses (typer's `CliRunner`) with the
suite's stub embedder and store, because the shared sandbox index is
`vectors=off` and a CLI-level `semantic-search` refuses with `ConfigError`
(exit 4) at base before any format resolution — that refusal cannot answer the
question. Base tree and tip tree, same project, six spellings:

| argv | base exit | base stdout | tip exit |
|---|---|---|---|
| `--json` | 0 | JSON | 0 |
| `--json --format json` | **0** | JSON | **2** |
| `--json --format text` | **0** | JSON (`--json` won) | **2** |
| `--format json` | 0 | JSON | 0 |
| `--format text` | 0 | text | 0 |
| (none) | 0 | text | 0 |

**(a) Did any invocation that succeeded at base now exit non-zero?** Yes, two:
`semantic-search Q --json --format json` (base exit 0, JSON; tip exit 2) and
`semantic-search Q --json --format text` (base exit 0, JSON, because `--json`
won; tip exit 2). Both are named, both are in
`runs/r3-gate/conflict-rule.txt`.

**(b) Is the rule honest for this round?** **NARROW REQUIRED.** Against the
card's bar, no: an invocation that worked keeps failing. The first of the two
is the decisive reproducer — `--json --format json` asks for the same answer
twice, base answered it (exit 0, identical bytes either way), and the round
now refuses it while the answer was never in doubt. The rule should allow the
pair when the two spellings agree (`--json` with `--format json`, and `--json`
with `--format text` may still be refused as a genuine contradiction), which
keeps the round additive and still removes the silent precedence. That is a
routing decision for the next round, not a repair in this record.

## Bar 5 — the argument shape

(a) unique name: `get-code-context --symbol helper --project P` → exit 0 and
the body `--- mod.py:1-3 ---` with the three source lines; byte-identical to
`get-code-context mod.py --symbol helper`.

(b) ambiguous name (a real duplicate `step` in two modules): exit 2, stdout
empty, and the message lists **2 candidates** — `dup_a.py:1-2` and
`dup_b.py:1-2`, each with kind `function`, `container=None` and its
`decl_hash` — against a `find-symbol step --json` answer whose `match_count` is
2. Every candidate, as a number, not as words. The `--json` twin returns
the same two with file, range, kind, container, decl_hash, signature, language.

(c) FILE positional, three invocations, base vs tip: `mod.py --start-line 1
--end-line 3`, `mod.py --symbol helper`, `mod.py` — stdout identical, stderr
identical, equal exits (0, 0, 1 for both trees; the last refuses for its own
reason identically).

No positional moved or was renamed: usage lines are `{file}` → `[file]` for
`get-code-context` and `{name}` → `[name]` for `find-definition` (required
relaxed to optional so `--symbol` alone is legal), `find-symbol` unchanged;
the names `file` and `name` are unchanged. `runs/r3-gate/argument-shape.txt`.

## Bar 6 — the regression table

All ten invocations re-run in a worktree at `105484f` and at the tip, same
project, same index, one interpreter, one moment; stdout and stderr compared
with `cmp`, exits read from the run. **10 of 10 rows reproduce: 0 B stdout,
0 B stderr, equal exits.** No row fails.
`runs/r3-gate/ten-invocations.txt`.

## Bar 7 — docs

- `SKILL-CLI.md` carries one flag table (three rows: `--version`,
  `--format text|json`, `--format compact`) and one argument-shape paragraph
  in the same section, `## Flags, and one argument shape for the symbol reads`.
- The references that own the changed commands were updated in the same
  commit: `machine-output.md` (+28 lines), `query.md` (+59), `indexing.md`,
  `plans.md`, `semantic.md`, `failures.md` (one line each where the flag list
  or the shape is restated).
- `uv run python scripts/generate_skill_docs.py --check` → exit 0.
- `CHANGELOG.md` carries the round under `## [Unreleased] — one flag, one
  argument shape across the reads`.
- No `docs/adr/` file changed (`git diff --name-status 105484f..9c29da3`
  contains none).
- The entry document's budget still holds: **120 lines, 7645 bytes**
  (budget 120 lines / 8 KiB).

## Bar 8 — the tests can fail

One throwaway worktree at the tip; baseline 45 passed; one mutation at a time,
the whole new module run, then `git checkout` to revert. Exactly the named
test went red each time:

| mutation | red test id | result |
|---|---|---|
| version prints a fixed 9.9.9 | `test_version_prints_the_installed_version_on_one_line` | 1 failed, 44 passed |
| `--format json` echoes an extra object on overview | `test_format_json_equals_the_json_flag[overview]` | 1 failed, 44 passed |
| overview project record emits a second line | `test_overview_compact_is_one_tagged_line_per_record` | 1 failed, 44 passed |
| find-references compact falls back to text | `test_compact_is_refused_where_there_is_no_compact_form[find-references]` | 1 failed, 44 passed |
| `--format` silently wins with both flags | `test_json_and_format_together_are_a_usage_error[overview]` and `[find-symbol]` | 2 failed, 43 passed |
| ambiguous symbol returns the first row | `test_symbol_only_context_refuses_an_ambiguous_name_with_candidates` and `test_ambiguous_symbol_only_context_answers_json_with_the_candidates` | 2 failed, 43 passed |

The last two rows are one parametrised test and one test plus its JSON twin;
no unrelated test fell. `runs/r3-gate/mutations.txt`.

## Bar 9 — standing bars

    uv run pytest tests/ -q -W error        1049 passed, 2 deselected, exit 0 (155 s)
    uvx ruff check src tests scripts        All checks passed!
    uv run python scripts/publishing_hygiene_lint.py
                                            scanned=1229 skipped=0 violations=0
    git log --format='%h %G?' 105484f..9c29da3   five commits, all `G`
    git status --porcelain --untracked-files=no  empty
    git ls-remote origin refs/heads/main    9c29da351c1e554b5bb635e7db00eab05646b83c
    gh run list: ci / 9c29da3 / event=push / completed / success (run 42369499551)

This record does not claim warn-clean: the local `-W error` run is green, and
the five `watcher.py` fork warnings under CI's own command are outside this
round's scope.

## Bar 10 — scope fence and the two deviations

Diff stat `105484f..9c29da3`, paths touched: `SKILL-CLI.md`, `CHANGELOG.md`,
`src/code_indexer/cli.py` (+295/-17), `src/code_indexer/command_classes.py`
(+15), `tests/test_docs.py`, `tests/test_output_format.py` (new, 459 lines),
`skill/references/*.md` (6 files), `runs/r3-flags*` (the round's own record and
captures). Nothing else. No `docs/json-schema.md` change (none was needed), no
`src/code_indexer/core.py` change, no manifest schema change and no
`EXTRACTION_VERSION` change: `SCHEMA_VERSION = "7"` and `EXTRACTION_VERSION = 5`
are outside the diff, and no reindex is implied by any of it.

**(a) the installed skill copy.** The leg synced the household skill copy
(`<agent-home>/skills/code-indexer`) itself, which the memo makes the
orchestrator's post-close step. Measured: the
digest test `tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`
→ 1 passed, 0 skipped; the installed `SKILL.md` and the tracked `SKILL-CLI.md`
have the same sha256 (`854629b5…2522b`), and all six installed references are
byte-identical to `skill/references/`. **Ruling: a harmless repair of the
expected-red local test, not a deviation to record against the round** — the
repository is untouched by it, and without it the suite would be red locally
while green in CI. Worth noting in the close-out so the step is not run twice.

**(b) the leftovers.** `docs/qa-verification-2026-09-23-*.md`, `runs/T-*` and
the other pre-existing untracked captures are still untracked and were not
swept into any commit in the range (`git log --name-only 105484f..9c29da3`
names none of them); their mtimes predate the round. **Ruling: untouched, as
the memo wanted.**

## Findings

1. **medium, blocking for the card's bar** — the conflict rule refuses an
   invocation that succeeded at base. Reproducer: `semantic-search Q --json
   --format json` → base exit 0, tip exit 2 (`runs/r3-gate/conflict-rule.txt`).
   Ruling: NARROW REQUIRED (see bar 4).
2. **low** — 8 write commands accept `--json` and refuse `--format`; the
   round's docs say "every read command that takes `--json`", which is
   accurate, but the bar's wording covers all 28 holders. Reproducer:
   `add-project X --format json` → exit 2, `No such option: --format`.
   No code change implied unless the wider reading is wanted.
3. **low** — compact labels are normalized spellings, not the text form's own
   labels (`loc`/`LOC`, `directory`/`directories`, `hotspot-fan-in`/
   `hotspots (fan-in):`, `share`/`(100.0%)`). No new field, but a reader must
   translate. Reproducer: `runs/r3-gate/compact-shapes.txt`, the field-name
   section.
4. **low** — the new suite pins `--format json` parity on 3 commands and text
   parity on 4, so a regression on the other 17 would stay green. This gate
   measured all 20; the tests do not. Suggest parameterising the parity case
   over the whole `--format` roster in a later round.
5. **info** — the two positionals `get-code-context {file}` and
   `find-definition {name}` were relaxed from required to optional. No-argument
   invocations exit 2 at both trees, with a different message. Additive.
6. **info** — the brief located the entry document at `skill/SKILL-CLI.md` and
   allowed a `core.py` and `docs/json-schema.md` change; the entry document
   actually lives at the repository root as `SKILL-CLI.md` (installed as
   `SKILL.md`), and neither `core.py` nor the schema page was touched. The
   scope fence holds as measured.

## What I did not verify

- **`semantic-search --format compact`.** The environment has no embedding
  service (local ports 11434 and 6333 are closed) and the sandbox index is
  `vectors=off`; the command refuses before rendering. Its documented compact
  shape is untested here, and the bar's six named commands do not include it.
- **A real `plan-show` / `plan-validate` payload.** No plan file exists in the
  sandbox; parity was measured on their identical refusal path only.
- **A `changed-symbols` payload on a non-git project.** Measured on a git
  project (exit 0, parity holds) and on the identical `InternalError` path in
  the first pass; the payload itself was not inspected field by field.
- **No reindex claim.** Not verified directly; inferred from an unchanged
  manifest schema version, an unchanged extraction version and a `src/` diff
  that touches neither.
- **CI internals.** The run for `9c29da3` is green; I did not read its log.
