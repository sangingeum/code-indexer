# Folds — the issue #22 re-review (PASS WITH FOLDS)

Findings folded: the eight from `runs/issue22/review-fix-round.md` (F1-F6, F9,
F10, F11). The ninth item of that list — `get-code-context --symbol` resolves
its symbol from a capped read and can report `not indexed` for a row the store
holds — is filed separately as issue **#23** and is **not** touched here: it is
pre-existing, a different symptom (a false negative, not a false completeness
claim), and it is a code fix.

Constraint honoured: doc prose, code comments/docstrings, user-facing strings
and two test pins. No executable behaviour change; `EXTRACTION_VERSION` stays 4;
no schema change; no reindex.

## Tip note

The dispatch named `79906a1` as the tip. Two doc-only commits landed after it —
`5cb759a` (the narrow re-verification record) and `d0e0707` (a timing row
matched to its committed capture). `git diff 79906a1..d0e0707 -- src/` is
empty, so `79906a1` remains the right "before" base for this leg and the
classification below is unaffected.

## Disposition per fold

| id | where | disposition |
|---|---|---|
| F1 | `runs/issue22/fix-round.md:37-48` | **folded** — the `--max-nodes 10` row now reads `omitted 124` and quotes **both** notes, matching its own committed capture (`runs/issue22/after-roots-find-callers-code-indexer-max-nodes-10.json`); the lead-in says the roots spend the whole budget so level-0 edges are refused and counted. Capture is right, prose now agrees. |
| F2 | `SKILL-CLI.md:494-513` | **folded** — the paragraph now opens "Under these walks the manifest store hands over everything it holds"; `definitions_of` is stated as having **no limit parameter at all** (stronger and true), the three `limit: int \| None = None` reads are named separately, and the closing sentence scopes the claim away from `refs_to`'s and browse `find_symbols`' own 25-row defaults. |
| F3 | `manifest.py:531-540` | **folded** (docstring) — "its first four columns are the table's primary-key column set (`(file, line, to_name, relationship)`, a different order) and the fifth is the receiver". Matches the DDL at `manifest.py:82`; the totality conclusion is unchanged. |
| F4 | `changed.py:326-341` | **folded** (docstring + user-facing string) — the remedy now reads `find-callers <symbol> --max-nodes N` with `N` above the count, in both the docstring and the emitted impact note. The same advice line in `docs/json-schema.md:87-91` said the same over-promise and was corrected in the same pass (declared below). |
| F5 | `graph.py:110-127` | **folded** (docstring) — "A hidden root's subtree is never walked, so the sibling edge count covers the roots that were shown only … it must not be read as the size of everything hidden." |
| F6 | `core.py:774-782` | **folded** (comment) — the invariant is written at the site: the read caps at 25, the pin takes 3, the order key is total, so 3 of 25 and an uncapped read pin the same three; and the contrast with `get-code-context --symbol` (filter after the cap) is named. |
| F9 | `tests/test_graph.py::test_a_capped_root_list_counts_the_edges_it_also_refused` | **folded** (new pin) — a capped-root subject with one reference row answers `roots 10, truncated true, omitted 1` with exactly two notes, root note first and edge note second. |
| F10 | `tests/test_graph.py::test_find_symbol_browse_mode_keeps_its_default_cap` | **folded** (new pin) — `find-symbol --type class` with no `--limit` returns 25 of the rig's 40 classes; `--limit 40` returns all 40 in the same fixture, so the pin is about the default, not about the data. |
| F11 | `SKILL-CLI.md:337-348`, `docs/json-schema.md:114-126` | **folded** — the false combination is gone from both. The roots themselves are still never counted into `omitted`; what changed is that `omitted: 0` is no longer presented as the shape of a root-capped answer. Both documents now carry the measured pair. |

## F11 — the measured pair, at this tip

```
$ uv run code-indexer find-callers __init__ --project code-indexer \
      --max-nodes 10 --json --skip-stale-check
→ roots 10, truncated true, omitted 124
  note: 40 definition roots exist for this subject, node cap 10 — 30 roots not shown; raise --max-nodes
  note: node cap 10 reached — 124 more reference edges not shown (a lower bound: ...); raise --max-nodes to see more
```

Capture: `runs/issue22/folds/f11-tip-max-nodes-10.json` (stderr in the `.err`
sibling). The roots spend the whole budget, so level-0 edges are refused too
and are counted. The accounting is right; only the prose was wrong, and the
code was not touched.

### Declared in-class extensions (same finding, same kind of text)

1. **`CHANGELOG.md:18-26`** carried a third copy of the falsified sentence ("is
   a second, documented instance of `truncated: true` with `omitted: 0`").
   F11 named two documents; leaving the changelog asserting the same number the
   code contradicts would have kept the finding open under a different
   filename. Corrected to the measured pair.
2. **`docs/json-schema.md:87-91`** carried the `find-callers <symbol>` advice
   that F4 corrected in the emitted string. Corrected alongside it so the
   documented remedy and the printed remedy agree.
3. **`tests/test_graph.py`** (`test_a_capped_root_list_says_how_many_roots_exist`)
   had F11's claim in its docstring. That fixture has no reference rows, so
   `omitted == 0` is true *of the fixture*; the docstring now says exactly that
   and points at the two-note pin instead of generalising.

None of the three is a behaviour change; all are the same prose defect.

## `git diff 79906a1..HEAD -- src/` — per-hunk classification

Five hunks in four files, 21 insertions / 9 deletions. Machine-checked by
`runs/issue22/folds/classify_src_diff.py` (AST comparison: docstrings stripped,
then string-literal values flattened; anything surviving both is an executable
change and fails):

| file | hunk | classification |
|---|---|---|
| `changed.py` | `@@ -327` | **docstring** (`_impact_bound_note`) |
| `changed.py` | `@@ -338` | **user-facing string literal** — the emitted impact note; the only literal that changed anywhere in this diff |
| `core.py` | `@@ -774` | **comment** |
| `graph.py` | `@@ -115` | **docstring** (`_root_cap_note`) |
| `manifest.py` | `@@ -530` | **docstring** (`callers_of`) |

```
src/code_indexer/changed.py: string literal text only (code tree identical)
  - " callers per symbol (raise --max-nodes does not affect that bound; run find-callers <symbol> for one symbol's whole fan-in)"
  + " callers per symbol (raise --max-nodes does not affect that bound; run find-callers <symbol> --max-nodes N with an N big enough to cover one symbol's fan-in)"
src/code_indexer/core.py: comments/docstrings only (code tree identical)
src/code_indexer/graph.py: comments/docstrings only (code tree identical)
src/code_indexer/manifest.py: comments/docstrings only (code tree identical)
```

Full uniform diff: `runs/issue22/folds/src-diff-79906a1-HEAD.txt`. F3, F5 and F6
are comments/docstrings; F4 is a docstring plus one user-facing string — that
is the split, stated explicitly.

## The two pins can fail

`runs/issue22/folds/revert_sensitivity.py` → `revert-sensitivity.txt`. Each pin
is flipped one line at a time on the committed tree, the single test is run,
and the file is then restored with `git restore --source=HEAD --staged
--worktree tests/test_graph.py` and checked against a copy taken before the
first flip:

```
committed tests/test_graph.py: sha256 bd320413b17ead8f3c5de192f311f995f02e5d2a5a702562920a5f3167936fb6

F9  flipped payload["omitted"] == 1 → == 0
    assert 1 == 0            FAILED   exit=1
    restored: sha256 …6fb6 MATCH
F10 flipped len(rows) == 25 → == 40
    assert 25 == 40          FAILED   exit=1
    restored: sha256 …6fb6 MATCH
```

The F9 flip is the pre-fold claim itself (`omitted: 0`), so the pin fails on
exactly the prose F11 removed; the F10 flip asks for an uncapped browse read,
so the pin fails if the default ever becomes "everything". Both restored
byte-exact, `git status --porcelain tests/` empty afterwards.

## Gates at the fold tip

```
uv run pytest tests/ -q -W error   -> 500 passed, 2 deselected in 71.59s
uvx ruff check src tests           -> All checks passed!
```

500 = the 498 of the fix round plus these two pins. `EXTRACTION_VERSION` is 4
(`graph_extract.py:95`), unchanged; no schema change; no reindex run.

## Commits

```
20f599b docs: correct the root-cap accounting and the store-read claim        %G?=G
6a19acc docs(src): say what the caps actually are at four sites               %G?=G
5922dbb test(graph): pin the two-note root cap and browse mode's default 25   %G?=G
```

Issue #22 is **not** closed by this leg. Board card T-000012 was read only
(`tack --db ~/.hermes/state/tackboard/code-indexer.sqlite3 show T-000012`);
no board writes.
