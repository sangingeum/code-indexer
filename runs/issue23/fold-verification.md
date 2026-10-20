# Fold verification — issue #23 (get-code-context --symbol resolution)

Verdict: **PASS**

- Fold set under test: `498a060` (review record) → `aa947f3` (the development
  leg's folds of review items 1-3) → `454ef10` (this gate leg's folds of review
  items 4-5). `454ef10` was the tip of `origin/main` when the checks below ran;
  this record is the commit added on top of it, and every finding is judged on
  pushed content, not on a worktree.
- Both fold commits signed (`%G? = G`).
- Issue #23 remains **open**. This is the narrow pass over the folds, not a
  replay of the eight gate bars.

## Item 1 — the fixture state leak is fixed and nothing was weakened — PASS

Teardown-only additions, no assertion touched:

```
$ git diff 498a060..aa947f3 --stat
 CHANGELOG.md                    | 19 ++++++++++++++-----
 SKILL-CLI.md                    |  7 ++++---
 src/code_indexer/core.py        | 11 ++++++-----
 tests/test_symbol_resolution.py |  8 ++++++++
 4 files changed, 32 insertions(+), 13 deletions(-)
```

`tests/test_symbol_resolution.py` shows 8 insertions and 0 deletions — the test
file's whole diff is additions:

```
@@ -111,6 +111,9 @@ def wide_rig(tmp_path, monkeypatch):
     yield core, reg, entry, project, cfg
     reg.close()
+    import code_indexer.cli as cli
+    cli._core = None
+    cli._core_skip = False
@@ -120,6 +123,9 @@ def repeated_rig(tmp_path, monkeypatch):
     yield core, reg, entry, project, cfg
     reg.close()
+    import code_indexer.cli as cli
+    cli._core = None
+    cli._core_skip = False
@@ -163,6 +169,8 @@ def test_cli_symbol_mode_does_not_deny_an_indexed_row(wide_rig):
     assert "def prepare():" in result.output
+    cli._core = None
+    cli._core_skip = False
```

The state-leak probe, in the order that used to depend on ordering luck:

```
$ uv run pytest tests/test_symbol_resolution.py tests/test_units.py tests/test_unwatch.py -q -W error
............................                                             [100%]
28 passed in 4.88s                                                        rc=0
```

**Teardown on the failure path — confirmed, not assumed.** In both fixtures the
`yield` is the last statement before the teardown block, so the reset sits in the
generator body after the yield; pytest resumes that body once the test finishes,
including when the test raises, so the two reset lines execute on the failure
path as well. Confirmed empirically as well: a throwaway probe outside the repo
(a `yield` fixture that writes a marker after the yield, a test body that raises
after the fixture was entered) ran to

```
FAILED test_teardown_probe.py::test_body_raises
1 failed in 0.04s
marker file written by the fixture teardown: "teardown ran"
```

That is a `yield` fixture's teardown running with the test failing, which is the
property the fix relies on.

## Item 2 — the wording matches the measured behaviour — PASS

Row position read from the capture, not from the prose:

```
$ jq -r 'to_entries as $e | ($e|length) as $n | $e[]
        | select(.value.file=="src/code_indexer/manifest.py" and .value.name=="__init__")
        | "position=\(.key+1) of \($n)"' runs/issue23/find-symbol-init.json
position=33 of 40
```

`CHANGELOG.md` names exactly that — `q-concept-04`, `src/code_indexer/manifest.py`
/ `__init__`, row 33 of 40 — and adds that an `eval-compare` against a report
recorded before the change is not comparable for that query, which is the
comparability clause the review asked for.

The decorated-definition case, measured on the live index:

```
$ code-indexer get-code-context src/code_indexer/cli.py --symbol get_code_context --json --skip-stale-check
{"file": "src/code_indexer/cli.py", "stale": false, "re_resolved": true,
 "n_segments": 2, "ranges": ["1435-1452", "1436-1452"]}                     rc=0
```

Two overlapping ranges for one definition (`def get_code_context`, `cli.py:1436`).
All three surfaces say "every matching symbol **row**" and name that duplication:
`CHANGELOG.md` ("Rows are not definitions: a decorated definition contributes two
rows, so it surfaces as two overlapping ranges"), the `Core.code_context`
docstring, and `SKILL-CLI.md`'s **get-code-context** entry.

Doc-drift guards, stated exactly: `tests/test_docs.py` contributes 13 tests to
the suite run below and they are green; the module guards `README.md` and
`SKILL-CLI.md` against drifting from the code. It does **not** read
`CHANGELOG.md` — `rg CHANGELOG tests/` returns nothing — so the changelog clause
is verified here by inspection against the capture above, not by a test.

## Item 3 — no behaviour change in the fold commit — PASS

```
$ git show --stat --format='%h %s%n%G?' aa947f3
aa947f3 docs(issue-23): fold the review findings on symbol resolution
G

 CHANGELOG.md                    | 19 ++++++++++++++-----
 SKILL-CLI.md                    |  7 ++++---
 src/code_indexer/core.py        | 11 ++++++-----
 tests/test_symbol_resolution.py |  8 ++++++++
 4 files changed, 32 insertions(+), 13 deletions(-)
```

`src/` across the whole fold sequence, which is the docstring alone:

```
$ git diff 81f0c8c..HEAD --stat -- src/
 src/code_indexer/core.py | 11 +++++-----
 1 file changed, 6 insertions(+), 5 deletions(-)

$ git diff 81f0c8c..HEAD -- src/ | rg '^\+[^+]'
+        Symbol mode returns EVERY matching symbol row in this file — the
+        name and file filters run in one uncapped read, so a row that sorts
+        past ``find_symbols``' default cap is still found, and a name with
+        more than five matching rows in one file is not shortened
+        (issue #23). Rows are not definitions: a decorated definition
+        contributes two rows, so it surfaces as two overlapping ranges.
```

Six added lines, all prose inside the `code_context` docstring; no executable
line changed. Note on scope: the range from the pre-fix commit to the tip also
carries the gate record and the review record, so its stat lists 17 files. The
fold commit itself is the four files above, and within `src/` the entire range is
one docstring.

## Item 4 — the folds are clean on the pushed content — PASS

Judged from `origin/main`, not from the worktree (`git show origin/main:<path>`),
over the three files this round touched — the gate record and both published
captures. Four pattern classes were run against each file, case-insensitively:
agent profile names, absolute host path prefixes (home and temp), the local
card-id shape, and the dispatch-text word. Result:

```
(no matches in any of the three files)
```

Zero matches, so there is no surviving prose use to justify line by line. The
edits that produced that state:

- the local card id in the gate record's header block → "the gate's card";
- the two references to the dispatching text in the observations paragraph →
  "the dispatching round's description" (same class, same fix).

Both published captures carry a declared header; first two lines of each, as
pushed:

```
runs/issue23/gate-bar3-base-cli.txt
# Publication note: paths elided. The base worktree's import path and the
# --project argument of the three commands below are absolute host paths; they

runs/issue23/gate-bar3-base-pytest.txt
# Publication note: paths elided. The pytest temporary-directory path in the
# traceback below is an absolute host path carrying the host user name; it is
```

Tracked state of the directory on `origin/main` — both redacted captures still
tracked, no raw copy tracked:

```
$ git ls-tree --name-only origin/main runs/issue23/
bar5-callers.json  find-symbol-init.json  gate-bar1.txt  gate-bar2.txt
gate-bar3-base-cli.txt  gate-bar3-base-pytest.txt  gate-bar3-tip-pytest.txt
gate-bar6-pytest.txt  gate-bar6-ruff.txt  gate-bar8-shape.txt  gate.md
init-files.txt  review.md
```

The unmodified captures live outside the repository in the local evidence store
for this round; their content hashes were checked against the files before
redaction and are unchanged (`fe0ac18e…` for the CLI capture, `667a9028…` for the
pytest capture).

## Item 5 — suite and lint, once each — PASS

```
$ uv run pytest tests/ -q -W error
506 passed, 2 deselected in 72.44s (0:01:12)                              rc=0

$ uvx ruff check src tests
All checks passed!                                                        rc=0
```

Same totals as the gate's bar 6, warn-clean and lint-clean at the tip.

## Item 6 — no reindex is still owed — PASS

```
$ rg -n "EXTRACTION_VERSION" src/code_indexer/graph_extract.py
70:Extraction version: bump EXTRACTION_VERSION when the queries or resolution
95:EXTRACTION_VERSION = 4

$ git diff 81f0c8c..HEAD --name-only -- src/
src/code_indexer/core.py
```

The version constant is 4, unchanged from base, and the only `src/` change across
the whole fold set is the `core.py` docstring. `CHANGELOG.md` and `SKILL-CLI.md`
are documentation. Nothing about index generation moved, so no reindex is owed.

## Redaction diff summary — the two published captures

| file | raw lines | published lines | changes from the raw capture |
|---|---|---|---|
| `runs/issue23/gate-bar3-base-cli.txt` | 31 | 36 | 5 header lines + 4 substitutions: the import path on line 1 → `<base worktree>/src/code_indexer/__init__.py`; the `--project` argument on lines 2, 6, 10 → `<checkout>` |
| `runs/issue23/gate-bar3-base-pytest.txt` | 146 | 151 | 5 header lines + 1 substitution: the pytest temporary-directory path on line 17 → `<pytest tmp>` (the rest of that `ProjectEntry(...)` line, including the slug, timestamp and name, is byte-identical) |

`diff` between each raw capture and its published copy yields 13 changed lines in
the first file and 7 in the second — the header adds and the substitution pairs,
nothing else. No placeholder is itself an absolute path, and the headers name the
class of path elided without reproducing one.

## What stayed identical

- **Every command, exit code and outcome line** in both captures: `rc=1` with
  `InternalError: symbol '__init__' not indexed in …` for `registry.py` and
  `doctor.py`, `rc=0` with the `39-56` range for `embedder.py`, and the base
  pin-file outcome of **5 failed, 1 passed**. The base-build claim in the gate
  record therefore stays auditable from the published files alone.
- **Every captured source line** in the `embedder.py` excerpt (lines 39-56) and
  every traceback line of the pin-file capture, other than the elided path.
- **Every verdict, number, range, row position and count** in the gate record.
  The three gate-record edits are the card id and the two dispatch-text
  references; no evidence line changed, and no other paragraph was touched.
- **Every test assertion** in the fold set: 8 added lines in
  `tests/test_symbol_resolution.py`, 0 removed.

Issue #23 stays open; the behaviour was ruled correct by the review and nothing
in this pass reopens it. Follow-ups named in the review (the unreachable
dotted-name branch, scoring `_symbol_range` against every row, collapsing
overlapping symbol-mode ranges) remain out of scope for this fold set.
