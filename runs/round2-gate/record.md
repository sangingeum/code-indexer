# Round 2 verification gate — record

- Card: `<card-id>` (identifier withheld: `runs/*.md` is an authored, hygiene-governed
  surface, and a board id is a prohibited token there)
- Repo: `code-indexer`, branch `main`, tip under test `cf6af65`
- Round base: `a22769d`
- Gate role: household QA/test engineer
- Date: 2026-11-07
- Verdict: **PENDING — skeleton committed before the bars were measured**

## Method

Every bar is ruled on fresh output run by this gate at `cf6af65`, captured under this
folder with its command and exit code beside it. The two dev legs' figures are treated as
claims and re-measured. Bar names below are concepts; the dispatch brief's ordinal labels
are provenance only.

Iron law observed: no pass was run against a live project and no live collection was
touched. Migration and multi-version work ran on **copies** of a manifest inside a
throwaway index root.

Reading caveat: the house `code-indexer` index over this checkout is at extraction 4 while
the tip writes extraction 5, so its row surfaces refuse with exit 4 by design. Exploration
therefore used `rg`/`fd` and direct file reads where `code-indexer` refuses, as the dispatch
brief allows for this round.

## Bar table

| Bar | Verdict | Command | Exit | Observed |
|---|---|---|---|---|
| suite and lint bar | PENDING | | | |
| one-schema-revision bar | PENDING | | | |
| service-free-pass bar | PENDING | | | |
| vectors-on-reads-no-source bar | PENDING | | | |
| chunk-maintenance bar | PENDING | | | |
| axes-and-policy-matrix bar | PENDING | | | |
| per-side-fingerprint bar | PENDING | | | |
| vectors-off-search-refusal bar | PENDING | | | |
| refusal-matrix bar | PENDING | | | |
| migration bar | PENDING | | | |
| identity-survives-line-shift bar | PENDING | | | |
| docs-truth bar | PENDING | | | |
| live-index-untouched bar | PENDING | | | |

## Rulings owed

1. `--mode lexical` on a `vectors=off` project — PENDING
2. Row surfaces refusing only on an extraction/chunker difference — PENDING
3. The extraction bump refusing the household's own exploration — PENDING

## Findings

PENDING.

## What this gate does not verify

- Rounds 3, 4 and 5 (structural query, plans and apply, provider and agent surface).
- The operational reindex of the live projects, which is a post-gate step.
