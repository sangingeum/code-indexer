# Round 1 verification gate — record

- Card: the round-1 verification card (identifier withheld: `runs/*.md` is an authored, hygiene-governed surface)
- Repo: `code-indexer`, branch `main`, tip under test `f7d63f1` (`origin/main` == `f7d63f1` at gate start)
- Gate role: household QA/test engineer
- Date: 2026-11-07
- Verdict: **PENDING — bars being measured**

Every bar below is ruled on fresh command output run by the gate at `f7d63f1`, captured
under this directory with the command and exit code recorded beside it. Dev-leg figures
are treated as claims, not evidence.

## Bar table

| Bar | Claim | Status | Evidence |
|---|---|---|---|
| B1 | Suite green and warn-clean from a clean tree | pending | `B1-pytest.txt` |
| B2 | No client library on the CLI start path | pending | `B2-importtime.txt` |
| B3a | Read commands leave manifest hash and `meta.last_indexed` unchanged | pending | `B3a-*` |
| B3b | Read commands dial nothing (offline sink, zero connections) | pending | `B3b-*` |
| B4 | `fresh` / `stale` / `unknown` verdicts, text and JSON shape | pending | `B4-*` |
| B5 | Exit-code taxonomy 0/1/2/3/4/5 reproduced | pending | `B5-*` |
| B6 | Failed search is not an empty one; exit 3 inside budget | pending | `B6-*` |
| B7 | `--refresh` is the only read→write trigger; flock → 5 | pending | `B7-*` |
| B8 | Extras install matrix in throwaway venvs | pending | `B8-*` |
| B9 | MCP surface, relay, annotations, usage record | pending | `B9-*` |
| B10 | No index data rewritten in the round | pending | `B10-*` |
| B11 | Docs match behaviour; `test_docs.py` asserts the new truth | pending | `B11-*` |
| B12 | Quality gates: ruff, hygiene, no host paths, signed commits | pending | `B12-*` |

## Rulings owed

1. `doctor` still dials — pending.
2. Matched indexes report `fingerprint=unverified` — pending.
3. `find_definition` as an eleventh removal — pending.

## Findings

_None recorded yet._

## What this round does NOT verify

Rounds 2-5 surfaces: the `chunks` table and vectors-off pass, the three version axes,
symbol identity and ambiguity envelopes, structural query filters, the plan format and
apply path, the provider interface, the skill split, and the MCP keep-or-remove decision.

## Notes on the measurement environment

The indexed tree is this repository, so evidence files written under `runs/` during the
gate are themselves visible to a staleness scan of `code-indexer`; counts that include
this directory are called out where they occur.
