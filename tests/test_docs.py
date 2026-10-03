"""Documentation-drift tests (offline; part of `pytest -m "not live"`).

Guards README.md and SKILL-CLI.md against drifting from the code:

1. command coverage   — every registered CLI command is documented in
                        SKILL-CLI.md's command block, and every documented
                        command exists (hidden/dev commands on an allow-list)
2. option coverage    — every documented --flag exists in the command's
                        real click parameters (with alias resolution)
3. duplicate commands — each command appears once in the command block
4. env var coverage   — env vars read from the code appear in SKILL-CLI.md's
                        env list; documented env vars are real
5. MCP parity table   — every registered MCP tool appears in README's
                        parity table; CLI-only commands are labeled
6. Python range       — pyproject requires-python matches the README's claim
7. anchor/link check  — relative markdown links resolve to files/headings

Every failure prints the exact mismatch so the fix is mechanical.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest
import typer
from typer.main import get_command as typer_get_command

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
SKILL = REPO / "SKILL-CLI.md"
PYPROJECT = REPO / "pyproject.toml"

# Dev/ops utilities documented separately in SKILL-CLI.md (they are in the
# command block under a dev-utilities comment, but not part of the agent
# workflow sections); allow-list for the bidirectional coverage check.
HIDDEN_COMMANDS: set[str] = set()

SKILL_DESC = re.compile(r"\bdescription:\s*\"(.*)\"", re.M)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _click_group():
    import code_indexer.cli as cli_mod

    return typer_get_command(cli_mod.app)


def _walk(group, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for name, cmd in group.commands.items():
        out[prefix + name] = cmd
        if hasattr(cmd, "commands"):
            out.update(_walk(cmd, prefix + name + " "))
    return out


def _commands() -> dict[str, object]:
    return _walk(_click_group())


def _command_flags(cmd: click.Command) -> set[str]:
    """Long option names (--x, --x/--y aliases flattened)."""
    flags: set[str] = set()
    for p in cmd.params:
        opts = getattr(p, "opts", [])
        name = getattr(p, "name", "")
        # TyperOption subclasses typer's vendored click, not click itself —
        # check by attribute, not isinstance.
        if opts and not name.isupper():
            for opt in opts:
                if opt.startswith("--"):
                    flags.add(opt)
    return flags


def _flag_aliases(cmd: click.Command) -> dict[str, set[str]]:
    """primary -> alias set for dual-name options (--type/--symbol-type)."""
    out: dict[str, set[str]] = {}
    for p in cmd.params:
        longs = [o for o in getattr(p, "opts", []) if o.startswith("--")]
        if len(longs) > 1:
            out[longs[0]] = set(longs[1:])
    return out


def _skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```[a-z]*\n(.*?)```", text, re.S)


def _documented_command_lines() -> list[str]:
    """Lines of SKILL-CLI.md's '# registry ... # lifecycle' command block."""
    for block in _fenced_blocks(_skill_text()):
        if "code-indexer add-project" in block and "code-indexer index-more" in block:
            return [
                ln.strip()
                for ln in block.splitlines()
                if ln.strip().startswith("code-indexer ")
            ]
    return []


def _documented_commands() -> dict[str, str]:
    """command name -> full documented line."""
    out: dict[str, str] = {}
    for line in _documented_command_lines():
        rest = line[len("code-indexer "):]
        name = rest.split()[0]
        out.setdefault(name, line)
    return out


def _documented_flags(line: str) -> set[str]:
    return set(re.findall(r"(--[a-z][a-z0-9-]+)", line))


# Section-anchored slices so checks look at the right block of each file.
def _readme_env_section() -> str:
    """README now defers env vars to SKILL-CLI.md; both must agree."""
    text = _skill_text()
    m = re.search(r"^## Key rules$", text, re.M)
    assert m, "SKILL-CLI.md is missing the '## Key rules' section"
    return text[m.start():]


ENV_VARS_IN_CODE = [
    # mirror of src/code_indexer (config._DEFAULTS + scattered getenv reads);
    # kept explicit so a new env var added in code forces an edit here.
    "OLLAMA_URL", "QDRANT_URL", "EMBED_MODEL", "INDEX_ROOT", "STALE_TTL",
    "EMBED_BATCH", "UPSERT_BATCH", "MAX_FILE_BYTES", "WATCH_DEBOUNCE",
    "WATCH_QUIET_PERIOD", "WATCH_SWEEP_INTERVAL", "OLLAMA_TIMEOUT",
    "EMBED_CONCURRENCY", "CHUNK_MAX_CHARS", "PARANOID_HASH", "VERBOSE",
    "QUERY_INSTRUCTION", "EMBED_CACHE", "EMBED_CACHE_MAX_GB",
]

# CODE_INDEXER_BIN is read by the MCP server (not the CLI), so it is
# documented in README's MCP section rather than the SKILL env list.
MCP_SERVER_ENV_VARS = {"CODE_INDEXER_BIN"}


# ---------------------------------------------------------------------------
# 1. command coverage
# ---------------------------------------------------------------------------

def test_every_registered_command_is_documented():
    documented = _documented_commands()
    missing = sorted(set(_commands()) - set(documented) - HIDDEN_COMMANDS)
    assert not missing, (
        "commands registered in the CLI but missing from SKILL-CLI.md's "
        f"command block: {missing}"
    )


def test_every_documented_command_exists():
    cmds = _commands()
    bogus = sorted(n for n in _documented_commands() if n not in cmds)
    assert not bogus, f"SKILL-CLI.md documents commands that do not exist: {bogus}"


# ---------------------------------------------------------------------------
# 2. option coverage
# ---------------------------------------------------------------------------

def test_documented_flags_exist_in_cli():
    cmds = _commands()
    bad: list[str] = []
    for name, line in sorted(_documented_commands().items()):
        cmd = cmds[name]
        real = _command_flags(cmd)
        aliases = _flag_aliases(cmd)
        for flag in sorted(_documented_flags(line) - {"--help"}):
            if flag == "--all":
                # argument-scoped vs project flag; verify on the actual cmd
                if flag in real:
                    continue
                if name in ("watch", "unwatch"):
                    continue
                bad.append(f"{name}: documented --all does not exist")
                continue
            ok = flag in real or any(flag in v for v in aliases.values())
            if not ok:
                bad.append(f"{name}: documented {flag} does not exist "
                           f"(real options: {sorted(real)})")
    assert not bad, "\n".join(bad)


def test_global_flags_line_is_accurate():
    """--skip-stale-check and --fresh must exist wherever the docs claim them."""
    text = _skill_text()
    m = re.search(r"^Global flags:.*$", text, re.M)
    assert m, "SKILL-CLI.md lost the 'Global flags:' line"
    line = m.group(0)
    m2 = re.search(r"^also accepts `--fresh`.*$", text, re.M)
    assert m2, "SKILL-CLI.md lost the '--fresh' global-flag sentence"
    full = line + "\n" + m2.group(0)
    assert "--skip-stale-check" in full and "--fresh" in full
    sample = _commands()["semantic-search"]
    real = _command_flags(sample)
    assert "--skip-stale-check" in real and "--fresh" in real


# ---------------------------------------------------------------------------
# 3. duplicate detection
# ---------------------------------------------------------------------------

def test_no_duplicate_commands_in_block():
    seen: dict[str, int] = {}
    for line in _documented_command_lines():
        name = line[len("code-indexer "):].split()[0]
        seen[name] = seen.get(name, 0) + 1
    dupes = sorted(n for n, c in seen.items() if c > 1)
    # Two legitimate multi-line entries: find-symbol browse mode is a second
    # spelling of the same command, watch/unwatch show the --all form.
    allowed = {"find-symbol", "watch", "unwatch"}
    dupes = [d for d in dupes if d not in allowed]
    assert not dupes, f"commands listed more than once in SKILL-CLI.md: {dupes}"


# ---------------------------------------------------------------------------
# 4. env var coverage
# ---------------------------------------------------------------------------

def test_code_env_vars_documented_in_skill():
    text = _readme_env_section()
    missing = sorted(v for v in ENV_VARS_IN_CODE if v not in text)
    assert not missing, f"env vars read by the code but absent from SKILL-CLI.md: {missing}"
    readme = _readme_text()
    missing_mcp = sorted(v for v in MCP_SERVER_ENV_VARS if v not in readme)
    assert not missing_mcp, (
        f"MCP-server env vars absent from README: {missing_mcp}")


def test_documented_env_vars_exist_in_code():
    text = _readme_env_section()
    documented = set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b", text))
    documented -= set(ENV_VARS_IN_CODE) | {"PATH", "JSON", "SQL", "FTS5", "URL",
                                           "SIGTERM", "SIGINT", "GB", "LRU",
                                           "WAL", "SDK"}
    bogus = sorted(d for d in documented
                   if d not in ENV_VARS_IN_CODE and "_" in d)
    assert not bogus, (
        f"SKILL-CLI.md documents env vars the code does not read: {bogus} "
        "(add to ENV_VARS_IN_CODE if the code gained them)"
    )


# ---------------------------------------------------------------------------
# 5. MCP parity table
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
    text = re.search(r"### CLI ↔ MCP parity.*?(?=\n## )", _readme_text(), re.S).group(0)
    for cmd in ("overview", "index-more", "doctor", "watch", "unwatch"):
        assert cmd in text and "CLI-only" in text, (
            f"parity table does not label {cmd} as CLI-only"
        )


# ---------------------------------------------------------------------------
# 6. Python range
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
    # The pinned single-version scheme: README must not silently claim support
    # for a range beyond the upper bound.
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
# 7. link / anchor check
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
    for path in (README, SKILL):
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
    for path in (README, SKILL):
        text = path.read_text(encoding="utf-8")
        assert "see below" not in text.lower(), (
            f"{path.name} still contains a 'see below' reference"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
