# Verification gate — publishing hygiene (issue #27)

Verification round, read-only on code. Date: 2026-10-21.
Branch `main`, tip `d851d52bb2d03f6b8d0cb766f25aee44051e1d81`, equal to
`origin/main` at the start of the gate; tree clean apart from local untracked
captures. Commits under verification, both `%G? = G`:

| Commit | Subject |
|---|---|
| `fc2af93` | docs(hygiene): remove internal names and card ids from published records |
| `d851d52` | chore(hygiene): enforce the publishing rules with a lint script and test |

Scope of the round: the eleven published records neutralised in `fc2af93`,
and the mechanical check added in `d851d52` (lint script, offline test,
pre-commit hook, brief and changelog updates). The tool's schema and behaviour
are out of scope and were confirmed untouched.

## Verdict

**PASS.** All ten bars hold on fresh command output run in this gate. Two
deviations the implementation leg flagged were ruled (see "Deviations"), with
one recommendation that is not a blocker for this round, and three minor
helper/lint observations recorded under "Findings" for the owning surface.

| Bar | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | the check is not vacuous: every forbidden name has a digest | PASS | `captures/bar1-name-digests.txt` |
| 2 | every class fires on real input, outside the repo | PASS | `captures/bar2-class-firing.txt` |
| 3 | no silent skips; counts real; untracked out of scope | PASS | `captures/bar3-skips-and-scope.txt` |
| 4 | the neutralisation kept the record | PASS | `captures/bar4-neutralisation-diff.txt`, `...-authored.txt` |
| 5 | both redaction helpers still substitute | PASS (one caveat on the second helper) | `captures/bar5-redaction-helpers.txt` |
| 6 | the hook is real and blocks | PASS | `captures/bar6-precommit-hook.txt` |
| 7 | an independent scan agrees with the rule | PASS | `captures/bar7-independent-scan.txt` |
| 8 | suite and linter | PASS | `captures/bar8-suite.txt`, `captures/bar8-ruff.txt` |
| 9 | the docs describe what the code does | PASS | `captures/bar9-docs-and-help.txt` |
| 10 | the two deviations, ruled | PASS with one recommendation | this record, §Deviations |

Note on the issue itself: #27 carries **one** comment, not the two the brief
expected. That comment is the corrective one — full-scan inventory, the
surface split by file type, the digest design, the offline test plus hook. No
second comment exists as of this gate (`comments` length = 1); the design
reasoning the check implements is all present in that single comment, and the
check matches it.

Dev self-report, reproduced rather than trusted:

| Claim | Observed |
|---|---|
| suite `519 passed, 2 deselected`, `-W error` | `519 passed, 2 deselected in 73.82s`, exit 0, no warnings |
| ruff clean | `All checks passed!`, exit 0 |
| lint `scanned=266 skipped=0 violations=0` | identical |

## Bar 1 — the name set is not vacuous

Digests were recomputed from the roster, not read from the brief:

```
$ uv run python <workdir>/digest_check.py
committed digests: 11
candidates matching a digest : 11
digests explained            : 11 / 11
dead digests (no name)       : 0
```

All eleven digests resolve to a real name: the nine roster roles, the
orchestrator role, and the local build account. No digest is unexplained
(a digest that matched nothing would be a dead entry), and no name the project
must forbid is missing from the set. The three extra candidates tried
(the default profile label, the framework name, the forge login) correctly
match nothing — they are not roster roles, so they are not in scope.

A digest match alone is only half the proof; bar 2 shows the matching path
actually fires.

## Bar 2 — every class fires, in a throwaway tree outside the repository

Probes were written under a temporary scan root outside the checkout and fed
to the shipped script with `--root` + `--paths`. Inputs are shown with the
account segment replaced in the published capture; the live probe used a
plausible fictional account (plain and dotted forms), a real-shaped Windows
user root, and a network share URL.

| Probe | Class expected | exit | Observed |
|---|---|---|---|
| `/home/<account>/projects/app/src/main.py` in `.md` | absolute-host-path | 1 | `posix-plain.md:1: absolute-host-path: /home/<account>/` |
| `/home/<dotted-account>/checkout/src/main.py` | absolute-host-path, not swallowed by the placeholder exemption | 1 | `posix-dotted.md:1: absolute-host-path: /home/<dotted-account>/` |
| `<windows-user-root>\AppData\Local\tmp\out.txt` | absolute-host-path | 1 | `windows.md:1: absolute-host-path: <windows-user-root>\` |
| `<smb-share>/dump.tar` | absolute-host-path | 1 | `share.md:1: absolute-host-path: <smb-share>` |
| internal role name in `.md` | internal-profile-name | 1 | `name.md:1: internal-profile-name: forbidden internal name at column 14` |
| card id in `.md` | board-card-id | 1 | `card.md:1: board-card-id: <card-id>` |
| clean line | none | 0 | `scanned=1 skipped=0 violations=0` |
| `runs/capture.txt` with a name line and a home-path line | path only | 1 | `runs/capture.txt:2: absolute-host-path: /home/<account>/` (line 1, the name, not reported) |

The dotted account confirms the exemption is keyed on the synthetic list and on
metacharacters, not on the shape of an account name — a real dotted account is
caught. The report names the class and never echoes the forbidden name itself.

## Bar 3 — skips are counted, and the default scope is what the brief says

```
$ ... --root <scan-root> --paths blob.md huge.md clean.md
publishing hygiene: scanned=1 skipped=2 violations=0 (untracked files are out of scope)
exit=0
```

A file carrying a documented violation but a NUL byte, and a 2 MB + 10 byte
file, are both counted as skipped; the one readable file is counted as scanned.
The summary line always carries both counts, so neither skip can pass silently.

Default scope, with an untracked violating file placed inside the repository:

```
$ git status --short runs/vera27-untracked-probe.md     ->  ?? (untracked)
$ uv run python scripts/publishing_hygiene_lint.py
publishing hygiene: scanned=266 skipped=0 violations=0 (untracked files are out of scope)
exit=0
$ ... --paths runs/vera27-untracked-probe.md
runs/vera27-untracked-probe.md:1: absolute-host-path: /home/<account>/
runs/vera27-untracked-probe.md:2: board-card-id: <card-id>
exit=1
```

The untracked file is out of the default scan exactly as documented, and the
same file fails when named explicitly — so the omission is scope, not a broken
detector. The probe file was removed afterwards.

## Bar 4 — the neutralisation removed identifiers, not record

`git show fc2af93` was read in full: 11 files, 30 insertions, 25 deletions.
Every changed line is an identifier substitute or an attribution reword; no
technical statement, table row, command, count or verdict line was dropped.

Authored surfaces:

- `docs/subcommand-design.md` — the header's author and target role became
  "written in a design round" and "target stack: Python/uv"; section 4's
  heading became "Implementation notes for the Python implementation". The
  superseded note (schema v6 replaced the two reference tables with `refs`) is
  intact, all 15 headings are in place and in order, and the body is unchanged.
- three test docstrings — the attribution became "review round" and "verified
  against a real repository". Bodies and assertions untouched (the diff touches
  docstring lines only; the suite passes).

Round records: seven files, each losing a card id and its dispatching role,
with the surrounding sentence kept. The two records the brief calls out still
say what they said:

- `runs/issue22/fix-verification.md` — "The board card (narrow re-verification
  of the issue #22 blocker fix) was read read-only: status `in_progress`,
  claimed by the orchestrator, its dependency satisfied. No board write was
  made by this leg." The status, the read-only claim and the no-write claim
  survive; only the card id and the dependency's card id are gone.
- `runs/issue22/review-fix-round.md` — "Dispatch evidence is of record and was
  not re-run except where a claim was verifiable in one live read." The
  sentence keeps its meaning: evidence carried by the dispatch, not re-run.

The one attribution that changes wording rather than dropping a token is
`claimed_by: <role>` → "claimed by the dispatching role"; it states the same
fact without the profile name. No line lost technical content, and capture
payloads were not rewritten (bar 7).

Ruling: the neutralisation kept the record. Nothing here is a silent edit.

## Bar 5 — the redaction helpers still work

Both helpers were copied outside the checkout and exercised on synthetic
captures, so the run-time-derived prefixes resolved against the copy:

```
$ python3 <helper-copy>/runs/issue22/sanitize_evidence.py
1 repo src      : <repo>/src/code_indexer/cli.py
2 repo plain    : <repo>/docs/README.md
3 pytest tmp    : <tmp>/test_walk0
4 worktree src  : <base-worktree>/src/code_indexer/store.py
5 worktree plain: <base-worktree>/tests/test_store.py
6 home          : <home>/dev/<project>/<repo>/uv.lock
7 foreign home  : /home/<foreign-account>/checkout/README.md   (unchanged)
8 relative      : src/code_indexer/cli.py                   (unchanged)
```

All four required substitutions hold, and a foreign machine's path passes
through untouched. The helper derives its repository prefix from its own
location and its home prefix from the running account — no literal machine
path is published. In the copy the process exits 1 after the first file
because the other four files it targets do not exist there; in the repository
all five target files are present (verified), so the in-place run is complete.

The second helper (`runs/issue22/reverify/sanitize_capture.py`, untouched by
`fc2af93`):

```
pytest tmp   : <pytest-tmp>/pytest-3/test_x0/probe.txt
worktree     : <base-worktree>/src/code_indexer/store.py
home (this)  : <repo>
home (other) : <repo>          <-- foreign path rewritten, not passed through
assert 12 == 12                (untouched)
```

Verdict: PASS with a caveat. It still performs its substitutions and publishes
no machine path, but three limits are recorded: it rewrites *any* home path
(including a foreign machine's) to `<repo>` rather than passing it through; its
pytest rule leaves the `/pytest-N/` segment behind the placeholder; and its
prefixes are shape-based rather than derived at run time. It is a one-off
sanitiser for captures already in the tree, so none of this republishes
machine data — it is filed as a finding, not a blocker.

## Bar 6 — the hook is real

```
$ git ls-files -s .githooks/pre-commit
100755 01a7fdd620063bd461257853cfe1709b19042a8d 0  .githooks/pre-commit
```

Throwaway clone of `main` at the gate tip, `git config core.hooksPath
.githooks`, three attempts:

| Staged content | Result |
|---|---|
| a `.md` with a home path | blocked: `absolute-host-path`, `violations=1`, commit exit 1 |
| the same file made repo-relative | committed: `violations=0`, exit 0 |
| a `.md` with a role name and a card id | blocked: `internal-profile-name` + `board-card-id`, `violations=2`, exit 1 |

The blocking commit left the file staged, and the clone's log shows only the
clean probe commit on top of the tip — so the hook fails the commit rather than
warning, and the clean file is not impeded. The hook passes the staged paths
via `--paths`, which is the documented interface.

## Bar 7 — the rule still catches the classes that started this

Independent sweep with `rg` over `git ls-files`, not the project's script:

| Class | Surface | Hits | Reading |
|---|---|---|---|
| absolute home path | all 266 tracked files | 12 | all in `tests/test_watcher.py`, all the synthetic single-letter fixture roots the allow-list exempts |
| Windows user path | all tracked files | 0 | — |
| network share URL (`smb:`) | all tracked files | 0 | — |
| internal role names | 151 authored surfaces | 0 | — |
| board card ids | 151 authored surfaces | 0 | — |

Capture payloads were left intact, not rewritten: `fc2af93` touched no file
under `runs/` with a `.json`, `.txt` or `.log` suffix (its file list is seven
Markdown records plus one `.py`), and the 90 tracked capture payloads still
carry 84 lines with a roster name token — the evidence about the studied
repository is still quotable.

## Bar 8 — suite and linter

```
$ uv run pytest tests/ -q -W error
519 passed, 2 deselected in 73.82s (0:01:13)
exit=0
$ uvx ruff check src tests
All checks passed!
exit=0
```

`-W error` makes any warning a failure; the run is warn-clean. The offline
hygiene test is part of these 519 and itself runs the check over the tracked
tree (it would fail the suite, not just the script, if the tree regressed).

## Bar 9 — the docs are true

| Claim | Check |
|---|---|
| the lint command runs as printed | `uv run python scripts/publishing_hygiene_lint.py` → `scanned=266 skipped=0 violations=0`, exit 0 |
| `--hash-name NAME` prints a digest | prints `sha256("example")` = `50d858e0…545c`, exit 0 |
| output shape `path:line: class: detail` then the counts | matches the emitted lines in bars 2, 3 and 6 |
| authored surfaces are the seven listed suffixes | matches the code's set |
| capture data exempt for names and card ids, not for paths | demonstrated in bar 2 |
| binary, >2 MB and tool/cache directories skipped and counted | demonstrated in bar 3 |
| the hook blocks a violating commit; activation line correct | demonstrated in bar 6 |
| `--help` agrees with the script | the help text's options, defaults and allow-list match `_parse_args` and `SYNTHETIC_SEGMENTS` |
| changelog claims nothing extra | each clause of the entry maps to a demonstrated behaviour above |

`SKILL-CLI.md` carries no publishing-hygiene claim (the CLI surface is
unchanged), so there was nothing there to verify beyond `--help`, which was run
directly.

No schema or behaviour change: the two commits touch no file under `src/`,
`EXTRACTION_VERSION` is still 4, and nothing was reindexed.

## Deviations, ruled

**1. `y` added to the synthetic segment set.** The brief forbade changing test
fixtures, and the watcher tests use a pair of single-letter roots, so the set
enumerates both.

Ruling: the enumerated set is the right shape; do not replace it with a rule
for single-letter segments. A "single-character segment is synthetic" rule
would be strictly broader than the fixtures need and would silently exempt any
real single-letter account (they exist, if rarely) — the wrong direction of
error for a check whose purpose is to fail loudly. The enumeration costs one
line per new fixture and fails closed: a fixture root outside the list is
reported, which a reader sees and fixes deliberately. Keep it enumerated; the
set is the documented, auditable form.

**2. The placeholder/metacharacter exemption in the path rule.** A segment
carrying one of `[]{}()<>*+?^$\|\,` is treated as pattern text.

Ruling: not the narrowest honest exemption, and yes, a real account name can
carry such a character. `$` is the concrete case — machine accounts
conventionally end in it, and the local account tooling accepts it — so
`/home/<account-with-dollar>/…` passes silently; `+` and `,` behave the same
way in probes (`scanned=1 … violations=0`, exit 0). The exemption exists for
exactly one line in the tree: a redaction helper's own regex fragment, which is
a bracketed character class. The narrow form is "a segment that *begins* with
`[`, `{` or `<` is pattern text", which covers that line and drops `$`, `+`,
`,`, `^`, `\` and `|` from the exemption.

Not a blocker for this round: no tracked file relies on the broader form, the
required classes all fire, and the hole needs a real account containing one of
those characters to matter. It is recorded as a finding for the owning surface
so the next hygiene round narrows it rather than inheriting it silently.

## Findings (for the owning surface, not fixed here)

1. Path rule: the metacharacter exemption is broader than the tree needs;
   narrow it to a leading `[`, `{` or `<` (deviation 2 above).
2. Capture sanitiser: its home rule rewrites a foreign machine's path to
   `<repo>` instead of leaving it unchanged, and its pytest rule leaves a
   `/pytest-N/` tail. Harmless for the captures it already ran on, and it
   should not be reused on a foreign capture without a review.
3. Redaction helper: it exits 1 when any file in its fixed target list is
   missing, after having rewritten the ones present. Fine as a one-off; worth a
   `continue` if it is ever run outside its own directory again.

## Dogfood

The record and its captures are themselves published content, so the check was
run over them before commit:

```
$ uv run python scripts/publishing_hygiene_lint.py --paths runs/issue27-publishing-hygiene/gate-verification.md runs/issue27-publishing-hygiene/captures/*
publishing hygiene: scanned=12 skipped=0 violations=0
exit=0
```

The committed captures carry placeholders where a live transcript held a host
path, an account segment, a card id or a role name; the digests in the name-set
capture are verbatim, because that is already their published form. Raw
transcripts stay local working material.
