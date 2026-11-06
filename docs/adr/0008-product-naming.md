# ADR-0008: the product name

- Status: proposed (awaiting owner ratification)
- Date: 2026-11-07
- Deciders: owner (ratification), architecture review
- Supersedes / Superseded by: --

## Decision in one paragraph

The tool keeps the name `code-indexer` for this phase and is not renamed to `codewright` or anything else. The name is not the defect: the description is, and a rename would arrive in the same release that changes read behaviour, exit codes and the MCP surface, turning one migration into three that cannot be told apart in a release note. The proposal's own rule already forbids renaming as part of the first implementation phase without owner approval, and the availability check that would let the alternative be evaluated at all (repository, package index, executable, existing projects and marks) has not been made -- so the alternative is not yet a decision, it is a name. The rename cost is recorded here as an observable inventory rather than a worry, so that a later decision starts from a count.

## Context

1. **What a rename would touch inside the repository.** Measured over the tracked tree (artifact `runs/milestone0/probe-naming-inventory.txt`, produced by `runs/milestone0/probe_naming_inventory.py`): 315 tracked files, of which 202 contain the name, for 1,441 occurrences. About 60 percent of that is run evidence and the evaluation corpus, which are not product surface: excluding `runs/` and `eval/`, 89 tracked files carry 582 occurrences.

| Surface | Files | Occurrences |
|---|---|---|
| source under `src/` | 19 | 72 |
| tests | 48 | 305 |
| `docs/` | 13 | 100 |
| README, the skill, AGENT and CHANGELOG | 4 | 91 |
| `scripts/` and `pyproject.toml` | 3 | 12 |
| `.gitignore` and `uv.lock` | 2 | 2 |

2. **What a rename would touch that a search does not show.** The package directory `src/code_indexer/`, the import name `code_indexer`, the distribution name and the version in `pyproject.toml`, two console scripts (`code-indexer` and `code-indexer-mcp`), the MCP entry point those scripts point at, the GitHub repository, the changelog history, and the installed skill directory whose name is the command agents are told to run.
3. **What a rename would touch outside the repository.** The household convention document names the tool 13 times in normative protocol text, which means a rename is not a code change but a protocol change reported to the owner and applied deliberately. Beyond that, the tool's state directory (`~/.code-indexer`, holding every project's manifest and the registry), one environment variable that names the binary, and any client configuration copied from the block published in `README.md`. How many of those copied blocks exist outside the repository is not known to this record, and it is a gap rather than a zero.
4. **The proposal's rule.** Section 9 recommends `codewright` as the preferred new name, lists the checks that must precede any adoption -- repository and package-name availability, executable conflicts, existing projects and trademarks, how the name reads in agent instructions -- and then states the operative constraint: do not rename the repository, package or executable as part of the first implementation phase unless the owner explicitly approves it.
5. **The description is false in a way the name is not.** `pyproject.toml:4` calls the tool "Semantic code search for agents: one-shot CLI + MCP server over Qdrant + Ollama (Linux)". ADR-0001 makes that sentence false when it lands, because the core stops being semantic and stops requiring either service. The name `code-indexer` survives that change unharmed; the tagline does not.
6. **Renaming is a one-way door at a bad moment.** The first phase changes what a read command does (ADR-0003), what a query failure exits with (ADR-0003), and which MCP tools exist (ADR-0007). A rename in the same release makes every migration note ambiguous: a reader cannot tell whether a broken script is a renamed executable or a changed exit code.

## Decision

### 1. Keep the name for this phase

The distribution, the import package, the console scripts, the repository, the state directory and the environment variable keep their names. No alias is added and no deprecation is announced, because a deprecated alias for a name nobody has asked to change is noise.

### 2. What changes instead

The description and the documentation are allowed to broaden before the name does. `pyproject.toml`'s description is corrected in the round that makes it false (ADR-0001's), and the README's opening paragraph states what the tool does now: read code, and prepare precise edits to it. The wording of that sentence is not decided here.

### 3. What would have to be true for a rename to be worth it

All five, in this order, and none of them is a matter of taste:

1. the CLI surface has stopped changing -- no new command or changed exit code in a release cycle;
2. the phase's behaviour migrations have shipped and been absorbed, so a release note can be about one thing;
3. `codewright` clears the checks the proposal itself lists: GitHub repository name, package index name, executable name on the platforms this runs on, and a search that finds no existing project or mark in the same space;
4. the compatibility plan in section 4 is accepted before the first rename commit;
5. the owner approves it explicitly, which is the proposal's own condition and this record's as well.

### 4. The transition shape, if it is ever approved

- **Add before removing.** New console scripts `codewright` and `codewright-mcp` ship alongside the existing pair; the old scripts print one deprecation line on stderr and otherwise behave identically for one minor release.
- **Import name lags the product name.** The distribution becomes `codewright` while the import package stays `code_indexer` for that release, so no test, skill or script that imports it breaks on the same day.
- **State migrates, and the tool says so.** The index root is renamed with a marker file recording the move; an existing root is read through a compatibility path for one release and then left behind. Manifests are not rewritten: they are per-project SQLite files whose location is the only thing that changes.
- **The environment variable is accepted in both spellings** for the same release, with the old one warned about and the new one preferred.
- **The convention document changes once, at the end,** when the old command is removed, so agents are not told two true things for a release.
- **Removal happens in a deliberate breaking release**, per the proposal's section 9.

### 5. The cost, stated as what would be edited

89 tracked product files and 582 occurrences, two console scripts, one distribution name, one package directory, one repository, one state directory, one environment variable, 17 documentation and skill files, 13 normative mentions in the household convention document, and an unknown number of copied client configurations. That is the inventory a future decision should be priced against, and it is deliberately not converted into an estimate of effort.

## Requirements this record resolves

- **The naming question the proposal's section 9 raises**, answered for this phase rather than deferred silently: keep, with the conditions under which that answer changes.
- **Agreement with the proposal's operative constraint**: no rename in the first implementation phase without explicit owner approval. This record is the written form of that constraint, and it states the approval conditions rather than assuming they will never be met.
- **Disagreement with the proposal's recommendation:** `codewright` is a better word for the product the proposal describes and a worse decision for this phase, because the phase's users are mid-migration on behaviour and the name is the one thing that has not broken. A rename is a marketing decision wearing a refactoring costume; it should be taken when the product is stable enough for the name to be the most interesting thing about a release.
- **A second disagreement, with the framing that the name "undersells" the tool:** a name undersells nothing that a description cannot fix, and the description is measurably false today. Fixing the false statement is the action the evidence supports.

## Alternatives considered, and why rejected

- **Rename now, in the first implementation phase.** It fails the proposal's own rule, it compounds three migrations into one release, it needs an availability check nobody has run, and it converts a code change into a protocol change across the household.
- **Rename at the end of the plan, as a reward for finishing.** Tempting and still wrong on the same evidence: the checks in section 3 are not scheduling questions, and committing to a rename before they are run would make the plan's last round conditional on an unknown.
- **Rename only the description and leave `code-indexer` forever.** That is this decision plus a permanent refusal, and the refusal is not justified -- the conditions in section 3 may well be met one day.
- **Rename the executables only, keeping the package.** The two names would then disagree in every error message and every skill instruction, which is the worst of both.

## Consequences

- Nothing breaks, nothing is deprecated, and no user has to learn a second name during the release that changes what commands do.
- The description becomes inaccurate-then-corrected rather than renamed; the correction is a one-line change in the round that earns it.
- A future rename is more expensive than it would be today by exactly the growth of the surface -- which is the honest cost of waiting, and the reason the inventory is written down now.
- Tests that must exist: none for this record, and that is stated rather than implied. A naming decision with tests would be a renaming decision.

## What this record does not decide

- The package namespace, the repository name, or any renaming action itself. Those are the actions section 4 describes and the conditions section 3 gates.
- The wording of the corrected description, or of the README's opening paragraph.
- Whether the new name, if adopted later, is `codewright` rather than one of the other candidates -- the availability check decides among them, not this record.
- Trademark or package-name reservation, which is an owner action outside the repository.
