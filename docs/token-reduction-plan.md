**High-value CLI subcommands to add** (focused on reducing the amount of text agents read):

### Highest priority

| Subcommand | Purpose | Why it saves tokens |
|------------|---------|---------------------|
| **`skeleton` / `map` / `outline`** | Emit a compact structural map of the project (or a subdirectory/file): file paths + imports + public signatures / class hierarchies / exports only. No function bodies. | Gives the agent a full orientation layer in a few hundred–few thousand tokens instead of exploratory reads. Classic 90%+ reduction pattern. |
| **`list-symbols` / `symbols`** | List symbols (optionally filtered by type, path, or name pattern) with file + line + signature only. | Replaces broad searches + full-file reads when the agent just needs “what exists where”. |
| **`file-outline` / `outline-file`** | For one file: top-level declarations, signatures, and brief docstrings only. | Lets the agent decide whether to call `get-code-context` at all. |

### Strong secondary additions

| Subcommand | Purpose | Why it saves tokens |
|------------|---------|---------------------|
| **`architecture` / `overview`** | High-level summary: languages, top packages/modules, entry points, key classes, dependency hotspots. | One cheap call answers “how is this repo organized?” without any source. |
| **`exports` / `public-api`** | List only public/exported symbols per file or module. | Filters noise; agents rarely need private helpers for navigation. |
| **`callers` / `callees`** (or richer graph view) | Structured call-graph edges for a symbol (already partially covered by `find-references`, but a dedicated compact graph output helps). | Avoids repeated reference + context round-trips. |
| **`snippet` / `signature-only`** | Like `get-code-context` but returns only the signature + first docstring line (or a one-line summary). | Even tighter than the existing line-range tool. |

### Useful supporting commands

- **`tree` / `file-tree`** — directory tree with optional symbol counts or language tags (cheap structural overview).
- **`deps` / `imports`** — who imports whom (file- or module-level), compact.
- **`compress-context`** (or a flag on existing tools) — return AST-minified / body-stripped versions of retrieved chunks.

### Recommended implementation style
- Default output should be **dense and token-efficient** (one symbol per line, no fluff).
- Support `--json` for structured consumption and a human-readable default.
- Keep the same project resolution (`--project` / `--name`) and `--skip-stale-check` as existing commands.
- Prefer reading from the existing SQLite manifest + symbol index rather than re-parsing source when possible.

The single highest-ROI addition is a **`skeleton` / `map`** (or `outline`) command. Almost every successful token-reduction system for coding agents starts with giving the model a cheap structural map before any full source is read.