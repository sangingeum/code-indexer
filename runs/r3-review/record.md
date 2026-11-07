# Review record — the read-surface round: one flag, one argument shape

Base `105484f`, review tip `8e7fde7` (`origin/main`, tracked tree clean).
Implementation `ddf5521` `feed305` `c5e5570` `c316468` `9c29da3`; fix
`099c896` `4257200` `43fec69` `3bff398` `14b512d` `5d98f9d`; records `6430168`
`8e7fde7`. The two QA bars are read as of record, not re-run. Everything
below that is not a gate figure was measured here; the commands and their
outputs sit in `live-invocations.txt`, masked (`<checkout>`, `<home>`).

## Verdict

**CHANGES REQUESTED — documentation only, no code change.** The shipped
behaviour is what the round says it is: `--version` prints the installed
version, `--format` resolves in one place, the compact set is honest, the
symbol reads take one shape, and the one invocation that changed is named in
the entry. What does not hold is the round's own subject — that a document
must not claim more than the identity behind it. Two of the six references
still state the *opposite* of the shipped conflict rule, the entry's closing
sentence contradicts its own exception twelve lines above it, and the compact
roster is a promise held by hand in four documents and one frozenset with no
derivation joining them. Three sentences and one table row fix all of it; see
*Fold-now items*, each with the exact edit.

| axis | verdict |
|---|---|
| standards | two false normative sentences, one overbroad claim, one hand-held roster, one info on the `--json` residue |
| design | the surface is right; keep `--format`, keep `--json`, keep `compact`; the narrowing is the simplest honest rule; two contract notes, no blockers |

## Axis 1 — standards

### The claims the round added, each tested

I swept every normative sentence the round added to the entry document
(`SKILL-CLI.md`), `skill/references/{machine-output,query,semantic,indexing,plans,failures}.md`
and `CHANGELOG.md`, and tested each against the shipped behaviour.

| # | claim (shortened) | where | verdict |
|---|---|---|---|
| 1 | `--version` prints the installed package version, one line, exit 0 | `SKILL-CLI.md:72` | **holds** — `code-indexer 0.1.0`, exit 0; equals the distribution version by the gate's import check (`runs/r3-gate/record.md`, bar 1) |
| 2 | `--format text\|json` on *every read command that takes `--json`* | `SKILL-CLI.md:73` | **holds for the class, hides a residue** — 19 of 22 `READ_COMMANDS`; the other three (`deps`, `eval`, `eval-compare`) take no `--json` at all, and the 9 non-read holders are covered by finding 6 |
| 3 | the pair is accepted when the spellings agree and is exit 2 when they disagree | `SKILL-CLI.md:73`, `machine-output.md:37-41`, `CHANGELOG.md:10-15` | **holds** — measured: `--json --format json` exit 0 with the payload; `--json --format text` exit 2, one stderr line, stdout empty |
| 4 | *passing both is `ArgumentError`, exit 2* | `query.md:110-112`, `semantic.md:56-58` | **FALSE at the tip** — see finding 1 |
| 5 | compact exists on exactly these seven commands | `SKILL-CLI.md:74`, `machine-output.md:52-58`, `query.md:113-117`, `CHANGELOG.md:19-21` | **holds, set for set** — see the two specific claims below |
| 6 | refused elsewhere with exit 2, naming `text\|json` | `SKILL-CLI.md:74`, `machine-output.md:61-64` | **holds** — `find-references --format compact` → `has no compact form; supported formats: text\|json`, exit 2 |
| 7 | compact is built only from fields the text form already prints, under normalized names | `machine-output.md:45-70` | **holds** — every compact token is a payload field or a value; the label mapping (`loc`, `directory`, `share`, `hotspot-fan-in`, `fan_in`) is stated and exhaustive. It also drops text fields: finding 5 |
| 8 | `find-symbol` compact keeps a capping or ambiguity note on its own line | `machine-output.md:56` | **holds** — the note is emitted outside the format branch (`cli.py:2286-2290`) |
| 9 | *every invocation that worked keeps its answer* | `CHANGELOG.md:29` | **FALSE as written** — see finding 2 |
| 10 | an ambiguous name is `AmbiguousSymbolError`, an `ArgumentError`, exit 2, every candidate listed | `query.md:118-121` | **holds** — `errors.py:57` subclasses `ArgumentError`; gate bar 5 measured two candidates with file, range, kind, container and `decl_hash` |
| 11 | `--version` changes no subcommand's own options | `machine-output.md:44` | **holds** — 33 helps at both trees, the whole diff is the added `--format` block (gate bar 1) |
| 12 | a `FILE` keeps its meaning, scoping `--symbol` to a range inside it | `SKILL-CLI.md:77-79` | **holds** — measured in gate bar 5(c) and by the three-way identity of `mod.py`, `mod.py --symbol`, `mod.py --start-line/--end-line` |

### (a) the compact set: docs versus code, derived independently

Code side, derived from the registration rather than from the doc:
`COMPACT_FORMAT_COMMANDS` (`command_classes.py:76-84`) lists seven names, and
each has a renderer — `overview` (`cli.py:267`), `skeleton` (`cli.py:307`),
`outline` (`cli.py:324`), `find-symbol` (`cli.py:2268`), `find-definition`
(`cli.py:2374`), `index-status` (its own single line, `cli.py:1311-1313`), and
`semantic-search` (`core.py:1354`, in the shared search renderer, not in
`cli.py`). No command outside the set has a compact branch: every `fmt ==
"compact"` site in `cli.py` is one of the six, and the only one in `core.py`
is the search renderer. The two hidden aliases resolve to their canonical
name (`cli.py:2034` passes `command="skeleton"`, `cli.py:2082` passes
`command="outline"`), so `map --format compact` and `file-outline --format
compact` work although the aliases are absent from the frozenset — correct,
and worth knowing before someone "fixes" the set by adding them.

Doc side: `SKILL-CLI.md:74`, `machine-output.md:52-58` (a seven-row table),
`query.md:114-116` and `CHANGELOG.md:19-20` all list the same seven names.
**The sets are equal, and I derived them by two independent routes.** The
claim is true today. What makes it a finding is that nothing keeps it true:
finding 4.

### (b) the conflict rule: message versus documented rule

Documented (`SKILL-CLI.md:73`): the pair is accepted when it agrees
(`--json --format json`) and is exit 2 when the two name different formats.
Shipped (`cli.py:250-252`):

    ArgumentError: --json and --format text disagree: --json is an alias of
    --format json; pass one of them

Same rule, same meaning, word for word in substance: *agreement passes,
disagreement is a usage error naming itself, and the alias relationship is the
reason given.* The message does not say exit 2 or `ArgumentError` by name, but
it is the taxonomy line for a usage error and the exit code is 2 — the docs
carry the number, the message carries the reason. **Agrees.**

Precedence inside `_output_format` is: unknown value, then disagreement, then
missing compact form. So `find-references --json --format compact` reports the
disagreement, never that compact is unsupported — the right order (one
contradiction at a time), but undocumented; harmless, noted for the next
round that touches this function.

### Finding 1 — medium, blocking (fold-now): two references state the old rule as current

`skill/references/query.md:110-112`:

    alias of `--format json`: both spellings render the same bytes. Passing both
    is `ArgumentError`, exit 2, rather than a silent precedence -- one invocation,
    one answer format.

`skill/references/semantic.md:56-58`:

    `--json` is its exact alias and passing both is `ArgumentError`, exit 2

Both are false at `8e7fde7`: the agreeing pair exits 0 with the payload
(`live-invocations.txt`). The fix commit `43fec69` repaired the entry, the
machine-output page and the changelog, and its message says the rule "is now
stated once" — it is stated five times, twice in the superseded form. Severity
is medium rather than low because these references are the pages a reader
lands on for the two commands the round changed most (`query.md` owns the
symbol reads; `semantic.md` owns the one command where the rule actually
changed, since it alone had `--format` at base), and because the wrong version
is the stricter one: a caller following it refuses a pair that works.

### Finding 2 — low, fold-now: the entry's closing sentence contradicts its own exception

`CHANGELOG.md:29` ends the entry with "every invocation that worked keeps its
answer", twelve lines after `CHANGELOG.md:14-16` says the one pre-existing
combination this round changes is `semantic-search --json --format text`, which
used to let `--json` win and now refuses. The exception is honestly stated, so
the summary sentence is the defect: it is exactly the overbroad claim the
programme keeps paying for. One clause repairs it (fold-now item 3).

### Finding 3 — medium: the compact roster is held by hand in five places and derived in none

`COMPACT_FORMAT_COMMANDS` (`command_classes.py:76-84`) is a literal
`frozenset`, the four doc lists are prose or a markdown table, and the only
test over it is

    def test_the_compact_set_is_a_subset_of_the_read_commands():
        assert COMPACT_FORMAT_COMMANDS <= READ_COMMANDS

(`tests/test_output_format.py:420-421`). That is the weakest assertion in the
round. The refusal direction is sampled on five commands, three of them named
by hand (`test_compact_is_refused_where_there_is_no_compact_form`,
`tests/test_output_format.py:352-366`), so of the 15 read commands that should
refuse, 10 are unpinned; and nothing ties the doc's seven names to the
frozenset's seven. Add a member without a renderer and the CLI accepts the
value and silently renders text — the exact failure mode (a claim stronger
than the identity behind it) that this round exists to remove, sitting in the
one set the round did not derive. The parity roster for `--json` was derived
this round (finding 8); the compact roster was not. Fix direction in fold-now
item 5; no behaviour changes.

### Finding 4 — low, fold-now: a compact record is not the text record, and no page says so

Measured on one symbol:

    find-symbol build_overview --format compact
    src/code_indexer/overview.py:60-83 build_overview (function)
    find-symbol build_overview
    - <checkout>::src/code_indexer/overview.py:60-83 build_overview
      (function, confidence=exact, evidence=ast)  def build_overview(...)

The compact record drops `confidence`, `evidence` and the signature — three
fields the text form prints, one of which (`confidence`) is the document's own
semantic discriminator between an AST and a heuristic row. `machine-output.md`
says compact is built *only from* fields text prints, which is true and
one-directional; a reader is left to infer the projection. One sentence in the
compact table closes it.

### Finding 5 — info: the residue behind "every read command that takes `--json`"

28 commands accept `--json`; 20 accept `--format`. The eight write commands
(gate finding 2, accepted) plus `sweep-orphans`, whose default invocation is
the read shape and which is classified as a conditional write
(`command_classes.py:40-42`), are the nine. The doc's quantifier is accurate
over the class `READ_COMMANDS` — but that class is defined in
`command_classes.py` and nowhere in the entry document, so a reader who types
`sweep-orphans --format json` gets `No such option` from a command the entry
table lists as "write (with --apply)" one page over. Not a defect of the
round's chosen scope; one word ("read" → "read-class") would cost nothing if
the entry had the budget.

### Terminology

Clean. `git diff 105484f 8e7fde7` added lines under `SKILL-CLI.md`,
`CHANGELOG.md`, `skill/`, `src/`, `tests/` contain no `G3`/`M1`/`B2`-shaped
token and no round label used as a concept name; a case-insensitive sweep for
`r3`, `round3` and `round-3` over the added lines returns nothing. The
`runs/r3-*` directories are captures and evidence paths, not code, file names
in the product tree or identifiers — they carry provenance the way a commit
hash does, and they are not counted here. New identifiers read as concepts:
`COMPACT_FORMAT_COMMANDS`, `_format_overview_compact`, `_resolve_symbol_file`.

### Budgets

| document | base | gate tip `9c29da3` | review tip `8e7fde7` | budget |
|---|---|---|---|---|
| `SKILL-CLI.md` | 115 lines / 7149 B | 120 / 7645 | **120 / 7810** | 120 lines / 8192 B |
| `skill/references/query.md` | 292 lines | 323 | 323 | 400 lines / 24576 B |
| `tests/test_output_format.py` | — | 459 | 550 | none |

**Ruling: the spend was right; the next addition must be a subtraction or a
split, and it must not be a budget increase.** The five lines bought the whole
flag surface and the one argument shape in the place a reader opens first, and
replaced per-command restatements that were already drifting; that is the best
line-per-byte the entry document can buy. But it landed at exactly 120 lines
with 382 bytes (4.7%) of headroom — 95.3% of the byte budget spent. The gate's
7645 is true for its own tip and is not the number to quote now.
Recommendation, not a fold: the generated roster table (`<generated:roster>`)
is the block to evict into a reference when the next line is needed; splitting
the entry itself is a programme decision, not this round's.

### Record hygiene of the two gate records

- No board id, no internal profile name in either record: a sweep for
  `T-0[0-9]{4}` and for the household's profile names returns nothing in
  `runs/r3-gate/record.md` or `runs/r3-fix-reverify/record.md`. Both cite
  commits and commands, as they should.
- Figures spot-checked against their own commands: the gate's "120 lines, 7645
  bytes" reproduces at its own tip (`git show 9c29da3:SKILL-CLI.md`, above);
  its "459 lines" for the new test module reproduces; the re-verify's
  "registered_commands=33" reproduces (`rg -c '^@app.command'` = 33) and its
  "read_roster=22 / derived_pair_roster=19 / without deps, eval, eval-compare"
  reproduces from `command_classes.py` and `test_output_format.py:61-78`. Both
  records state their own tips, so the gate's 7645 and the review tip's 7810
  are both true — the review quotes the latter.
- One figure I could not reproduce without re-running a bar: the pass counts
  (1049 at the gate, 1067 at the fix, +18 new ids). The arithmetic is
  consistent (1067 − 1049 = 18); the run is the gate's, not mine, and one
  figure was checked against a static source instead.

## Axis 2 — design

### Finding 5 (design) — `--format` is the right surface; keep all three of them

The survey asked one flag shape and the round answered with a flag on 20 read
commands plus a permanent `--json` alias. Judged on the three questions:

- **Both spellings forever: keep.** The alias is not a courtesy, it is
  enforced: `FORMAT_ROSTER` is recomputed from the typer registration and the
  parity case asserts stdout, stderr and exit equality for all 19 holders
  (`tests/test_output_format.py:61-78, 235-249`). A permanent alias that is
  tested to be byte-identical is cheaper than a deprecation window, and
  `--json` is the spelling every agent and script in this house already uses.
  What is *not* enforced is the alias's declaration: 26 hand-written
  `typer.Option(False, "--json", ...)` declarations, in three different help
  strings (20 "JSON output.", 4 with no help, 2 bespoke). Behaviour cannot
  diverge while the parity test holds; the help text and the option's
  presence can. One shared option constant would remove the last of it — a
  refactor, not a fold.
- **Is `compact` an honest third mode?** Yes, with one caveat. It shares the
  *data* and nothing else: `overview` has a second renderer
  (`cli.py:267-304`) beside `overview.format_overview`; `skeleton`, `outline`,
  `find-symbol` and `find-definition` each carry an inline compact branch
  beside an inline text branch; `index-status` is the text line by
  construction. Every one iterates a *hand-written field list*
  (`lang['files']`, `d['symbols']`, `h['fan_in']`), so a field added to the
  payload reaches compact only if someone edits the compact function too —
  and finding 4 is that drift, already materialized once (confidence,
  evidence, signature). The honest statement is that compact is a *named
  projection*, not a reformatting, and the docs should say which fields are
  outside it. I checked the truncation worry and it does not hold: the
  section caps in compact (`[:5]`, `[:6]`) are the same caps the text renderer
  applies, so compact drops no row that text keeps.
- **Ruling: keep as shipped. Do not deprecate `--json` later; do not collapse
  `compact` into `text --quiet`.** The collapse is not even available: at base
  `semantic-search --format compact` already worked (the command and `deps`
  were the only `--format` holders before this round), so removing the mode
  would break a pre-existing invocation — the one thing this round was
  required not to do. And `--quiet` would have to mean "one line per record"
  on five commands and "the same line" on a sixth; that is a third mode with a
  worse name.

### Finding 6 (design) — the narrowed conflict rule is the simplest honest one

The rule the tip implements is one sentence: *the two spellings must agree;
when they do not, the invocation is refused naming the contradiction.* That is
better than the alternative it replaced (refuse every double spelling), because
that alternative refuses `--json --format json`, which base answered, and
worse than nothing only in that one base invocation
(`semantic-search --json --format text`) still changes behaviour. The gate
named it; the entry names it; the residue is a genuine contradiction (the two
flags asked for different formats) whose resolution by silent precedence was
the defect. Does the pair being accepted or refused *depending on the value*'
leave a worse inconsistency? No: acceptance is not keyed to a value, it is
keyed to *agreement*, which is the one property a caller can reason about
without knowing the implementation. **Keep.**

The one loose end is not the rule but its reporting: the disagreement is
reported before "this command has no compact form" (finding 1(b)), so
`--json --format compact` on a command without a compact form tells the caller
to pass one flag, and the second attempt tells them compact does not exist.
Two errors instead of one; acceptable, and worth one clause if the function is
touched again.

### Finding 7 (design) — the argument shape: the contract holds, the card's words do not

- **No locator at all:** both commands refuse as usage errors, exit 2, stdout
  empty, and the message names both ways out:
  `get-code-context needs a FILE positional, or --symbol <name> to resolve the
  symbol project-wide`; `find-definition needs a NAME positional, or --symbol
  <name>`. Measured here, and pinned by
  `tests/test_output_format.py:458-464, 493-498`.
- **`--symbol` is the positional, byte for byte** where it is an alias:
  `find-definition --symbol X` and `find-definition X` print the same line.
- **The ambiguity is resolved by the flag, not moved.** `get-code-context
  --symbol X` with no FILE resolves project-wide through the manifest and
  refuses a duplicate with every candidate (`cli.py:332-345`,
  `identity.resolve_one`); with a FILE the same flag means *inside this file*.
  Two different resolutions behind one flag. The docs say so ("a FILE keeps
  its meaning, scoping `--symbol` to a range inside it"), the tests exercise
  both, and no fallback silently bridges them — but the flag's meaning is
  decided by arity, which a caller can only learn from the doc. If this
  surface grows a third locator, that is the line at which `--symbol` should
  split into `--in-file` and `--symbol`.
- **The card's own description does not match what shipped**: it calls
  `--symbol` "an alias for the positional file" on `get-code-context`. It is
  not an alias — the FILE positional keeps its meaning, and `--symbol` is a
  resolver that *becomes* an equivalent of the positional only when the
  positional is absent. The shipped shape is the better one and the docs
describe it correctly; I record the mismatch because the next reader of the
card will expect an alias.

### Finding 8 (design) — the parity test's identity: sound, with one blind spot

Read at `tests/test_output_format.py:61-78` and `:226-235`. The roster is
recomputed at import from `typer.main.get_command(app)` — each command's
`params` are scanned for an option whose `opts` contain both `--json` and
`--format`, intersected with `READ_COMMANDS` — and the drift test asserts both
directions: `FORMAT_ROSTER - PARITY_ARGV` (`missing`) and `PARITY_ARGV -
FORMAT_ROSTER` (`stale`). So:

- *a new `--json` command with no parity case* → non-empty `missing`, red. It
  bites for the right reason, and the re-verify proved it by adding one; more
  to the point, the set is not a list a developer can forget to update.
- *a command that stops accepting `--format`* → it leaves `FORMAT_ROSTER` while
  its `PARITY_ARGV` entry remains, so `stale` is non-empty, red. This direction
  is the one a hand-maintained roster would have lost, and it is covered.
- *a command that silently loses its compact form* → **not** covered by the
  derivation: `COMPACT_FORMAT_COMMANDS` is not derived from anything, and the
  only test over it is a subset assertion (finding 3). The seven commands have
  one compact test each, so a *removal of a renderer* goes red for those seven;
  an *added member without a renderer* stays green. That asymmetry is the
  finding, not the mechanism: the roster that the round derived is the flag
  roster, and the roster it left hand-held is the compact one.

### Finding 9 (design) — the residue: two rulings

- **Eight write commands that take `--json` and refuse `--format` (gate finding
  2, accepted).** *Acceptable residue; no card needed yet.* The round scoped
  itself to reads and says so in all four places; a write command's answer is a
  one-line receipt plus its JSON, and "text|json|compact" on a command that
  writes a file buys a format nobody asked for. The card's bar read wider than
  the card's scope; the gate reported that honestly instead of widening the
  round. Revisit only if a caller actually asks — and note `sweep-orphans` is
  the ninth holder and the one with a real read shape (finding 5), which is
  where I would spend the next flag.
- **`semantic-search --format compact` is documented and unmeasured here** (no
  embedding service, `vectors=off`). *Acceptable residue, with a carded
  follow-up.* Its renderer exists and is the only compact branch outside
  `cli.py` (`core.py:1354`, one line per hit `path:start-end symbol score`),
  and the command refuses before rendering on this host, so the doc's compact
  description for it is the round's one untested promise. Two consequences: it
  is the compact member no test exercises (the six compact tests name the
  other six), and `semantic.md:61` states its record shape ("in compact, as
  the symbol column") with nothing behind it on any host in this house. Card
  it as "measure the compact hit line where embeddings exist"; do not fold a
  code change on an unmeasured path.

## Fold-now items

Documentation only; no behaviour, no schema, no reindex. I did not land them
here because the skill-copy digest test pins the installed copy of these
references to the tracked copy, and syncing that copy is outside this review's
fence — the dev leg lands them with the sync it already runs.

**1. `skill/references/query.md:110-112`** — replace the three lines

    alias of `--format json`: both spellings render the same bytes. Passing both
    is `ArgumentError`, exit 2, rather than a silent precedence -- one invocation,
    one answer format.

with

    alias of `--format json`: both spellings render the same bytes. Passing both
    is accepted when they agree (`--json --format json` asks the same question
    twice) and is `ArgumentError`, exit 2, when they name different formats --
    never a silent precedence, one invocation, one answer format.

**2. `skill/references/semantic.md:56-58`** — replace

    `--json` is its exact alias and passing both is `ArgumentError`, exit 2
    (see [machine-output.md](machine-output.md)).

with

    `--json` is its exact alias; passing both is accepted when they agree and
    is `ArgumentError`, exit 2, when they name different formats (see
    [machine-output.md](machine-output.md)).

**3. `CHANGELOG.md:29`** — replace "no reindex, and every invocation that
worked keeps its answer." with "no reindex, and every invocation that worked
keeps its answer except the one combination named above."

**4. `skill/references/machine-output.md`**, in the compact table, one sentence
after the table: "A compact symbol record carries the file, the range, the name
and the kind; the text form's `confidence`, `evidence` and signature are not
part of it — read `--format json` when those are the question."

**5. Carded, not folded: derive the compact roster.** In
`tests/test_output_format.py`, replace the subset assertion with a case
parametrized over `sorted(READ_COMMANDS)`: a member of
`COMPACT_FORMAT_COMMANDS` must exit 0, a non-member must exit 2 with
`has no compact form; supported formats: text|json`, and a new test must
compare the seven names listed in `SKILL-CLI.md:74` with the frozenset. That
makes the compact promise the same kind of claim as the flag promise.

## What I did not verify

- **`semantic-search --format compact`'s payload.** No embedding service and a
  `vectors=off` index here; the command refuses before rendering. I read its
  renderer; I did not run it.
- **The write commands' refusal as I would measure it.** The message is from
  the gate's and the re-verify's captures; my own invocation surfaced usage
  text through a pipe that swallowed the exit code, so I quote theirs.
- **Both QA bars.** Verdicts and counts are of record. I reproduced only the
  static figures in them (finding: record hygiene, above) and did not re-run
  the suite, the mutation set or the parity matrix.
- **CI internals.** I report the conclusion for my own push; I did not read
  the earlier runs' logs.
- **`changed-symbols`, `plan-show`, `plan-validate` payload shapes.** Read from
  the gate's bar 2 as measured on their refusal paths; not re-measured here.
- **The `--json` help-string inconsistency** (26 declarations, three texts) is
  reported from a static count, not from 26 `--help` outputs.
