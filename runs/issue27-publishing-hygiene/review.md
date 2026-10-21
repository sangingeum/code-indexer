# Two-axis review — publishing hygiene (issue #27)

Review round, read-only on code. Date: 2026-10-21.
Branch `main`, tip `7de9059`, tree clean apart from local untracked captures.
Commits reviewed, all `%G? = G`:

| Commit | Subject |
|---|---|
| `fc2af93` | docs(hygiene): remove internal names and card ids from published records |
| `d851d52` | chore(hygiene): enforce the publishing rules with a lint script and test |
| `6be8d83` | docs(runs): verification gate record for issue #27 |
| `7de9059` | fix(hygiene): narrow the path exemption and derive the second helper's prefixes |

Base for the diff: `6d4f740`. Gate of record:
`runs/issue27-publishing-hygiene/gate-verification.md` (PASS, 10/10 bars). It was
not re-run here; its numbers are ruled on as record. The gate's tip was
`d851d52`, so its evidence covers everything except the correction leg, which is
inspectable only at the tip and is treated as a claim to check, not a fact.

## Surface reviewed

Eleven published records neutralised (`fc2af93`), the mechanical check
(`d851d52`: lint, offline test, pre-commit hook, brief and changelog), and the
correction leg (`7de9059`: narrowed path exemption, second helper rewritten).
Whole-round file surface confirmed with `changed-symbols --base 6d4f740`:
eleven files in `fc2af93`, five in `d851d52`, four in `7de9059`, and the only
files touched twice are the lint, its test and the brief. No file under `src/`
and no schema change: the tool's behaviour is out of scope and untouched.

## Standards axis

The design is right. A publishing rule that lives only in prose is a rule
nobody runs; making it a script, an offline test and a commit hook is the
house's tooling-first posture, and the digest decision is the interesting part.
The findings below are about where the mechanism draws its lines, not about
whether it should exist.

### Surface split: right design, wrong mechanism, fail-open

The line itself is drawn where it belongs. Capture data under `runs/` is quoted
evidence about a studied repository; redacting it would falsify the record, and
a check that demanded it would be wrong. Names and card ids are authored
material and are governed in every authored suffix — including Markdown under
`runs/`, which is exactly where the residue was.

The implementation, however, is not the surface split it documents. It is a
suffix whitelist:

- `_is_authored` returns True only for `.md .py .sh .yml .yaml .toml .cs`, and
  then additionally requires `not _is_capture(rel)`.
- `_is_capture` is True only for `runs/**` with `.json .txt .log`, and those
  three suffixes are not in the authored set. The intersection is empty, so the
  `not _is_capture(...)` clause can never change the result. It is inert code
  that reads as if the directory anchored the rule. It does not.

Probed live (`--root <probe>`, two files, same card-id-shaped token): a
Markdown record under `runs/` is reported; a `.txt` capture outside `runs/` is
not. The important half holds; the other half is a suffix fact wearing a
directory's clothes.

The cost is a fail-open hole in exactly the direction a hygiene check cannot
afford. 126 of the 278 tracked files are outside the authored suffix set —
`.txt` 73, `.json` 28, `.err` 11, `.scm` 8, four with no extension, one
`.jsonl`, one `.lock` — and none of them is governed by the name or card-id
rule. The extensionless four include `.githooks/pre-commit`, an authored script
by any reading. A new record format (`.rst`, `.html`), a captured run left in
`.err` or `.jsonl`, or a note committed as `.txt` outside `runs/` publishes
internal material with the check green and nothing said about it.

The fix is cheap and I measured its price: a fail-closed rule (govern every
tracked text file; exempt only real capture data, i.e. `runs/**` with a capture
suffix) flags **zero** files in the tree today, because all 126 currently
unchecked files are clean. It also needs `.err`, `.out` and `.jsonl` in the
capture suffix set, so the exemption covers capture data as it is actually
spelled. This is Fold 1, and it is the one finding I would land before the
check is next relied on as a gate.

### The digest list

Eleven entries; all unique, all 64-hex lowercase, and each resolves. I
recomputed candidates independently of the gate: eleven resolve to real names
(nine role profiles, the orchestrating role's role word, and the local build
account), and three plausible non-roster candidates correctly match nothing.
The list is not vacuous, and the gate's bar 1 claim is accurate.

The trade is right for a single-owner repository. Names in cleartext would make
the check a searchable roster for anyone reading the script; digests keep the
prohibition without the list. Two honest costs remain:

1. **Silent failure at add time.** A digest typed by hand or pasted from the
   wrong buffer matches nothing for ever, and nothing says so — the tree stays
   green, the name is not forbidden, and only a reader who recomputes will
   notice. `--hash-name` prints the value but cannot add it, so the human step
   is the fragile one. The cheap mitigation is inside the repo: assert every
   entry is a valid 64-character lowercase sha256 and that the set is at least
   the roster size it was introduced with, so a truncated or corrupt paste is a
   red test rather than a quiet hole.
2. **The roster is partly published anyway.** The suite builds its positive
   fixture as a two-fragment concatenation of one real role name. The lint
   cannot see it, and a reader can. That is the exact property the digests
   exist to protect, spent to test one call path. The mechanism can be pinned
   without naming anyone: inject a synthetic digest into the module's set and
   assert the matching path fires.

### The path exemption

The narrowing closed the real hole. `$` was the concrete case — machine
accounts end in it — and it is now reported, alongside `+`, `,`, `-` and `_`;
the tests pin each. The remaining exemption is one predicate: a segment that
*begins* with `[`, `{` or `<` is pattern text. That is a coherent rule with one
sentence of semantics rather than three special cases.

`[` and `{` have no user in the tree at the tip. That is not the same as dead
surface: at the gate tip the `[` branch had exactly one live user — the second
helper's own home-path regex, whose character class begins with `[`. The commit
that removed that line is also the commit that kept the branch. A rule whose
last user was deleted one commit ago, in the class of file that keeps producing
this shape (a committed redaction helper whose literals contain the paths it
rewrites), is a rule worth keeping. `{` is the same predicate and costs no
implementation. Keep both; the alternative is a second exemption mechanism
added later under pressure.

### The synthetic segment set

It reads as an accumulation, and the comment is the reason: it explains the
single-letter pair (the watcher tests' `x`/`y` roots) and nothing else. Five of
the seven entries have no user anywhere in the tree — they are the universal
fixture conventions. Enumeration is still the right shape (the gate's ruling is
correct: a "single character means synthetic" rule would fail open on real
single-letter accounts), but the constant should say what it is: fixture
account names the house allows, with the one project-specific pair called out.
One comment, no behaviour change — part of Fold 1, which touches the same file.

### The two helper refactors

The reusable part should **not** move next to the lint. These two are record
artifacts: their rules are shaped by the paths of the round they served
(`/tmp/ci22-*`, the five fixed target files), one of them has already run its
only job, and promoting a spent one-off into `scripts/` would create a
"reusable" sanitiser whose behaviour nobody has reviewed as a utility — the
gate already flagged that its pytest rule leaves a `/pytest-N/` tail behind the
placeholder. Keeping them where the record lives is the honest home.

Three consequences of growing them do need naming:

- **Two idioms for the same root.** `parents[2]` on `__file__` in one,
  `parents[2]` on `HERE` in the other. Both resolve correctly at the tip (I
  imported both and compared against the checkout). Correctness is accidental
  to the idiom, not protected by it — a move rewrites the wrong root silently.
  Acceptable for a record artifact; it is the reason they must not be imported
  as libraries by anything new.
- **Two different home-path boundaries.** One helper rewrites a bare home prefix
  with no boundary; the other added a lookahead. Same concept, two rules,
  written a commit apart. Unify on the boundary form (Fold 4).
- **A record became load-bearing for CI.** The suite imports
  `runs/issue22/reverify/sanitize_capture.py` by path. That file can now never
  be retired, moved or trimmed without editing the test suite. It is not a
  defect today; it is an obligation that nobody wrote down (Fold 2, docstring
  line).

### Test quality

Run-time fixture assembly does not weaken the assertions. Every expected string
is built by the same helper that builds the input, the comparisons are exact
(`== [f"record.md:1: absolute-host-path: {home}"]`), and the parametrised
account-shape table is the best thing in the file: it pins `$`, `+`, `,`, `-`,
`_` and a non-leading bracket, which is precisely the behaviour the narrowing
bought. Readability suffers a little — a reader has to evaluate concatenations
— but that is the price of a file that must pass its own check, and it is
stated in the docstring.

The gap is coverage of the surface line itself. Nothing pins that a Markdown
record under `runs/` is governed. The card-id capture case uses
`runs/capture.json`, which passes because `.json` is not authored, not because
the file is a capture — so the suite would stay green if `_is_authored` started
treating all of `runs/` as exempt, which is the one regression that would
re-publish the eleven records this round cleaned. That is Fold 2, together with
the roster name in the fixture and the digest-format assertion above.

Using the private `_scan(path, rel)` from the tests is acceptable here: the
script is a single-purpose module, and testing through `main()` alone would
mean building a tree on disk for every case.

### The hook, and where enforcement actually lives

The hook is real: the gate proved it blocks a violating commit and lets a clean
one through, and it passes staged paths through the documented `--paths`
interface. But `core.hooksPath` is unset in this checkout, and nothing in the
repository sets it — so in any given clone the hook is opt-in, not enforcement.
What actually enforces the rule on every push is CI: the offline test is
unmarked, sits in `tests/`, and the workflow runs `pytest -m "not live"`, so a
tracked violation fails the build. `AGENT.md` and the changelog describe the
activation command but frame the hook as the gate; a reader can reasonably
conclude the repository enforces it. One sentence fixes the framing: CI
enforces through the suite, the hook is the local early warning. That is
Fold 3.

Two smaller notes on the same surface. The hook calls `python3` directly rather
than the project environment, which is correct only while the lint is stdlib
and dependency-free (it is) — worth the same sentence so a future import does
not silently break the hook. And the documented lint command stops at
`src tests`, so `scripts/` is outside both the house lint and the CI ruff step;
the new script passes the default rule set but shows two style findings
(`E501`, `UP031`) when it is brought into scope. Fold 5 extends the scope to
`scripts`.

## Spec axis (fidelity to issue #27)

The issue asks for two things: the residue cleaned, and a systematic method so
it stays clean. Both are delivered.

**The cleaned set matches the corrected inventory, not the original
understated one.** At the base commit, an independent sweep for the three
classes over authored suffixes finds internal names in six files and card ids
in seven; two files carry both, so the union is eleven — exactly the eleven
files `fc2af93` touches. The corrected comment's larger counts (17 name files,
205 hits) are explained by the same comment's own argument: most name hits are
file names inside captured output of the studied repository, which the split
keeps as evidence. Nothing in the authored set was left behind.

**Both cited inventory items are closed at the tip.** The three test docstrings
were reworded to neutral phrases with no loss of meaning, the design doc's
author and target became role words, and the one committed helper the issue
names now derives its prefixes from the running checkout instead of spelling
them. The round also found and fixed a second helper carrying the same class of
literal (`runs/issue22/reverify/sanitize_capture.py`) — inside the class, not
outside the issue. Note the timing: that second path residue was still present
at the gate tip and is closed only by `7de9059`, which is why this review rules
on the tip rather than on the gate's snapshot.

**The neutralisation removed identifiers, not record.** Spot-checked the
`runs/issue22/review.md` and `sanitize_evidence.py` hunks of `fc2af93`: a card
id became "the board card that carries this leg", `claimed_by: <role>` became
"claimed by the dispatching role", a docstring attribution became "review
finding". Status, the read-only claim, the no-write claim, counts and verdicts
all survive. No capture payload was touched: the eleven-file list is seven
Markdown records, one docs file, the one helper, and two Python test modules —
no `.json`, `.txt` or `.log`.

**Scope.** Nothing in the round exceeds the issue: the governing comment
promises exactly the lint, the offline test, the digest design and the hook,
plus the brief and changelog record. No schema change, `EXTRACTION_VERSION`
untouched, no file under `src/`. Nothing the issue asked for is missing.

**The round's own records pass the check they introduced.** My own run over the
tracked tree at the tip: `scanned=278 skipped=0 violations=0`. The gate record's
own dogfood (twelve files, clean) stands; the review record you are reading is
held to the same bar before it is committed.

## Fold list

Numbered, atomic, for a separate leg. None blocks the issue's two asks; folds 1
and 2 should land before the check is next trusted as a gate.

1. **Surface classification fails open** — `scripts/publishing_hygiene_lint.py`:
   make `_is_authored` the complement of `_is_capture` (govern every tracked
   text file except capture data) instead of a suffix whitelist; add `.err`,
   `.out`, `.jsonl` to `CAPTURE_SUFFIXES`; update the module docstring to state
   the rule as "everything except capture data"; and rewrite the
   `SYNTHETIC_SEGMENTS` comment to say "fixture account names, with the
   watcher tests' single-letter pair called out" rather than explaining only
   the pair. Measured cost today: zero new violations across the 126 files the
   current rule does not govern.
2. **The suite does not pin the surface line** —
   `tests/test_publishing_hygiene.py`: add a case that a Markdown record under
   `runs/` carrying an internal name is flagged; change the capture-side
   fixture from `runs/capture.json` to `runs/capture.txt` so it pins the
   exemption by suffix-as-capture rather than by not being authored; replace
   the concatenated roster name with a synthetic digest injected into the
   module's set, so the test pins the matching path without publishing a name;
   assert every digest entry is 64 lowercase hex and that the set is at least
   the roster size it was introduced with; and name, in the sanitiser test's
   docstring, that it imports a historical record artifact on purpose.
3. **The hook's enforcement role is overstated** — `AGENT.md` and
   `CHANGELOG.md`: one sentence each saying CI enforces the rule through the
   offline suite and `.githooks/pre-commit` is the opt-in local early warning,
   and that the hook's `python3` call is valid only while the lint stays
   stdlib.
4. **Two helpers, two home-path rules** —
   `runs/issue22/sanitize_evidence.py`: give the home rule the same boundary as
   the other helper, as a negative lookahead (`(?![A-Za-z0-9._-])`) rather than
   the shipped positive one (see ruling 1), so "this host's home" means one
   thing in both files.
5. **`scripts/` is outside the lint scope** — `.github/workflows/ci.yml` and
   the `AGENT.md` lint line: `uvx ruff check src tests scripts`, so the new
   script is held to the same bar as the code it gates.

## Rulings on the leg's four open questions

**1. The `<home>` boundary lookahead `(?=/|\s|["']|$)` — narrow enough to
keep?** No, but not because it is too narrow: it is narrow in the wrong
direction. It protects a neighbouring account (`/home/name2`) and pays for that
with silent misses in the places paths actually end in prose — `,`, `)`, `:`,
`;` and a closing full stop. A miss here is under-redaction, which publishes
the very path the helper exists to remove. The negative form satisfies both
constraints in one line: `(?![A-Za-z0-9._-])`. Keep the intent, change the
form (fold 4). As a historical helper that has already run on its captures the
shipped form is harmless; it must not be reused as a utility as written.

**2. `REPO_ROOT = HERE.parents[2]` is a fixed depth.** Acceptable here, and I
checked it rather than reasoned about it: both helpers resolve to the checkout
at the tip (`parents[2]` is correct for a file three levels down from the
root). It is acceptable precisely because these are record artifacts pinned by
path, not libraries: nothing new should import them, and the test that does is
the exception I am asking to be documented (fold 2). If either is ever copied
for reuse, the copy must find the root by walking up to a marker
(`pyproject.toml`), not by counting parents.

**3. A segment beginning with `<` is exempted unconditionally.** Acceptable,
and it is the one exemption I would defend hardest. Angle brackets are this
house's published spelling for host-specific text — the gate record, the issue
comment and this record all depend on it — and a real machine path cannot
begin with `<` on any host the repository is built on. The rule is doc-level
in the sense that it governs prose as much as code, which is the right level
for a convention about how records are written. State it that way in the
docstring when fold 1 touches it, rather than leaving it as a bare predicate.

**4. The `[`-leading exemption has no current user.** Keep it. It had a live
user one commit before the tip — the second helper's own home-path regex, whose
character class opens with `[` — and that commit deleted the line while keeping
the branch. A future committed helper that sanitises paths will produce the
same shape, and the alternative (dropping it now, adding a second exemption
when it bites) is worse than a predicate that already covers it. This is not
dead surface; it is the exemption's own history.

## Verdict

**Accepted. No standards veto; the issue's two asks are delivered and
faithful.** The residue is gone at the tip and the cleaned set is exactly the
set that was violating; the mechanical check is real, offline, CI-enforced, and
cannot pass vacuously on the class it was built for; the gate's ten bars hold
and its two findings were routed and fixed at the tip rather than inherited.

The reservations are about shape, not substance. The surface split is
documented as a directory rule and implemented as a suffix whitelist, leaving
126 tracked files outside the name and card-id rules — including the hook
itself — and the suite does not pin the half of the split that this round's
eleven records depended on. Folds 1 and 2 close both before the check is next
trusted as a gate. Folds 3 to 5 are honesty and scope: the hook is opt-in and
CI is the enforcer, the two helpers should agree on one home rule, and the
script belongs inside the lint scope that gates the rest of the repository.

Method note: this record was reviewed by a different agent than the one that
authored the round, the gate numbers were ruled on rather than re-run, and the
independent checks here are five: the base-time inventory sweep, the live
surface-line probe, the digest resolution count, the fail-open cost measurement
across the 126 files, and the tracked-tree scan at the tip.
