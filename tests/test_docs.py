"""Documentation-drift tests (offline; part of `pytest -m "not live"`).

The agent surface is one entry document (``SKILL-CLI.md``) and six reference
files under ``skill/references/``, split and budgeted by ADR-0009. The command
blocks and the entry document's roster are generated from the CLI registry by
``scripts/generate_skill_docs.py``; prose is written by hand.

ADR-0009 section 5 decides six checks, each of which was a defect class first:

1. roster equality  -- the entry document's roster equals the registry, names
                       and read-or-write classes both
2. index closure    -- every reference named exists, every reference file is
                       named, every command is covered by exactly one
                       reference's generated sections
3. budgets          -- 120 lines and 8 KiB for the entry document, 400 lines
                       and 24 KiB per reference, measured and printed
4. taxonomy         -- every ADR-0003 exit code appears in the entry document
                       and in failures.md, and every `error_type` the code
                       declares has a row in failures.md
5. generated blocks -- each generated region equals the generator's output, so
                       a hand edit inside one fails here
6. installed copy   -- when installed in the new shape (a directory holding
                       SKILL.md and references/), its digest equals the tracked
                       digest; when not, the check skips with the reason.
                       A legacy single-file installed copy is NOT installed in
                       this shape, so it skips with that reason -- it is a
                       symlink to the entry document alone and cannot carry
                       the references the entry document's triggers resolve to.

The MCP parity list stays a separate check (ADR-0007 section 5) in this same
module: it is a different document with a different owner of truth.

A command is *covered* by a reference when that reference carries the
command's generated section, headed ``### `name```. Coverage is counted on
that heading alone, which is why the JSON roster in machine-output.md and the
remedy tables in failures.md do not make a command covered twice.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
import tomllib
from pathlib import Path

import click
import pytest
from typer.main import get_command as typer_get_command

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
ENTRY = REPO / "SKILL-CLI.md"
REFERENCES_DIR = REPO / "skill" / "references"
PYPROJECT = REPO / "pyproject.toml"
JSON_SCHEMA = REPO / "docs" / "json-schema.md"
CHANGELOG = REPO / "CHANGELOG.md"
GENERATOR = REPO / "scripts" / "generate_skill_docs.py"
ERRORS = REPO / "src" / "code_indexer" / "errors.py"

ENTRY_BUDGET = (120, 8 * 1024)
REFERENCE_BUDGET = (400, 24 * 1024)
EXIT_CODES = (0, 1, 2, 3, 4, 5)
ERROR_TYPE_HEADING = "ErrorType"

SKILL_DESC = re.compile(r"\bdescription:\s*\"(.*)\"", re.M)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_generator():
    """The generator module, loaded once under a name the suite can hold."""
    name = "generate_skill_docs"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _click_group():
    import code_indexer.cli as cli_mod

    return typer_get_command(cli_mod.app)


def _under_checkout(path: Path) -> str:
    """A path as a drift message names it -- asserted, never raised.

    ``relative_to`` raises ``ValueError`` when the path is not under the
    checkout, so a rig pointed at a copy elsewhere would report a crash
    instead of the drift it was asked about.
    """
    assert path.is_relative_to(REPO), (
        f"{path} is outside the checkout {REPO}: these checks read the "
        "tracked tree, so a copy elsewhere is not a tree they can judge")
    return str(path.relative_to(REPO))


def _walk(group, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for name, cmd in group.commands.items():
        out[prefix + name] = cmd
        if hasattr(cmd, "commands"):
            out.update(_walk(cmd, prefix + name + " "))
    return out


def _commands() -> dict[str, object]:
    """Every registered command, hidden spellings included, by name."""
    return {n: c for n, c in _walk(_click_group()).items() if " " not in n}


def _command_flags(cmd: click.Command) -> set[str]:
    """Long option names (dual spellings included)."""
    flags: set[str] = set()
    for p in cmd.params:
        for opt in getattr(p, "opts", []):
            if opt.startswith("--"):
                flags.add(opt)
    return flags

def _entry_text() -> str:
    return ENTRY.read_text(encoding="utf-8")


def _reference_paths() -> list[Path]:
    return sorted(p for p in REFERENCES_DIR.glob("*.md") if p.is_file())


def _reference_text(name: str) -> str:
    return (REFERENCES_DIR / name).read_text(encoding="utf-8")


def _surface_text() -> str:
    """The entry document plus every reference: what an agent can load."""
    parts = [_entry_text()] + [p.read_text(encoding="utf-8") for p in _reference_paths()]
    return "\n\n".join(parts)


def _generated_body(text: str, key: str) -> str:
    """The body of one generated region, markers excluded."""
    begin, end = f"<!-- generated:{key}:begin -->", f"<!-- generated:{key}:end -->"
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    return text[start:stop].strip("\n")


def _roster() -> dict[str, tuple[str, str]]:
    """command -> (class, reference file), read from the entry document."""
    out: dict[str, tuple[str, str]] = {}
    for line in _generated_body(_entry_text(), "roster").splitlines():
        row = re.match(r"^\| `([^`]+)` \| ([^|]+?) \| `([a-z-]+\.md)` \|", line)
        if row:
            out[row.group(1)] = (row.group(2).strip(), row.group(3))
    return out


def _command_sections() -> dict[str, list[str]]:
    """command -> the reference files carrying its generated section."""
    out: dict[str, list[str]] = {}
    for path in _reference_paths():
        for name in re.findall(r"^### `([^`]+)`$", path.read_text(encoding="utf-8"), re.M):
            out.setdefault(name, []).append(path.name)
    return out


def _size(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    return len(text.splitlines()), len(text.encode("utf-8"))


def _dir_digest(root: Path) -> str:
    """A digest over every file below `root`, keyed by relative path."""
    digest = hashlib.sha256()
    files = sorted(
        (p for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _tracked_digest() -> str:
    """The same digest over the tracked copy, under the installed names."""
    digest = hashlib.sha256()
    items = [("SKILL.md", ENTRY)] + [
        (f"references/{p.name}", p) for p in _reference_paths()
    ]
    for relative, path in sorted(items):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


INSTALLED_SHAPE = "a directory holding SKILL.md and references/"


def _installed_skill_dirs() -> list[Path]:
    """Where an installed skill may live, in precedence order."""
    roots: list[Path] = []
    override = os.environ.get("CODE_INDEXER_SKILLS_ROOT")
    if override:
        roots.append(Path(override))
    roots.append(Path.home() / ".hermes" / "skills")
    return [root / "code-indexer" for root in roots]


def _installed_skill() -> tuple[Path | None, str]:
    """The installed copy, or None plus the reason it is not in this shape."""
    for path in _installed_skill_dirs():
        if path.is_dir() and (path / "SKILL.md").is_file() and (path / "references").is_dir():
            return path, ""
        if path.exists() or path.is_symlink():
            return None, (
                f"{path} is not installed in this shape ({INSTALLED_SHAPE}); "
                "a legacy single-file copy cannot carry the references"
            )
    return None, f"no installed skill directory exists ({INSTALLED_SHAPE})"


# ---------------------------------------------------------------------------
# 1. roster equality with the registry
# ---------------------------------------------------------------------------

def test_roster_equals_the_registry():
    """The entry document's roster is the registry: names and classes both."""
    from code_indexer import command_classes

    roster = _roster()
    registry = _commands()
    missing = sorted(set(registry) - set(roster))
    bogus = sorted(set(roster) - set(registry))
    assert not missing, f"registered but absent from the roster: {missing}"
    assert not bogus, f"roster names commands that do not exist: {bogus}"

    expected = {}
    for name in registry:
        if name in command_classes.WRITE_ONLY_COMMANDS:
            expected[name] = "write"
        elif name in command_classes.CONDITIONAL_WRITE_COMMANDS:
            expected[name] = "write (with --apply)"
        else:
            assert name in command_classes.READ_COMMANDS, name
            expected[name] = "read"
    wrong = {
        name: (roster[name][0], cls)
        for name, cls in expected.items()
        if roster[name][0] != cls
    }
    assert not wrong, f"roster class disagrees with ADR-0003 section 1: {wrong}"


def test_roster_names_the_reference_that_carries_the_command():
    """Each roster line points at the reference whose sections carry it."""
    sections = _command_sections()
    wrong = {
        name: (reference, sections.get(name))
        for name, (_cls, reference) in _roster().items()
        if sections.get(name) != [reference]
    }
    assert not wrong, f"roster reference pointer is wrong: {wrong}"


# ---------------------------------------------------------------------------
# 2. index closure
# ---------------------------------------------------------------------------

def test_every_named_reference_exists():
    """Every reference the entry document names is a file that exists."""
    named = set(re.findall(r"skill/references/([a-z-]+\.md)", _entry_text()))
    assert named, "the entry document names no reference"
    missing = sorted(n for n in named if not (REFERENCES_DIR / n).is_file())
    assert not missing, f"the entry document names references that do not exist: {missing}"


def test_every_reference_file_is_named():
    """Every file under skill/references/ is named by the entry document."""
    named = set(re.findall(r"skill/references/([a-z-]+\.md)", _entry_text()))
    on_disk = {p.name for p in _reference_paths()}
    orphan = sorted(on_disk - named)
    assert not orphan, f"reference files the entry document never names: {orphan}"


def test_every_command_is_covered_by_exactly_one_reference():
    """No command without a home, no command with two, no orphan heading."""
    sections = _command_sections()
    registry = set(_commands())
    uncovered = sorted(registry - set(sections))
    doubled = {n: files for n, files in sections.items() if len(files) > 1}
    orphan = sorted(set(sections) - registry)
    assert not uncovered, f"registered commands no reference documents: {uncovered}"
    assert not doubled, f"commands documented by more than one reference: {doubled}"
    assert not orphan, f"reference sections for commands that do not exist: {orphan}"


# ---------------------------------------------------------------------------
# 3. budgets
# ---------------------------------------------------------------------------

def test_entry_document_budget():
    lines, size = _size(ENTRY)
    max_lines, max_bytes = ENTRY_BUDGET
    assert lines <= max_lines and size <= max_bytes, (
        f"SKILL-CLI.md is {lines} lines / {size} bytes; "
        f"the budget is {max_lines} lines / {max_bytes} bytes"
    )


@pytest.mark.parametrize("path", _reference_paths(), ids=lambda p: p.name)
def test_reference_budget(path: Path):
    lines, size = _size(path)
    max_lines, max_bytes = REFERENCE_BUDGET
    assert lines <= max_lines and size <= max_bytes, (
        f"skill/references/{path.name} is {lines} lines / {size} bytes; "
        f"the budget is {max_lines} lines / {max_bytes} bytes"
    )


def test_every_reference_opens_with_its_governing_records():
    """A reference that restates a decision names where it is decided."""
    for path in _reference_paths():
        head = path.read_text(encoding="utf-8")[:600]
        assert "Governed by" in head, f"{path.name} does not name its records"
        assert re.search(r"ADR-00\d\d", head), f"{path.name} names no ADR"


# ---------------------------------------------------------------------------
# 4. taxonomy coverage
# ---------------------------------------------------------------------------

def test_entry_document_carries_every_exit_code():
    text = _entry_text()
    section = re.search(r"^## Exit codes$(.*?)(?=\n## )", text, re.M | re.S)
    assert section, "SKILL-CLI.md lost its exit-code table"
    found = {int(m) for m in re.findall(r"^\| (\d) \|", section.group(1), re.M)}
    missing = sorted(set(EXIT_CODES) - found)
    assert not missing, f"exit codes missing from the entry table: {missing}"
    assert "`ErrorType: message`" in section.group(1), (
        "the entry table must say ErrorType is the semantic discriminator"
    )


def test_failures_reference_expands_every_exit_code():
    text = _reference_text("failures.md")
    missing = [c for c in EXIT_CODES if not re.search(rf"^#+ .*\b{c}\b", text, re.M)]
    assert not missing, (
        f"failures.md has no section for exit codes {missing}; "
        "every code the entry document lists is expanded there"
    )


def _declared_error_types() -> set[str]:
    """The ``error_type`` strings the code declares: what stderr shows a caller."""
    text = ERRORS.read_text(encoding="utf-8")
    return {m.group(1)
            for m in re.finditer(r'^\s+error_type = "([A-Za-z0-9_]+)"', text, re.M)}


def _documented_error_types() -> set[str]:
    """The `ErrorType` column of every cause-and-remedy row in failures.md."""
    text = _reference_text("failures.md")
    cells = {m.group(1)
             for m in re.finditer(r"^\| `([A-Za-z0-9_]+)` \|", text, re.M)}
    # The column heading is a backticked `ErrorType` in the same position.
    return cells - {ERROR_TYPE_HEADING}


def test_failures_reference_names_every_declared_error_type():
    """The whole taxonomy, compared in both directions.

    The exit-code check above reads headings, so it can only see codes and
    cannot notice a declared ``ErrorType`` with no row -- which is how five of
    a round's seven new types went missing. Every declared type is a line a
    caller can meet on stderr, so every one needs a row to look up, and a row
    naming a type that does not exist is just as wrong.
    """
    declared = _declared_error_types()
    documented = _documented_error_types()
    assert len(declared) > 20, (
        f"errors.py declares only {sorted(declared)}: the reader is broken")
    undocumented = sorted(declared - documented)
    assert not undocumented, (
        f"failures.md has no row for error types the code declares: "
        f"{undocumented}; a caller that meets one cannot look it up")
    invented = sorted(documented - declared)
    assert not invented, (
        f"failures.md names error types no code declares: {invented}")


# ---------------------------------------------------------------------------
# 5. generated blocks equal the generator
# ---------------------------------------------------------------------------

def test_generated_blocks_match_the_generator():
    generator = _load_generator()
    drifted: list[str] = []
    for key, body in generator.blocks().items():
        path = generator.GENERATED[key]
        text = path.read_text(encoding="utf-8")
        if _generated_body(text, key) != body:
            drifted.append(f"{key} in {_under_checkout(path)}")
    assert not drifted, (
        "generated blocks differ from scripts/generate_skill_docs.py output "
        f"(run it): {drifted}"
    )


def test_generated_markers_are_balanced():
    """No region left unrendered, and no file carrying a stray marker."""
    for path in [ENTRY, *_reference_paths()]:
        text = path.read_text(encoding="utf-8")
        begins = re.findall(r"<!-- generated:([a-z-]+):begin -->", text)
        ends = re.findall(r"<!-- generated:([a-z-]+):end -->", text)
        assert begins == ends, f"{path.name}: marker pairs are unbalanced ({begins} vs {ends})"


def test_a_drift_path_outside_the_checkout_is_an_assertion_not_a_crash():
    """The failure message must be buildable, or the check reports a crash."""
    outside = REPO.parent / "elsewhere" / "SKILL-CLI.md"
    with pytest.raises(AssertionError) as caught:
        _under_checkout(outside)
    assert "outside the checkout" in str(caught.value)


EXTERNAL_FLAGS = {
    # click's own option
    "--help",
    # keeper's flags, named where a plan is applied or undone
    "--expected-hash",
    "--expected-content",
    "--task-id",
}


def test_every_mentioned_flag_exists_on_a_command():
    """A `--flag` in the agent surface is a flag the CLI really accepts."""
    real: set[str] = set()
    for cmd in _commands().values():
        real |= _command_flags(cmd)
    mentioned = set(re.findall(r"(--[a-z][a-z0-9-]+)", _surface_text()))
    bogus = sorted(mentioned - real - EXTERNAL_FLAGS)
    assert not bogus, f"documented flags no command accepts: {bogus}"


def test_global_flags_section_names_the_fresh_set():
    """The global-flags paragraph names every command that really takes
    --fresh, and does not claim it universally."""
    text = _reference_text("machine-output.md")
    section = re.search(r"^## Global flags$(.*?)(?=\n## )", text, re.M | re.S)
    assert section, "machine-output.md lost the 'Global flags' section"
    para = section.group(1)
    assert "--skip-stale-check" in para
    fresh = sorted(n for n, c in _commands().items() if "--fresh" in _command_flags(c))
    # `map` and `file-outline` are hidden dual names of skeleton/outline: the
    # same command, so the paragraph names the canonical spelling only.
    aliases = {"map", "file-outline"}
    fresh = [n for n in fresh if n not in aliases]
    assert fresh == ["find-definition", "find-references", "find-symbol",
                     "get-code-context", "outline", "semantic-search",
                     "skeleton"], fresh
    for name in fresh:
        assert f"`{name}`" in para, f"{name} accepts --fresh but the section omits it"
    for name in ("find-callers", "find-callees", "deps", "overview",
                 "changed-symbols"):
        assert f"`{name}`" in para, name
    assert "every project-scoped read/search command also accepts" not in para


# ---------------------------------------------------------------------------
# 6. the installed copy's digest
# ---------------------------------------------------------------------------

def test_installed_copy_digest_matches_the_tracked_copy():
    """Equal digest when installed in the new shape; skip with the reason when not."""
    path, reason = _installed_skill()
    if path is None:
        pytest.skip(reason)
    assert _dir_digest(path) == _tracked_digest(), (
        f"the installed copy at {path} differs from the tracked copy; "
        "the release step installs after this test passes"
    )


def test_installed_digest_helper_on_a_throwaway_copy(tmp_path: Path):
    """The digest helper agrees on an exact copy and disagrees on a tamper."""
    root = tmp_path / "code-indexer"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(_entry_text(), encoding="utf-8")
    for path in _reference_paths():
        (root / "references" / path.name).write_bytes(path.read_bytes())
    assert _dir_digest(root) == _tracked_digest()
    target = root / "references" / _reference_paths()[0].name
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _dir_digest(root) != _tracked_digest()


def test_installed_digest_helper_skips_a_legacy_copy(tmp_path: Path, monkeypatch):
    """A single-file installed copy is not installed in this shape."""
    skills_root = tmp_path / "skills"
    (skills_root / "code-indexer").mkdir(parents=True)
    (skills_root / "code-indexer" / "SKILL.md").symlink_to(ENTRY)
    monkeypatch.setenv("CODE_INDEXER_SKILLS_ROOT", str(skills_root))
    path, reason = _installed_skill()
    assert path is None
    assert "not installed in this shape" in reason


# ---------------------------------------------------------------------------
# the rest of the documentation contract
# ---------------------------------------------------------------------------

ENV_VARS_IN_CODE = [
    # mirror of src/code_indexer (config._DEFAULTS + scattered getenv reads);
    # kept explicit so a new env var added in code forces an edit here.
    # Retired variables are not live reads: they are checked separately, against
    # config.RETIRED_ENV_VARS, by test_retired_env_vars_stay_documented.
    "OLLAMA_URL", "QDRANT_URL", "EMBED_MODEL", "INDEX_ROOT",
    "EMBED_BATCH", "UPSERT_BATCH", "MAX_FILE_BYTES", "WATCH_DEBOUNCE",
    "WATCH_QUIET_PERIOD", "WATCH_SWEEP_INTERVAL", "OLLAMA_TIMEOUT",
    "EMBED_CONCURRENCY", "CHUNK_MAX_CHARS", "PARANOID_HASH", "VERBOSE",
    "QUERY_INSTRUCTION", "EMBED_CACHE", "EMBED_CACHE_MAX_GB",
]

# CODE_INDEXER_BIN is read by the MCP server (not the CLI), so it is
# documented in README's MCP section rather than the skill's env table.
MCP_SERVER_ENV_VARS = {"CODE_INDEXER_BIN"}


def _configuration_section() -> str:
    """The env table lives in the indexing reference, its one home."""
    text = _reference_text("indexing.md")
    m = re.search(r"^## Configuration$", text, re.M)
    assert m, "indexing.md is missing the '## Configuration' section"
    return text[m.start():]


def test_code_env_vars_documented_in_the_reference():
    text = _configuration_section()
    missing = sorted(v for v in ENV_VARS_IN_CODE if v not in text)
    assert not missing, f"env vars read by the code but absent from indexing.md: {missing}"
    readme = _readme_text()
    missing_mcp = sorted(v for v in MCP_SERVER_ENV_VARS if v not in readme)
    assert not missing_mcp, f"MCP-server env vars absent from README: {missing_mcp}"


def test_retired_env_vars_stay_documented():
    """A retired variable keeps its row: it is accepted and ignored, not dropped.

    The code names its retired variables in ``config.RETIRED_ENV_VARS``; the
    reference must keep saying so, because a reader who has one exported needs
    to find out that it no longer does anything.
    """
    from code_indexer.config import RETIRED_ENV_VARS

    text = _configuration_section()
    missing = sorted(v for v in RETIRED_ENV_VARS if v not in text)
    assert not missing, (
        f"retired env vars absent from indexing.md: {missing} "
        "(the row stays, and says the variable is ignored)"
    )


def test_documented_env_vars_exist_in_code():
    from code_indexer.config import RETIRED_ENV_VARS

    text = _configuration_section()
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]{2,})`", text))
    documented -= set(ENV_VARS_IN_CODE) | set(RETIRED_ENV_VARS) | {
        "PATH", "JSON", "SQL", "FTS5", "URL",
                                          "SIGTERM", "SIGINT", "GB", "LRU",
                                          "WAL", "SDK"}
    bogus = sorted(d for d in documented
                   if d not in ENV_VARS_IN_CODE and "_" in d)
    assert not bogus, (
        f"indexing.md documents env vars the code does not read: {bogus} "
        "(add to ENV_VARS_IN_CODE if the code gained them)"
    )


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP parity table (ADR-0007 section 5)
# ---------------------------------------------------------------------------

def _mcp_tools() -> list[str]:
    from code_indexer.server import mcp
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    return sorted(t.name for t in tools)


def test_mcp_tools_in_parity_table():
    table = re.search(
        r"### CLI ↔ MCP parity.*?(?=\n## )", _readme_text(), re.S)
    assert table, "README lost the '### CLI ↔ MCP parity' section"
    text = table.group(0)
    missing = [t for t in _mcp_tools() if f"`{t}`" not in text]
    assert not missing, f"MCP tools missing from README parity table: {missing}"


def test_cli_only_commands_labeled():
    """The commands with no tool are named as CLI-only in the parity list."""
    text = re.search(r"### CLI ↔ MCP parity.*?(?=\n## )", _readme_text(), re.S).group(0)
    assert "**CLI-only**" in text
    for cmd in ("index-more", "doctor", "watch", "unwatch", "sweep-orphans",
                "eval"):
        assert cmd in text, f"parity table does not name {cmd}"
    # A command with a tool must not be labelled CLI-only.
    for tool in ("overview", "skeleton", "list_projects"):
        assert tool not in text.split("**CLI-only**")[1], (
            f"{tool} is both a tool and labelled CLI-only")


def test_mcp_section_documents_the_install_line_and_usage_record():
    text = _readme_text()
    assert 'uvx --from "code-indexer[mcp]" code-indexer-mcp' in text
    assert "mcp-usage.jsonl" in text
    assert "use the CLI subcommand of the same name" in text


# ---------------------------------------------------------------------------
# Python range
# ---------------------------------------------------------------------------

def test_python_version_consistent():
    py = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires = py["project"]["requires-python"]
    lower = re.search(r">=([\d.]+)", requires).group(1)
    upper_m = re.search(r"<([\d.]+)", requires)
    upper = upper_m.group(1) if upper_m else None
    readme = _readme_text()
    claimed = re.findall(r"[Pp]ython 3\.\d+", readme)
    bad = [c for c in claimed if c != f"Python {lower}"]
    assert not bad, (
        f"README claims {bad} but pyproject requires-python lower bound is {lower}"
    )
    if upper:
        upper_major = upper.split(".")[1] if "." in upper else upper
        for c in claimed:
            minor = int(c.split(".")[1])
            assert minor < int(upper_major), (
                f"README claims {c} which is >= the requires-python upper "
                f"bound {requires}"
            )


def test_python_version_file_matches():
    py = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requires = py["project"]["requires-python"]
    lower = re.search(r">=([\d.]+)", requires).group(1)
    version_file = REPO / ".python-version"
    if version_file.exists():
        assert version_file.read_text().strip() == lower, (
            f".python-version ({version_file.read_text().strip()}) != "
            f"requires-python lower bound ({lower})"
        )


# ---------------------------------------------------------------------------
# link / anchor check
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _headings(text: str) -> list[str]:
    hs = []
    for ln in text.splitlines():
        m = _HEADING_RE.match(ln)
        if m:
            hs.append(m.group(2).strip().lower())
    return hs


def _anchor_of(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


def test_relative_links_resolve():
    bad: list[str] = []
    for path in [README, ENTRY, *_reference_paths()]:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\((?!http)([^)#]+)(?:#([^)]+))?\)", text):
            rel, anchor = target
            resolved = (path.parent / rel).resolve()
            if not resolved.exists():
                bad.append(f"{path.name}: link target {rel} does not exist")
                continue
            if anchor:
                heads = _headings(resolved.read_text(encoding="utf-8"))
                anchors = {_anchor_of(h) for h in heads}
                if anchor not in anchors:
                    bad.append(
                        f"{path.name}: anchor #{anchor} not found in {rel} "
                        f"(available: {sorted(anchors)})")
    assert not bad, "\n".join(bad)


def test_no_dangling_see_below():
    for path in (README, ENTRY):
        text = path.read_text(encoding="utf-8")
        assert "see below" not in text.lower(), (
            f"{path.name} still contains a 'see below' reference"
        )


# ---------------------------------------------------------------------------
# the read contract (ADR-0003) is described, not just implemented
# ---------------------------------------------------------------------------

def test_the_surface_describes_the_read_write_contract():
    text = _surface_text()
    # classification
    assert "read command" in text and "write command" in text
    assert "`sweep-orphans --apply`" in text
    # --skip-stale-check is inert, --refresh is the only write trigger
    assert "inert" in text
    assert "only flag that\nturns a read into a write" in text or \
        "only flag that turns\na read into a write" in text
    # the staleness block and its text line
    for field in ("stale", "stale_known", "stale_reasons",
                  "stale_reason_count", "index_revision", "last_indexed"):
        assert f"`{field}`" in text, field
    assert "Warning: index is stale: 2 modified files, 1 deleted file" in text
    assert "answers describe the index, not the tree" in text
    # bounded query
    assert "--all-projects" in text and "reason=budget" in text
    assert "partial: true" in text or "`partial`" in text
    # exit-code table
    for code in ("| 0 |", "| 1 |", "| 2 |", "| 3 |", "| 4 |", "| 5 |"):
        assert code in text, code
    assert "RebuildRequiredError" in text or "requires a rebuild" in text
# The banner's shipped one-line form, both ends anchored: a count phrase, the
# meaning sentence, then the repair, in that order. The anchors are the point --
# a loose substring check stayed green while a document quoted the count prefix
# and carried the sentence only in the prose around it.
_STALE_BANNER = re.compile(
    r"Warning: index is stale: \d+ (?:modified|added|deleted) files?"
    r"(?:, \d+ (?:modified|added|deleted) files?)* -- <path>; "
    r"answers describe the index, not the tree; "
    r"refresh with code-indexer reindex-project <path>"
)


def test_every_document_quoting_the_stale_banner_quotes_the_whole_line():
    """Each quoting document carries the shipped line in full, per document.

    ``README.md`` is in this surface because it is the first document a reader
    opens and its quote went stale for a whole round while the loose check in
    ``test_the_surface_describes_the_read_write_contract`` stayed green: the
    count matched in one place and the sentence matched in the prose beside
    it. Matching the whole one-line form per document is what makes that drift
    red -- a count without the sentence, or the sentence moved in front of the
    count, is not the shipped line.
    """
    bad: list[str] = []
    for path in [README, ENTRY, *_reference_paths()]:
        quoted = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                  if ln.strip().startswith("Warning: index is stale")]
        if path in (README, ENTRY) and not quoted:
            bad.append(f"{path.name}: quotes no stale banner line")
        for ln in quoted:
            if not _STALE_BANNER.fullmatch(ln):
                bad.append(f"{path.name}: stale line is not the shipped "
                           f"one-line form:\n  {ln}")
    assert not bad, "\n".join(bad)


def test_the_five_read_answers_document_json():
    """The five read commands that answer as data say so in their section."""
    text = _reference_text("machine-output.md")
    for name in ("list-projects", "lookup-project", "index-status",
                 "find-definition", "find-references"):
        row = re.search(rf"^\| `{name}` \|.*\|$", text, re.M)
        assert row, f"machine-output.md does not list {name}"
        assert "`--json`" in row.group(0), (name, row.group(0))


def test_readme_describes_the_read_contract():
    text = _readme_text()
    assert "stale_known" in text
    assert "--refresh" in text and "only flag that turns a read" in text
    assert "`--skip-stale-check`" in text and "inert" in text
    assert "`3` backend" in text or "3 backend" in text
    assert "`--all-projects`" in text


def test_json_schema_documents_the_new_answers():
    text = JSON_SCHEMA.read_text(encoding="utf-8")
    for heading in ("list-projects --json", "lookup-project --json",
                    "index-status --json", "find-definition --json",
                    "find-references --json",
                    "The staleness block (every read answer)"):
        assert f"## {heading}" in text, heading
    for field in ("stale_known", "stale_reason_count", "index_revision",
                  "last_indexed"):
        assert field in text, field
    assert '"schema": 1' in text or "`schema` stays `1`" in text


def test_changelog_has_the_read_contract_section():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert "## [Unreleased] \u2014 read contract" in text
    section = text.split("## [Unreleased] \u2014 read contract", 1)[1]
    section = section.split("\n## ", 1)[0]
    assert "Reads stop repairing the index" in section
    assert "failed search" in section and "exits 3" in section


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
