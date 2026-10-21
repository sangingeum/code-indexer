# Fold verification — publishing hygiene (issue #27)

Narrow re-verification of the five review folds. Not a re-gate: the gate passed
ten bars at `6be8d83`, the review accepted the round at `6544949` with five
folds, and the fold leg landed at `f5f2827`. This record verifies the folded
surface only, against the fold list in `review.md` (section "Fold list").

## State of record

| Fact | Observed |
|---|---|
| Branch / tip | `main` / `f5f2827` (`9f4cd61` folds 1, 2, 4; `f5f2827` folds 3, 5) |
| Signatures | `f5f2827` `%G? = G`, `9f4cd61` `%G? = G` |
| Tree | clean apart from untracked local evidence |
| Fold leg diff | 7 files, 143 insertions, 51 deletions (`fold-captures/fold-diff.txt`) |

Method: `code-indexer changed-symbols --project code-indexer --base 6996126
--impact` for the folded surface in one call, then direct reads of the known
files. `rg` carried the literal sweeps (byte-for-byte paths, digests, name
tokens, split literals); the arithmetic that mattered was checked in captured
output rather than in prose. Every probe below was a script file, run from
outside the checkout, with its output captured beside this record.

## Per-fold result

| # | Fold (as the record specifies it) | Present? | Evidence |
|---|---|---|---|
| 1 | `_is_authored` is the complement of `_is_capture`; `.err`/`.out`/`.jsonl` join the capture suffixes; module docstring states the rule as "everything except capture data"; `SYNTHETIC_SEGMENTS` comment recast as fixture names with the single-letter pair called out | **PRESENT** | `scripts/publishing_hygiene_lint.py:37-44` (docstring), `:72` (`CAPTURE_SUFFIXES`, six suffixes), `:75-81` (comment), `:121-128` (`return not _is_capture(rel)`), `:131-136` |
| 2 | Record-under-`runs/` case added; capture fixture switched `capture.json` → `capture.txt`; concatenated roster name replaced by a digest injected per load; digest set asserted 64 lowercase hex and ≥ the roster size; sanitiser docstring names the deliberate historical import | **PRESENT** | `tests/test_publishing_hygiene.py:160-172`, `:151-157`, `:43-50` + `:81-96`, `:187-196`, `:13-21` and `:232-234` |
| 3 | `AGENT.md` and `CHANGELOG.md` say CI enforces through the offline suite and the hook is the opt-in local early warning, valid only while the lint stays stdlib | **PRESENT** | `AGENT.md:26-31`, `CHANGELOG.md:19-21` |
| 4 | Both record helpers carry the home rule as `(?![A-Za-z0-9._-])` | **PRESENT** | `runs/issue22/sanitize_evidence.py:35`, `runs/issue22/reverify/sanitize_capture.py:41` (both with the rationale comment above the rule list) |
| 5 | `scripts/` inside the lint scope: CI and the `AGENT.md` lint line | **PRESENT** | `.github/workflows/ci.yml:24` (`uvx ruff check src tests scripts`), `AGENT.md:9` |

No fold is absent. `changed-symbols` over `6996126..f5f2827` names exactly these
symbols and nothing else: `_is_authored`, `_is_capture`, `_lint`, `_scan`, six
tests, and the two helpers.

## Bar 1 — the folds are in the tree, one by one

Six surfaces moved, all of them named in the fold list:

```
.github/workflows/ci.yml                  |  2 +-
AGENT.md                                  | 20 ++++---
CHANGELOG.md                              |  4 +-
runs/issue22/reverify/sanitize_capture.py |  8 ++-
runs/issue22/sanitize_evidence.py         |  9 ++-
scripts/publishing_hygiene_lint.py        | 58 ++++++++++++-------
tests/test_publishing_hygiene.py          | 93 ++++++++++++++++++++++++-------
```

Reverting the rule to its pre-fold form makes five of the six fold predicates
false — the tests really would catch it:

```
$ python3 <probe>/mutation_check.py        # loads 6996126's lint module
  PASS  record .md under runs/ is governed         got=True wanted=True
  FAIL  suffixless hook is governed                got=False wanted=True
  FAIL  plain .txt outside runs/ is governed       got=False wanted=True
  FAIL  runs/*.err is capture                      got=False wanted=True
  FAIL  runs/*.jsonl is capture                    got=False wanted=True
  FAIL  runs/*.out is capture                      got=False wanted=True
new assertions failing on the pre-fold rule: 5/6
```

The one check that does not discriminate against the pre-fold suffix whitelist
is the `runs/` record case: `.md` was already on that whitelist, so the old rule
also governed it. That test pins the *other* wrong shape the review named — a
directory-wide `runs/` exemption — not the suffix whitelist. Stated plainly
rather than counted as a sixth catch.

Capture: `fold-captures/mutation-prefold.txt`.

## Bar 2 — the rule is genuinely fail closed

`_is_authored` no longer consults a suffix list at all:

```python
def _is_authored(rel: Path) -> bool:
    """... Authored is the complement of capture: everything except capture data."""
    return not _is_capture(rel)
```

Proved from outside the checkout, through the shipped CLI, with a board card id
(a plain literal detector, so the answer does not depend on knowing the roster)
and a host path in every probe file:

| Throwaway path | authored | capture | exit | classes reported |
|---|---|---|---|---|
| `runs/note.md` (the `runs/`-shaped `.md`) | True | False | 1 | absolute-host-path, board-card-id |
| `runs/note.txt` | False | True | 1 | absolute-host-path |
| `runs/cap.json` / `cap.log` | False | True | 1 | absolute-host-path |
| `runs/cap.jsonl` / `cap.err` / `cap.out` | False | True | 1 | absolute-host-path |
| `runs/weird.dat` (unanticipated suffix) | True | False | 1 | absolute-host-path, board-card-id |
| `runs/noextension` | True | False | 1 | absolute-host-path, board-card-id |
| `.githooks/pre-commit` (suffixless) | True | False | 1 | absolute-host-path, board-card-id |
| `plain.txt` outside `runs/` | True | False | 1 | absolute-host-path, board-card-id |
| clean `.md` / clean `runs/*.txt` | — | — | 0 | `scanned=1 skipped=0 violations=0` |

The `.md` under a `runs/`-shaped directory is governed; a `.txt` there keeps its
payload but still carries the path class; the three new suffixes are capture;
a suffixless file and a plain `.txt` outside `runs/` are governed. The rule is
closed by construction — anything nobody anticipated lands on the governed side.

Capture: `fold-captures/probe-surface.txt` (account masked to `<probe-account>`).

## Bar 3 — no roster name in the authored surface; the digests still resolve

Sweep: `rg`, over `git ls-files` for the whole tree and over the 167 tracked
files that are not capture data, with a pattern file holding each roster token
whole-word and every two-part split joined by a concatenation idiom
(`"a" + "b"`, `"a" "b"`) — so a name reassembled across adjacent literals is
caught too.

| Sweep | Files | Lines matched |
|---|---|---|
| authored surfaces (capture data excluded) | 167 | **0** |
| whole tracked tree | 279 | 38 (all in 14 capture files under `runs/`) |
| positive control, one capture file, same patterns | 1 | 15 (the sweep does match) |

So in authored content the digest list is the only place any roster name is
represented: 0 plaintext occurrences across 167 files, while the same patterns
fire 15 times in one capture — the sweep is not vacuous.

Digests recomputed from the roster, not read from the brief:

```
committed digests              : 11
unique digests                 : 11
well-formed (64 lowercase hex) : True
resolved to a roster role      : 9 / 9
resolved to the orchestrator   : 1 / 1
resolved to the local account  : 1 / 1
digests explained              : 11 / 11
dead digests (no name)         : 0
control labels matching        : 0 / 3 (expected 0)
```

11/11, no dead entry, and three out-of-scope labels (the default profile label,
the framework name, the forge login) correctly match nothing. The
well-formedness test is real: `re.fullmatch(r"[0-9a-f]{64}", digest)` inside a
loop over the set, preceded by `len(digests) >= ROSTER_SIZE_AT_INTRODUCTION`
(11) — `tests/test_publishing_hygiene.py:187-196`.

Captures: `fold-captures/digest-roster-masked.txt`, `.../name-sweep-tally-masked.txt`.

The 38 tree-wide hits are capture payloads, which the rule exempts by design
and which are unchanged since the gate (bar 6). Two notes, recorded rather than
smoothed over: 37 of them are another repository's file and symbol names and
the account token in pytest temp paths, but **one** — the orchestrator role's
name at line 94 of
`runs/issue27-publishing-hygiene/captures/bar4-neutralisation-diff.txt` — is a
roster name inside a capture *about this repository*, i.e. the diff that proves
the neutralisation necessarily quotes the text it removed. A suffix rule cannot
tell that apart from evidence about another repository. It is permitted by the
rule as folded and it predates the folds; it is the one place where the round's
"the roster does not survive" claim rests on the capture exemption alone.

## Bar 4 — the digest injection cannot leak between cases

`_lint()` re-executes the module from source on every call
(`spec.loader.exec_module`) and, when a probe name is passed, replaces
`PROFILE_NAME_DIGESTS` on that module object only. Nothing caches a module, no
fixture is module- or session-scoped (`tmp_path` and `capsys` only), and
`_scan()` builds its own module per call.

Structural probe:

```
fresh load              : 11 digests, probe present = False
after injection         : 12 digests, probe present = True
next load               : 11 digests, probe present = False
first object still 12?  : True
module ids differ       : True
```

Order check, since no random-order plugin is installed: the module's 21 node ids
were collected and run in reverse and in a fixed-seed shuffle. Both pass —
`21 passed in 0.32s` and `21 passed in 0.25s`, `-W error`, exit 0 — so no test
depends on an injection left behind by another.

Captures: `fold-captures/probe-digest-injection.txt`,
`.../hygiene-reverse-order.txt`, `.../hygiene-shuffled-order.txt`.

## Bar 5 — the home-path boundary, both helpers

Both helpers now carry `re.escape(str(HOME)) + r"(?![A-Za-z0-9._-])"`. Applied
to the live home root through each helper's own `RULES`:

| Follower after the home root | `sanitize_evidence.py` | `sanitize_capture.py` |
|---|---|---|
| `,` | REDACTED → `<home>,` | REDACTED → `<home>,` |
| `)` | REDACTED | REDACTED |
| `:` | REDACTED | REDACTED |
| `;` | REDACTED | REDACTED |
| space / end of line / quote | REDACTED | REDACTED |
| `/` (a real subpath) | REDACTED → `<home>/src/app.py` | REDACTED |
| `2` (`.../home/<account>2/...`) | untouched | untouched |
| `-`, `_`, `.` (account-name characters) | untouched | untouched |
| **a closing full stop** | **untouched** | **untouched** |

Capture: `fold-captures/probe-home.txt` (home root masked to `<account>`).

### Ruling on the flagged residual: a bare home root at a full stop

The negative lookahead protects a sibling account (`<account>2`,
`<account>-backup`, `<account>.bak`) because those characters are legitimate
account-name characters; a full stop is one of them, so `.../home/<account>.`
is not redacted. The miss is real, and it is the same class the old positive
form had — just at one follower instead of five.

**Ruling: acceptable for these two record artifacts as they stand, not
acceptable as a reusable utility.** Three reasons, all checkable:

1. The consequence is bounded to the account token itself. A path that goes
   anywhere below the home root still matches at `<account>/`, because `/` is
   not in the lookahead class — so every path that carries project or record
   information is still redacted; only a bare home root followed by a full stop
   escapes, and nothing else about the machine layout travels with it.
2. It cannot fire on the material these helpers produced. The tracked content
   holds no `/home/<account>.` occurrence at all (`rg` over all 279 tracked
   paths: none), and the helpers' own outputs already use the `<home>`
   placeholder.
3. Both files are historical record artifacts pinned by path, never imported as
   libraries; the fold deliverable was the form the record specified, and that
   form is what landed.

If either is ever promoted to a utility, the precise form is two lookaheads —
`(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_-])` — which lets a sentence-final full stop
match while still protecting `<account>.bak`. That is a shape question for a
later round, not a fold failure: the fold asked for `(?![A-Za-z0-9._-])` and
delivered exactly it, with the rationale written into both files.

## Bar 6 — nothing was exempted or edited to get green

The stricter rule needed no new exemption. In the fold leg the only exemption
constant that moved is the one fold 1 ordered, and it moved the other way:

```
-AUTHORED_SUFFIXES = frozenset({".md", ".py", ".sh", ".yml", ".yaml", ".toml", ".cs"})
-CAPTURE_SUFFIXES = frozenset({".json", ".txt", ".log"})
+CAPTURE_SUFFIXES = frozenset({".json", ".txt", ".log", ".err", ".out", ".jsonl"})
-    if rel.suffix.lower() not in AUTHORED_SUFFIXES:
```

`SYNTHETIC_SEGMENTS` and the pattern-lead character list are untouched, and no
new branch was added anywhere. Cost of the stricter rule, measured rather than
asserted: run the tip's script over the review commit's own tree —
`scanned=279 skipped=0 violations=0`. Zero new violations, so there was nothing
to exempt.

Reconciling the review's figure: at `6544949` the old rule left 25 tracked files
genuinely outside the name rules (4 suffixless, 11 `.err`, 1 `.jsonl`, 1 `.lock`,
8 `.scm`), plus 101 capture payloads it also did not govern — 126 in total,
which is the number the review quoted. All 25 are governed at the tip and all
25 are clean.

Bytes since the gate commit `6be8d83`, over the eleven records `fc2af93`
neutralised:

| Path | Changed? |
|---|---|
| `docs/subcommand-design.md` | unchanged |
| `runs/issue21/folds-verification.md` | unchanged |
| `runs/issue22/fix-verification.md` | unchanged |
| `runs/issue22/folds.md` | unchanged |
| `runs/issue22/gate/gate.md` | unchanged |
| `runs/issue22/review-fix-round.md` | unchanged |
| `runs/issue22/review.md` | unchanged |
| `runs/issue22/round.md` | unchanged |
| `runs/issue22/sanitize_evidence.py` | **changed — fold 4, the boundary line and its rationale (8+/1−)** |
| `tests/test_csharp_symbols.py` | unchanged |
| `tests/test_query_staleness.py` | unchanged |

No capture payload changed: every tracked file under `runs/` with a capture
suffix is byte-identical between `6be8d83` and the tip (the only `runs/` paths
that moved are the two helpers and the new `review.md`). The one changed
neutralised record is a helper the fold list explicitly names, and its change
is the rule, not its content.

Captures: `fold-captures/old-rule-gap-by-commit.txt`,
`.../lint-at-review-commit-stricter-rule.txt`, `.../lint-base-tree-stricter-rule.txt`.

## Bar 7 — docs and CI say what is true

- `AGENT.md:26-31` — hook is "the opt-in local early warning, not the
  enforcement: CI enforces the rule through the offline suite
  (`tests/test_publishing_hygiene.py`)", and "the hook's plain `python3` call is
  valid only while the lint stays stdlib". Both statements hold: the hook calls
  `python3 "$root/scripts/publishing_hygiene_lint.py" --root "$root" --paths`
  over the staged files, the lint imports only `argparse`, `hashlib`, `re`,
  `subprocess`, `sys`, `pathlib`, and `core.hooksPath` is **unset** in this
  clone — the hook is opt-in in fact, not just in prose.
- `.github/workflows/ci.yml:24` — `uvx ruff check src tests scripts`;
  `:27` — `uv run pytest -m "not live"`. The offline selection really contains
  the hygiene module: `uv run pytest -m "not live" -q --collect-only
  tests/test_publishing_hygiene.py` → `21 tests collected`. The suite's
  `test_tracked_tree_is_clean` fails the build on any violation, so CI is the
  enforcer.
- `CHANGELOG.md:19-21` — same two sentences, no stronger claim.
- The commands printed in `AGENT.md` run as printed: `uv run pytest tests/ -q
  -W error`, `uvx ruff check src tests scripts`, `uv run python
  scripts/publishing_hygiene_lint.py` (bars 8 and 9).

## Bar 8 — suite and linter

```
$ uv run pytest tests/ -q -W error
533 passed, 2 deselected in 73.37s (0:01:13)        exit 0, no warnings

$ uvx ruff check src tests scripts
All checks passed!                                  exit 0
```

A capture-wide search for `warning` / `warnings summary` in the pytest output
returns nothing: warn-clean, `-W error` on. The counts match the fold leg's
claim exactly (`533 passed, 2 deselected`).

Captures: `fold-captures/pytest-warn-error.txt`, `.../ruff.txt`.

## Bar 9 — dogfood

The record and its captures were scanned before this commit, and the whole
tracked tree at the tip:

```
$ uv run python scripts/publishing_hygiene_lint.py \
      --paths runs/issue27-publishing-hygiene/fold-verification.md \
              runs/issue27-publishing-hygiene/fold-captures/* \
      > /tmp/fold-record-scan.txt
$ cp /tmp/fold-record-scan.txt \
     runs/issue27-publishing-hygiene/fold-captures/lint-record-paths.txt
publishing hygiene: scanned=19 skipped=0 violations=0 (untracked files are out of scope)

$ uv run python scripts/publishing_hygiene_lint.py     # tip before this record
publishing hygiene: scanned=279 skipped=0 violations=0 (untracked files are out of scope)

$ git add runs/issue27-publishing-hygiene/fold-verification.md \
          runs/issue27-publishing-hygiene/fold-captures/
$ uv run python scripts/publishing_hygiene_lint.py     # shipped tree, 298 tracked
publishing hygiene: scanned=298 skipped=0 violations=0 (untracked files are out of scope)
```

The first scan found one violation in the draft of this record — a quoted
roster name — and the sentence was rewritten to name the role instead; the line
above is the re-run after that edit.

(The path scan and the shipped-tree scan are reproduced verbatim in
`fold-captures/lint-record-paths.txt` and `fold-captures/lint-tracked-tip.txt`.)

## Verdict

**PASS.** All five folds are present and each is the change the record
specified. The authored rule is closed by construction and demonstrably so
from outside the checkout; the digest roster still resolves 11/11 with no dead
entry and the well-formedness test is real; the injection is per-load and the
module's tests pass in reverse and shuffled order; both helpers carry the same
negative-lookahead boundary and redact the four prose followers while leaving a
sibling account alone; nothing was exempted, no capture payload or neutralised
record was edited, and the only neutralised file that moved is the helper fold 4
names. Suite `533 passed, 2 deselected` warn-clean, ruff over
`src tests scripts` clean, lint `279/0/0`, and the round's own records pass the
rule they introduced.

Two residuals are recorded rather than buried, neither of them a fold failure:
the full-stop boundary ruling above (acceptable for these artifacts, two
lookaheads if ever reused), and the single roster name inside the
neutralisation-diff capture, which the suffix-based capture exemption permits
and which predates the folds.

Board card for this leg was read only; no board write, no issue opened or
touched. This gate verified; it did not fix.
