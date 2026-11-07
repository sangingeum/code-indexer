# Split the query reference at its concern boundary

Starting tip: `e5aa8a1`. The ruling was split, not raise: `query.md` stood at
399 of its 400 lines, and a reference that outgrows the budget is split at its
next concern boundary rather than exempted.

## The boundary, and why it is there

Two kinds of question were sharing one file. A structural question starts
from a name or a path and ends at a declaration: `overview`, `skeleton`,
`outline`, `find-*`, `get-code-context`, `deps`, `changed-symbols`, `map`,
`file-outline`. A retrieval-quality question starts from meaning or from a
report: `semantic-search`, `eval`, `eval-compare` -- plus the ordering policy
that decides the order of every ranked answer, including the structural ones.
The first kind is answered from the symbol table; the second is answered from
vectors, a query set or a pair of report files. That is the line the split
follows: `query.md` keeps the structural commands, `semantic.md` owns the
retrieval surfaces and the ordering policy, and `query.md` points at it with
one line instead of restating it.

The ordering prose could have stayed in `query.md` on the argument that most
of the surfaces it orders are structural ones. It moved instead because the
policy is one object: keeping half of it in each file is the duplication the
split exists to remove, and a reader who needs the order of a `find-callers`
answer is already reading about what an answer contains.

## Size table

| file | before (lines / bytes) | after (lines / bytes) | budget |
|---|---|---|---|
| `SKILL-CLI.md` | 120 / 6,612 | 109 / 6,931 | 120 / 8,192 |
| `skill/references/indexing.md` | 337 / 16,146 | 337 / 16,146 | 400 / 24,576 |
| `skill/references/query.md` | 399 / 20,982 | 292 / 13,798 | 400 / 24,576 |
| `skill/references/semantic.md` | -- | 191 / 11,451 | 400 / 24,576 |
| `skill/references/plans.md` | 204 / 10,595 | 204 / 10,595 | 400 / 24,576 |
| `skill/references/machine-output.md` | 141 / 6,224 | 141 / 6,224 | 400 / 24,576 |
| `skill/references/failures.md` | 127 / 9,178 | 127 / 9,178 | 400 / 24,576 |

`query.md` ends at 292 lines and 13,798 bytes: 108 lines and 10,778 bytes of
headroom, so the next concern has room to be written rather than squeezed.
`semantic.md` opens at 191 lines, 209 lines under its budget. The entry
document gained a reference row and lost more than it gained, because the
roster became one table with a `Reference` column instead of four grouped
tables with a pointer heading each: 109 lines, 11 under the cap.

## The four restored examples

Each was run against this checkout; the command strings below are exactly what
the reference carries.

1. `outline --docstrings` -- `skill/references/query.md:125`, in "One file:
declarations and docstrings". Run: exit 0.

   ```
   $ code-indexer outline src/code_indexer/core.py --docstrings
   function _hit_identity:43-59  def _hit_identity(hit: dict[str, Any], width: int = 80) -> str:
     What a hit matched, for every output format.
   function _merge_symbol_ranges:93-109  def _merge_symbol_ranges(
   class Core:124-140  class Core:
     Stateful core: owns config, registry, embedder, store, indexer.
   function __init__:133-148  def __init__(self, cfg: Config | None = None, skip_stale_check: bool = False):
   ```

2. `get-code-context --symbol` -- `skill/references/query.md:235`, in "Reading
source". Run: exit 0.

   ```
   $ code-indexer get-code-context src/code_indexer/core.py --symbol search --context-lines 3
   --- src/code_indexer/core.py:1060-1084 ---
    1060|             out = head + out[RERANK_POOL:]
    1061|         return out
    1062|
    1063|     def search(self, query: str, project: str | None = None, limit: int = 8,
   ```

   The spelling is `--symbol search`, not `--symbol Core.search`, because the
   lookup matches the name a file's rows carry and that name is the bare
   declaration name; `Core.search` is the `qualified_name` `find-symbol`
   reports. The dotted form was run too, so the reference can say what it does
   rather than leave it to be discovered:

   ```
   $ code-indexer get-code-context src/code_indexer/core.py --symbol Core.search --context-lines 3
   InternalError: symbol 'Core.search' not indexed in src/code_indexer/core.py
   exit 1
   ```

3. `changed-symbols --base/--head --impact` -- `skill/references/query.md:260`,
in "What changed". Run: exit 0.

   ```
   $ code-indexer changed-symbols --base HEAD~5 --head HEAD --impact
   other files: docs/adr/0003-read-only-and-staleness-contract.md, docs/adr/0009-agent-surface-skill-layout.md, ... (+2 more)
   added runs/r4a-rereview/probe_truncated_recheck.py / function _write:19-21
   ```

   That range's only changed symbol has no callers, so no `impact:` line is
   printed; the shape appears as soon as one does (a wider range against the
   same tree):

   ```
     impact: src/code_indexer/core.py format_hits (ast)
     impact: tests/test_search_class_order.py test_hit_identity_falls_back_to_the_file_qualifier (ast)
     impact: (+34 more)
   ```

4. `eval` / `eval-compare` -- `skill/references/semantic.md:64`, `:68` and
`:72`, in "The eval harness". The placeholders are the project to score (a
path, slug or name; run here as `--project code-indexer`) and the report
paths (`--out`, then read by `eval-compare`). Run: exit 0 each.

   ```
   $ code-indexer eval --project code-indexer --queries eval/queries.jsonl --k 1,3,5,10 --out /tmp/eval-dense.json
   eval project=<checkout> mode=dense queries=42
     recall@1=0.5952  recall@5=0.8333  mrr=0.6949  ndcg@10=0.7404 ...
   ```

   ```
   $ code-indexer eval-compare /tmp/eval-dense.json /tmp/eval-rerank.json
   metric                    a          b         delta
   ----------------------------------------------------
   recall@1                    0.5952    0.5952    +0.0000
   recall@3                    0.7619    0.8095    +0.0476
   recall@5                    0.8333    0.8810    +0.0477
   ...
   improved (5): q-concept-01, q-concept-05, q-ident-03, q-ident-07, q-ident-08
   regressed (1): q-concept-09
   unchanged (36): q-concept-02, q-concept-03, ...
   ```

   The candidate report (`--rerank heuristic`) was written first by the second
   `eval` invocation the reference carries; the two files it writes are what
   `eval-compare` reads, so the example set is closed.

## Generator and entry document

`scripts/generate_skill_docs.py` gained `semantic.md` in `REFERENCES` with
`semantic-search`, `eval`, `eval-compare`, and `query.md` lost them. Everything
else follows from that mapping: the generated section key
`commands-semantic`, the file `GENERATED` points at, the roster rows, and the
JSON roster in `machine-output.md`. Nothing was hand-edited inside a block;
`uv run python scripts/generate_skill_docs.py --check` exits 0.

The roster changed shape from one heading-plus-table per reference to a single
table with a `Reference` column (name, class, reference, one line). The
pointer now moves with the command because both come from the same registry
row, and the entry document came in 11 lines under its cap instead of sitting
on it. `tests/test_docs.py`'s roster reader follows the new shape; the roster
still has to equal the registry, name a reference that carries each command
and be carried by exactly one reference.

## Verification at the tip

- `uv run pytest tests/ -q -W error` -- 998 passed, 2 deselected, 1 failed:
  `tests/test_docs.py::test_installed_copy_digest_matches_the_tracked_copy`.
  That is the declared red: the installed copy is a directory in the new shape
  holding the previous five references, so its digest disagrees with the
  tracked six until the install step refreshes it from this checkout. No other
  test is red, and the baseline at `e5aa8a1` was 998 passed, 2 deselected.
- `tests/test_docs.py` -- 38 of 39 pass; index closure (every registered
  command in exactly one reference, every reference named and existing),
  budgets for the entry document and all six references, taxonomy coverage and
  generated-block equality all hold. The single failure is the digest above.
- `uvx ruff check src tests scripts` -- all checks passed.
- `uv run python scripts/publishing_hygiene_lint.py` -- scanned 921,
  violations 0.

## Carried open

- The installed skill directory still holds the five-reference copy; the
  digest test is red until the release step installs the six-reference tree.
  That install is not this leg's to perform.
- `src/code_indexer/ordering.py` and `src/code_indexer/ranking.py` name
  `skill/references/query.md` in their module docstrings as the home of the
  ordering policy, which is now `semantic.md`. The docstring text is prose in
  source, outside this leg's allowlist, so it was left as it stands; it is a
  comment-only correction for whoever next touches those modules.
- `get-code-context --symbol` accepts the bare declaration name and refuses the
  dotted `qualified_name` an agent will most naturally try (exit 1,
  `InternalError`). The reference now documents the real spelling and the
  refusal. Whether the command should accept the qualified name is a behaviour
  question for its own card, not for a documentation split.
