# ADR-0009: the agent surface -- skill layout and command documentation

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

The documentation an agent loads is split into one small entry document and reference files that are read on demand: the entry document carries the decision tree (which command answers which question), one line per command saying whether it is a read or a write, the exit-code taxonomy and the staleness rule, and a named trigger for each reference; every command's flags, examples and failure modes move into a reference file under `skill/references/`, one per concern, loaded only when that trigger fires. The split is bounded by measurement rather than taste -- the entry document may not exceed 120 lines and 8 KiB, and each reference has its own published budget -- the tracked copy is the only source and the installed skill is a copy whose digest the documentation test pins, and each reference's command sections are generated from the command registry so a flag cannot be documented that does not exist.

## Context

1. The entry document is the largest thing an agent loads before it does anything. `SKILL-CLI.md` is 613 lines and 39,797 bytes (`wc -lc SKILL-CLI.md`), and the installed skill directory holds a copy of it.
2. A single help page is already larger than the project's whole instruction file: `code-indexer watch --help` is 7,376 bytes over 84 lines, against `AGENT.md`'s 4,283 bytes over 73 lines. Flags are accurate in `--help` and are hand-copied into the document afterwards, which is where they drift.
3. Nothing compares the two copies. The installed skill is a copy of `SKILL-CLI.md`, and no test in the suite reads the installed directory: the copy is correct on the day it is made and unverified on every day after.
4. The proposal's section 8.2 asks for a small entry document with on-demand references and gives the reason -- an agent's context is the resource being spent -- but no record in the set decides what the entry document contains, what a reference contains, who refreshes the installed copy, or how drift is caught. The plan's round 5 carries the split as a deliverable, so the deliverable has no grant until this record exists.
5. `README.md` points at `SKILL-CLI.md` five times, and the parity table ADR-0007 shortens lives in both documents. One fact in two homes is the shape that produced the "all 14 tools" drift ADR-0007 section 5 closes with a test.
6. What an agent needs before it can choose a command is small and stable: the question-to-command mapping, which commands write, what an exit code means, and whether the answer it is about to trust describes the index or the tree. Everything else -- a flag's default, one operation's example, the plan workflow's four steps, a provider's failure modes -- is needed after that choice, if at all.

## Decision

### 1. The entry document

`SKILL-CLI.md` stays where it is and keeps its name, so the README's five links and the installer's path keep working. It carries, and carries nothing else:

- the decision tree: one row per question shape, question to command;
- the command roster: every registered command with its read-or-write class (ADR-0003 section 1) and one line on when to reach for it -- the twenty-seven commands that exist plus the four plan commands the plan adds;
- the exit-code table of ADR-0003 section 5, with the rule that `ErrorType` on stderr is the semantic discriminator and the number is the coarse branch;
- the staleness rule in three sentences: `fresh`, `stale`, `unknown`; `unknown` is not `fresh`; `--refresh` is the only repair a read offers;
- the read-only guarantee in one sentence, naming that a read never writes the manifest, the vector store or the sources;
- the reference index: one line per reference file, and the trigger that should make an agent open it.

**Budget: 120 lines and 8 KiB**, measured by the documentation test and printed when it fails. A topic that does not fit belongs in a reference; a command that does not fit means the roster line is too long, not that the budget grows.

What leaves the entry document and does not come back: per-command flag lists, per-command examples, the plan workflow in full, provider behaviour, and the MCP parity table (which belongs in `README.md`, where the human who installs the extra reads it).

### 2. The reference files

One file per concern, under `skill/references/`, each addressed by a trigger named in the entry document:

| Reference | Contents | Trigger in the entry document |
|---|---|---|
| `indexing.md` | `add-project`, `index-more`, `remove-project`, `reindex-project`, `sweep-orphans`, `watch`, `unwatch`: flags, defaults, two examples each, what each writes | the task is to build or repair an index |
| `query.md` | `overview`, `skeleton`, `outline`, `find-*`, `get-code-context`, `deps`, `changed-symbols`, `map`, `file-outline`: flags, defaults, examples | a query answered the wrong shape and its flags need reading |
| `plans.md` | the four plan commands, the plan document's members, validation, preview, apply, the receipt and recovery | the task is to change code through a plan |
| `machine-output.md` | `--json` shapes, schema versions, the staleness block, one `jq` recipe per shape | an agent parses output instead of reading prose |
| `failures.md` | every exit code with its `ErrorType`s, the cause and the remedy for each | a command failed and the remedy is not obvious from its line |

Budget per reference: 400 lines and 24 KiB. Each reference opens with the records that govern it (ADR numbers as provenance), because a reference that restates a decision without saying where it is decided cannot be corrected when the decision changes.

### 3. What is generated and what is written

- **Generated**: each reference's per-command sections -- name, arguments, flags with defaults, the read-or-write class, the JSON shape -- come from the command registry, the same registry the CLI builds its commands from. A hand edit inside a generated block fails the documentation test, and the generator is the only writer of that block.
- **Written**: the prose -- when to use a command, worked examples, the remedy paragraphs, the failure narrative. Prose that could be generated would be a template, and a template that guesses is worse than a sentence a person wrote.
- **Not duplicated**: a flag documented in a reference is not restated in the entry document; the roster line names the command and its class and stops there. The exit-code table exists once, in the entry document, and `failures.md` expands it rather than repeating it.

### 4. Who owns the installed copy

The tracked `SKILL-CLI.md` and `skill/references/` are the only source. The installed skill directory is a copy, refreshed by whoever cuts a release from the checkout: the release step installs the skill after the documentation test has passed, so the copy is never ahead of the tree it was made from and never behind it by more than one release.

- An agent that finds the installed copy out of date reports it; it does not edit the installed copy, because an edit there is a fork the next install silently deletes.
- The copy lives in one directory tree with its references, so the entry document's trigger lines resolve inside the installed skill and not only inside the checkout.

### 5. What the drift test covers

`tests/test_docs.py` grows six checks, and each one is a defect class that has already happened somewhere in this repository or in ADR-0007's own history:

1. **Roster equality**: the entry document's command roster equals the registry, names and read-or-write classes both.
2. **Index closure**: every reference the entry document names exists, and every file under `skill/references/` is named by the entry document -- no orphan reference and no dead trigger.
3. **Budgets**: the entry document is at most 120 lines and 8 KiB; each reference at most 400 lines and 24 KiB. The failure message prints the measured size against the budget.
4. **Taxonomy coverage**: every exit code of ADR-0003 section 5 appears in the entry document's table and in `failures.md`.
5. **Generated blocks**: each generated command block equals the generator's output for that command, so a flag added to the registry and not regenerated fails the suite instead of ageing.
6. **Installed-copy digest**: when the installed skill directory exists on the machine, its digest equals the tracked digest; when it does not, the check is skipped with the reason printed, because a clean checkout and a CI runner have no installed skill and a test that fails there teaches nobody anything.

The MCP tool list stays a separate check (ADR-0007 section 5), in the same module, because it is a different document with a different owner of truth.

## Requirements this record resolves

- **The proposal's section 8.2** (a small entry document plus on-demand references) in full: sections 1 and 2 are the split, section 3 is how the two halves stay honest, section 4 is the copy's ownership, section 5 is the drift test the proposal does not ask for but the split needs.
- **The plan's round 5 skill deliverable**, which no record granted before this one: the split, the budgets and the test are all scheduled there.
- **Disagreement with the proposal's framing of the skill as documentation**: it is not a document, it is the load path an agent pays for on every task, and a load path needs a budget the way an interface needs a contract. A documentation standard would have said "keep it clear"; this record says 120 lines and 8 KiB and makes the suite enforce it.
- **Disagreement with ADR-0007 section 5's placement of the parity table**: the table leaves both the skill and the README's prose for a single generated list in the README, because a CLI agent never needs it and the human who installs the extra always does.

## Alternatives considered, and why rejected

- **Keep one document with a table of contents.** Table-of-contents navigation still loads the whole file; 613 lines of it were loaded to answer a question that twenty lines answer.
- **Document nothing and send agents to `--help`.** Help is accurate and unsearchable across commands: an agent would have to know which of thirty-one commands to ask before it could learn which one it wanted.
- **References without an entry document.** The agent then guesses at a filename, which is a worse index than a table.
- **Generate the whole skill from the registry and docstrings.** The command blocks can be generated; the decision tree and the remedies cannot without a template that guesses at intent.
- **Publish the skill only, and drop the tracked copy.** The installed copy would then be the only source, and the repository would document a tool it no longer describes.
- **Set the budget by review rather than by the test.** A budget nobody measures is a preference, and the file being replaced grew to 39,797 bytes under review.

## Consequences

- The entry document drops from 613 lines and 39,797 bytes to at most 120 lines and 8 KiB; the detail survives in five references, at the cost of one extra read when it is actually needed.
- Two copies of the documentation exist (tracked and installed), and a test now pins them instead of a release note promising that they agree.
- `README.md`'s five links to `SKILL-CLI.md` keep working, and the parity table moves to `README.md` alone.
- A command added to the registry without a roster line fails the suite, so the entry document cannot lag the CLI by a release again.
- Tests that must exist: roster equality, index closure, both budgets, taxonomy coverage, generated-block equality, and the installed-copy digest with its skip case. `tests/test_docs.py` carries thirteen tests today; the six checks extend it rather than starting a second module.
- A reference that grows past 400 lines is split at its next concern boundary, not exempted.

## What this record does not decide

- The wording of the entry document or of any reference, and the names of the questions in the decision tree.
- Whether the skill is published to a package index, and how a release step performs the install -- the release process owns that, and this record owns only the rule that the copy follows the tree.
- The MCP tool documentation beyond the parity list's home; ADR-0007 section 5 governs its content.
- Whether an agent surface other than this skill ever exists (a prompt library, an MCP resource, a generated codebook).
