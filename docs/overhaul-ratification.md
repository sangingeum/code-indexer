# Ratification of the overhaul decision set

- Date: 2026-11-07
- Ratified by: the owner, recorded by the orchestrating role
- Records: `docs/adr/0001` through `docs/adr/0009` plus `docs/overhaul-plan.md`

## What was ratified

Nine decision records and one plan, in the order `docs/overhaul-plan.md` lays out.
The records set the index's dependency boundary (ADR-0001), the symbol identity
(ADR-0002), the read-only and staleness contract (ADR-0003), the edit plan and
its lifecycle (ADR-0004), the apply path and its recovery guarantee (ADR-0005),
the language-server strategy (ADR-0006), the MCP surface (ADR-0007), the absence
of a rename (ADR-0008) and the agent surface (ADR-0009).

Ratified as a set, because the plan's ordering argument depends on all nine: a
schema change on a codebase whose reads still write is a migration that cannot
be verified, and a plan locator is the identity.

## What the owner also approved, with the set

1. **The round 2 reindex**, as the plan's register describes it: one schema
   revision, one reparse per project, no collection rebuild, no service cost.
   The tool refuses with the rebuild exit code until each project has been
   passed once, and the pass is the ordinary per-project command that already
   exists.
2. **The round 1 behaviour change as a visible breaking change**: reads stop
   repairing the index, a failed query stops exiting zero, and an unscoped
   query stops fanning out over every project.
3. **Deferral of the language-server install** to the point round 5 needs it.
   The interface half of round 5 closes without a server; its absence is
   reported rather than absorbed.
4. **The MCP survival question deferred to measured use**, per ADR-0007: the
   narrowing and the relay fix are decided, the keep-or-remove decision is not.

## Folds applied at ratification

The re-verification record left two items open and two smaller drifts. They were
folded in the same commit that recorded the ratification, before the statuses
changed, so no accepted record was corrected after acceptance:

| Item | Fix |
|---|---|
| Seven registered commands had no reference file in ADR-0009's table | `indexing.md` now covers `index-status`, `list-projects`, `lookup-project` and `doctor`; `query.md` covers `semantic-search`, `eval` and `eval-compare`; section 5's closure check now fails when a command has no home |
| ADR-0009's installed-copy digest check named no directory | section 4 names the shape `<skills-root>/code-indexer/` and states that the root is passed to the release step as an argument, because a machine path in a published document is a defect |
| ADR-0009 claimed a disagreement with ADR-0007 section 5 that ADR-0007 does not hold | reworded as a recorded agreement with a different reason, which is what the two records actually say |
| Round 5 said the roster is generated while ADR-0009 section 3 made only reference blocks generated | ADR-0009 section 3 now makes the roster generated from the registry too, so a command cannot exist undocumented and a class cannot drift from ADR-0003 |
| The documentation module's fenced-block parser would break when the roster becomes generated | ADR-0009 section 5 names the parser replacement as part of the same commit |
| ADR-0004 said four commands appear that must carry `--json`; the plan adds six | corrected to six |
| ADR-0006 claimed its clause was the only place an answer was stated, while ADR-0004 states it too | reworded to say both records state it deliberately and the adapter applies one test |

Two further findings were left as recorded rather than folded, because they are
observations about the state of the tree and not conflicts between records: the
published documentation test keys three of its thirteen cases on a fenced block
that the skill split will replace (named in ADR-0009 section 5), and the cited
measurements of the audit are reproducible by command rather than by stored
artifact (the convention in `docs/adr/README.md`).

## What this record does not do

It does not start round 1, and it decides no behaviour of its own. The plan is
the order; the records are the decisions; this file records that both took
effect on the date above and on what evidence.

## Evidence

- `docs/overhaul-baseline-audit.md` — the measured baseline (764 lines).
- `docs/overhaul-baseline-audit-gate.md` — verification of that baseline, 14 bars.
- `docs/overhaul-decision-gate.md` — first consistency gate, bars 6 and 7 failed.
- `docs/overhaul-decision-gate-reverify.md` — narrow re-verification, 16 of 18 items closed, the rest folded above.
