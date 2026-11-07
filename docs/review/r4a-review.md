# R4a review — a withheld file is a skip, not a stale reason

Card T-000121 · review of card T-000102 · base `143d85f`, code tip `219fd61`,
evidence `95fa312` (runs/r4a-gate/, 64 captures).

Reviewer: solomon. The two axes were run separately (standings against the
record and the documents; spec against the governing records and the survey)
and aggregated here. The QA gate is of record: its ten bars were not re-run,
and its three non-blocking findings are disposed of at the end.

**Verdict: changes-requested.** The design is right — derive the skip rather
than store it, re-apply the same detector, report beside the verdict, never
name the material — and every one of those claims is true in the code except
where the fold table says otherwise. But the round introduces one documented
schema-contract violation (a key whose meaning changes), one false-fresh in a
documented configuration (`--allow-sensitive`), and one budget accounting bug
that re-creates the exact wallpaper this round exists to remove. Two of the
three are proven by execution below, not by reading.

---

## Axis 1 — standings, claim by claim

### 1. "The same detector decides the pass and the read"

**Partly false: there is not exactly one decision function. There are two
compositions of one predicate pair, and they disagree in a corner.**

- The predicates are shared: `sensitive.filename_is_sensitive` (:72-76),
  `sensitive.content_is_sensitive` (:79-81), `sensitive.file_is_sensitive`
  (:84-90).
- The pass composes them at `indexer.py:166-178`:
  `allow_sensitive = manifest.get_meta("allow_sensitive") == "1"`, and only
  inside `if not allow_sensitive:` is `file_is_sensitive` consulted.
- The read path composes them again at `staleness.py:167-183`, calling the two
  predicates directly and never consulting `allow_sensitive`
  (`core.py:536-542` reads only `all_files()` and `scope_include`).

Proved divergence (probe, real pass, real manifest):

```
A allow_sensitive: fresh  reasons []  skipped [('newsecret.py', 'sensitive content')]
A after pass:     newsecret row? True  state idle
```

On a project registered with `--allow-sensitive` (documented at
`skill/references/indexing.md:291-293`, tested at
`tests/test_sensitive_exclusion.py:190`), a secret-bearing file created after
the last pass is reported **`skipped` on a `fresh` verdict** — and the very
next pass writes its row. At the base commit that same tree reported `added`
and `stale`, which is the truth. The change therefore *regresses* this
configuration from a true stale verdict to a false fresh one, in exactly the
direction the record says a persisted skip list would fail (runs/r4a-staleness.md:46-52).

Record sentence that is false as written: "the content layer reads the same
prefix the pass reads, so the two cannot disagree about a file"
(staleness.py:155-156; repeated at runs/r4a-staleness.md:36-38 and
indexing.md:309-310).

Related, smaller: the prefix length is a literal in two places —
`SENSITIVE_READ_CHARS = 65536 * 4` (staleness.py:53) and `fh.read(65536 * 4)`
(indexer.py:174). Nothing keeps them equal.

**Encoding, unreadable file, filename-vs-content — checked, and these agree:**
both sides open with `encoding="utf-8", errors="replace"` and `read(262144)`
(indexer.py:172-174 / staleness.py:175-176); both treat `OSError` as "not a
content sensitive" (pass keeps the file, read marks it `added`), and a
filename-layer hit short-circuits identically on both sides. A secret past the
256 KiB prefix is invisible to both — verified: such a file is indexed and the
read says `fresh`, no skip (`C late-secret: indexed? True state fresh`).
That agreement is a privacy property of the pass, not of this round; noted,
not filed here.

### 2. Budget semantics

Three cases, as the code actually behaves:

- **A path beyond the 8 MiB** → `added` reason, `stale` verdict
  (`staleness.py:171-173`). Verified: `B budget: stale reasons [('added',
  'a_big.py'), ('added', 'z_secret.py')] skipped []` — with a 12 MiB new file
  sorted first, a genuinely sensitive second file is reported `added`.
  The gate saw the same shape at scale (runs/r4a-gate/REPORT.md:76-80: eight
  content-sensitive files past the budget report `added`). The degradation is
  conservative and it is *honest about staleness*; it is not honest about
  *why*, and it re-creates the noise the round removes.
- **A prefix that cannot be read** → `added` (`staleness.py:177-179`). Same
  outcome as base; a permanently unreadable rowless file therefore keeps a
  permanent banner (the pass cannot write a row for it either,
  `indexer.py:353-355`). Deferred — see fold table D1.
- **A tree where every path is new** → every path is classified until the
  budget is spent, then the rest are `added`; the verdict is `stale`, never a
  silent fresh. The honest-`added` claim is the code's, and it holds.

**But the budget is debited by file SIZE, not by bytes read**
(`staleness.py:180`: `budget -= f.size or len(text)`). The read is at most
262144 characters; a 12 MiB candidate is charged 12 MiB against an 8 MiB
budget after only its prefix was opened. So one large new file disables
classification for every path that sorts after it, and the constant
`SENSITIVE_RECHECK_BYTES` does not mean the number of bytes examined.
The docstring's claim that the constant "caps the total examined"
(staleness.py:159-160) and the record's "reads at most 8 MiB and then stops
classifying" (runs/r4a-staleness.md:108-110) are loose in the one direction
that matters: 8 MiB of *prefixes* is up to 32 such files
(`8 MiB / 256 KiB = 32`), and the code usually examines far fewer.

### 3. Skipped beside the verdict, and the JSON shape

- Never a reason: true. `detect` builds `reasons` only from `added`,
  `deleted`, `modified` (`staleness.py:133-139`), and `skipped` rides as its
  own field on the verdict (:142-146).
- Present on a fresh verdict: true (`block()` always emits the three fields,
  `staleness.py:222-225`); text `index-status` appends the phrase beside the
  rest of the line (`cli.py:1249-1252`).
- **JSON key names and shapes vs `docs/json-schema.md`: they match the
  block's own documented shape exactly — and they collide with an existing
  key in the same object.** `docs/json-schema.md:4` states the contract:
  "Field names are stable; new fields may be added (append-only), but existing
  names never change meaning." `semantic-search --json` already had
  `skipped: [{project, reason}]` and `skipped_count` = *its length*
  (`docs/json-schema.md:10-19`, built at `core.py:1333-1340`). The staleness
  block is then merged over that object with
  `payload.update(staleness_block or {})` (`core.py:1341`), so the block's
  `skipped_count` silently overwrites the fan-out count while the fan-out
  `skipped` list survives. Proved:

```
skipped (projects): [{'project': 'a', 'reason': 'budget'}, {'project': 'b', 'reason': 'budget'}] len: 2
skipped_count: 1
COLLISION: True
zero-skip case: len(skipped)=1 skipped_count=0 collision: True
```

  Two different quantities now share one key on the most-used read surface,
  the documented relation `skipped_count is its length` is false even when
  there are **zero** sensitive skips, and `partial: true` with
  `skipped_count: 0` is a combination a consumer will misread. This is the
  round's own changed lines (`staleness.py:222`, `:283`), so it is the round's
  to fix; renaming the three block fields is the cheap, append-only-compatible
  answer.

### 4. The answer carries no material

The code cannot print the material: `SkippedFile` holds `file` and a constant
phrase only (`staleness.py:66-76`), the phrases are module constants
(`sensitive.py:96-97`), `skip_reason_text` returns one of the two constants
(:100-102), and the `OSError` path discards the exception without printing it
(`staleness.py:177-179`). `text` never leaves `_classify_skipped`. Verified on
the fixture strings: the JSON block carries the path and `sensitive content`,
not `AKIA`, not `PRIVATE KEY` (tests and gate both). The path itself *is*
printed for a filename-layer skip (`secrets/prod.json`) — that is the design
("path and reason only"), not a leak.

### 5. "A read still writes nothing"

True. `_classify_skipped` opens for read only (`staleness.py:175`), writes no
cache, sets no attribute; `core.staleness` opens the manifest `writable=False`
(`core.py:536`). The only new side effect is reading source prefixes, which is
the point. The record's `--json` capture and the gate's byte-identity probe
agree with the code.

---

## Axis 2 — spec

Governing set read: `docs/adr/0003-read-only-and-staleness-contract.md`,
`0004-edit-plan-and-apply-lifecycle.md` (checked and excluded, see below),
`docs/overhaul-plan.md`, and
`~/dev/dispatches/surveys/code-indexer-2026-11-07/SYNTHESIS.md`.

### 1. The staleness contract — the record that owns it is ADR-0003, not ADR-0004

The dispatch memo attributed staleness to ADR-0004; that is wrong.
ADR-0004 is the edit-plan/apply lifecycle, which mentions staleness only as
`PlanStale` (:89). **ADR-0003 owns the staleness contract.**

Against ADR-0003, this change:

- **Keeps** §1's read-only sentence (line 10) and §5's exit-code table
  (61-68): no new write, no new exit code, `index-status` still exits 0.
- **Keeps** §2's "three verdicts, never a boolean guess" (line 35): `skipped`
  is not a verdict; `state` is still one of three.
- **Is consistent with** §3's "additive, `schema` stays 1" (line 39), but
  §3's field table (41-47) is now incomplete — and the key it gained collides
  with another object's key (Axis 1 §3).
- **Contradicts** §2, bullet 2 (line 34): "Detection compares ... using the
  scanner's existing include and exclude rules and **stat calls only**;
  **content is hashed only for files whose stat changed**." Both clauses are
  now false for rowless paths: detection opens and reads up to 256 KiB of
  each. The same bullet's budget sentence ("the walk has its own budget so
  that a very large tree degrades to `unknown`") does not cover this new
  budget, which degrades to `added` instead.

**This is an owner call, and I am not writing the amendment.** My
recommendation to the owner: amend ADR-0003 §2 bullet 2 with one clause —
"for a scanned path with no manifest row, the detector is re-applied from the
path and a bounded prefix of its content, under a separate budget" — and add
the three new fields to §3's table once their names are settled (Axis 1 §3).

### 2. Is `skipped` a fourth state or a report beside the verdict?

**Beside is the honest shape, and the change implements it honestly — for a
consumer that reads the block, not for one that reads `state`.** Judged on
the evidence: a withheld file is not stale (the index is exactly what the pass
intends), so inflating the verdict to a fourth state would be wrong; but the
verdict `fresh` alone does not carry the fact. The block does, on every read
answer, and that is enough *provided the consumer rule is written down where
consumers look*. Today it is written in `docs/json-schema.md:196-200`
("so `nothing changed` and `one file withheld on purpose` never read alike")
but **not** in `skill/references/indexing.md`, which is the surface an agent
loads. The sentence the reference needs, at indexing.md:203-207:

> `stale: false` with `skipped_count > 0` is not "nothing was withheld"; a
> consumer that branches on `stale` alone learns nothing about skipped files.
> They are not stale, and they are not in the index.

### 3. The 8 MiB budget — principled, or a guessed number?

**It is a guessed number, and the reference states it but not why.** 8 MiB has
no measured basis in the record: the measured cost of the common case is
27 µs for one 9,961-byte read (runs/r4a-staleness.md:107-110). 8 MiB is a
round number that admits at most 32 files at the full prefix (262144 chars)
and unboundedly many smaller ones — i.e. it bounds bytes, not work, and not
latency. As a stop-the-world guard that is defensible; stated as policy it
needs the one sentence the reference lacks: it is an approximation chosen so a
read cannot cost like a pass, not a measured threshold.

**At the boundary it is not acceptable silently.** A user with a 10 MiB new
tree gets `stale` whose reason for the withheld file is `added` — a claim
about the tree that is really a claim about the budget. The verdict must say
so: either a `skipped_unexamined_count` in the block, or one clause in
`skipped_summary`/`detail`. Without it, the reason is indistinguishable from a
genuine add, and the round's own promise ("degrades to honest `added`")
is honest only to the code. With it, the degradation is both conservative and
legible, and I would accept it.

Two further things follow from the same reading: debit the budget by bytes
actually read (fold B3), and do not let one file's size consume the whole
allowance.

### 4. Cost vs signal

The survey's ask is "the stale banner states once, and stays complete in JSON"
(SYNTHESIS.md:100) against banner wallpaper (SYNTHESIS.md:47-51, vera: "the
warning is constant here, so I no longer treat it as a signal"). This change
answers it for the common case — one withheld file, quiet banner, complete
JSON — without making a genuine stale read quieter: `added`, `modified`,
`deleted` are decided exactly as before, and the negative controls in
`tests/test_sensitive_skip_staleness.py:111-127` pin that. The two places
where it makes the truth *quieter* than it is are the `--allow-sensitive`
false fresh (Axis 1 §1) and budget truncation (Axis 1 §2) — both are folded
below, and neither is a reason to reject the shape.

---

## The gate's non-blocking findings, disposed

1. **`last_pass` prints a raw dict** (REPORT.md:106) — pre-existing, in
   `cli.py:1168-1170`, outside this diff's changed lines, already carded.
   **Not this round.**
2. **The plural line `2 files skipped (2 sensitive content)`** (REPORT.md:109-111)
   — in this round's changed text (`staleness.py:194-199`). **Fold now**: the
   numeral should be omitted whenever a single reason category accounts for the
   whole count, which removes the plural's clumsiness and also removes the
   `count == 1` special case that produced the singular form.
3. **The read path can read up to 8 MiB of prefixes** — accepted as bounded
   and honest *for the bytes read*; the bound as implemented is charged in file
   sizes and does not disclose truncation. Fold B3 + S3 make the bound mean
   what the finding claims.

---

## Fold table (ranked, blocking first)

| # | file:line | exact change | disposition |
|---|---|---|---|
| B1 | `staleness.py:222,283`; `core.py:1341`; `docs/json-schema.md:4,10-19,181-186` | Rename the block's three fields so no existing name changes meaning: `skipped_count/files/summary` -> `withheld_count/files/summary` (or nest them under one key). Update `docs/json-schema.md`, `skill/references/indexing.md:203-206,311-312`, `SKILL-CLI.md:103-105`, `CHANGELOG.md` (skip paragraph), `cli.py:1251`, and the six tests. Proved: `skipped_count` overwrites the fan-out count on `semantic-search --json` and is wrong even with zero skips. | **fold now** |
| B2 | `core.py:536-542`; `staleness.py:167-183` vs `indexer.py:166-168` | Read `allow_sensitive` with the scope at `core.py:539`, pass it into `detect`/`_classify_skipped`, and when set classify nothing (every rowless scanned path is `added`, which is what the pass will do). Add a test mirroring `tests/test_sensitive_exclusion.py:190`. Proved: `fresh` + `skipped` today; the next pass writes the row. | **fold now** |
| B3 | `staleness.py:180` | Debit by bytes read, not file size: `budget -= min(f.size, len(text.encode("utf-8", "replace")))`, and say so at :159-160. Proved: a 12 MiB new file makes a later sensitive file report `added`. | **fold now** |
| S1 | `staleness.py:194-199` | Emit the per-reason numeral only when more than one reason category is present (`2 files skipped (sensitive content)`; `2 files skipped (1 sensitive content, 1 sensitive filename)`). Drops the `count == 1` special case. Add the plural case to the test file. | **fold now** |
| S2 | `cli.py:1249-1252` beside `cli.py:1178-1182` | One `index-status` line now states the same fact twice in two vocabularies: `sensitive=1 files skipped (privacy; override with ...)` (last pass) and `1 file skipped (sensitive content)` (this tree). Keep one count; if the `--allow-sensitive` hint is worth keeping, fold it into the new phrase. | **fold now** (same command, same line; else defer to the text-hygiene card with S1) |
| S3 | `staleness.py:188-200,222-225`; `skill/references/indexing.md:315-318` | Disclose truncation: a count of paths left unclassified when the budget ran out (`skipped_unexamined_count`), plus one reference sentence saying such a path is an `added` reason that says nothing about its content. | **fold now** with B3; if not, **defer** to the round that owns the budget policy and **file an issue** |
| S4 | `staleness.py:53` vs `indexer.py:174`; `staleness.py:167-183` vs `sensitive.py:84-90` | One prefix constant and one composition: put the 256 KiB prefix beside the patterns in `sensitive.py` and add `skip_verdict(path, text=None)` there, used by both the pass and the diff. Then the record's "one decision" sentence becomes true. | **fold now** (rides with B2) |
| S5 | `sensitive.py:100-102` | `skip_reason_text` returns `sensitive content` for ANY value other than `"filename"`. Make the mapping explicit (`{"filename": ..., "content": ...}` / keyed lookup) so a third layer cannot be mislabelled. | **fold now** (3 lines) |
| D1 | `staleness.py:174-179`; `indexer.py:353-355` | A rowless file whose prefix cannot be read is `added` forever — a permanent banner for a file the pass can never index either. Either give it its own reported state (`reason=unreadable`) or stop charging it. | **file an issue** (not this round's defect) |
| D2 | `skill/references/indexing.md:203-207`; `docs/json-schema.md:196-200` | Add the consumer rule for item 2 of the spec axis: `stale: false` with a non-zero count is not "nothing withheld"; `stale` alone says nothing about skipped files. | **fold now** |
| D3 | `core.py:506-514` | `Core.staleness`'s docstring still describes only the walk and the hash; it now opens rowless files. One sentence, matching `staleness.py:124-127`. | **fold now** (doc is this round's) |
| O1 | `docs/adr/0003-...:34` (`§2` bullet 2) | Amend "stat calls only" / "content is hashed only for files whose stat changed" to admit the bounded prefix read on rowless paths, and add the three new fields to §3's table (`:41-47`) once named. | **owner call** — not written by me |

Nothing here asks for a redesign. B1, B2 and B3 are small, local edits to lines
this round already touched; S1-S5 and D2-D3 are the same shape of follow-through.

---

## What I did not check

The gate's ten bars (captures, byte identity, the in-process timings) are of
record and were not re-run. I did not review `runs/` documentation edits for
style beyond the claims they make, and I did not audit the pre-existing
sensitive detector's own coverage (secret patterns, prefix limit) — that is
upstream of this round.

Signed-off-by: solomon <solomon@household.local>
