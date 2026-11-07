"""Overview: project-level map from manifest data only (navigation item).

No source parsing at query time — every section reads the manifest tables
(files/symbols/imports/refs) plus cheap filesystem existence checks for the
config/entry-point candidates. Complements the file-level surfaces: agents
orient with `overview` (this), then `skeleton` / `outline` for structure,
then search/get-code-context for specifics.

CLI-only by design: this is a human/agent orientation surface rendered as
capped text; MCP agents already have skeleton/outline/search and the graph
tools, so an MCP wrapper adds surface area without new capability.

Sections (each capped):
- languages: files, approx LOC (files.loc), share
- directories: top-level dirs with file and symbol counts
- entry points: main/`__main__` symbols, __main__.py, pyproject
  [project.scripts], package.json bin/main, CMakeLists add_executable,
  go.mod + a func main
- tests: test files/dirs and a framework hint
- config/build: known config/build filenames present at the root
- hotspots: top files by fan-in (distinct files importing them or
  referencing their symbols) and by size, ordered by the same policy as
  every other structural answer: source, examples, tests, docs,
  generated/artifact, then the count. A test fixture, a shipped sample or an
  audit document that accumulates identifier mentions does not win
  orientation. The ladder is the project's own: `[tool.code-indexer.ordering]`
  in its pyproject may add directory names to a rung. Fan-in credits each
  reference to the definition it resolves to (never to the first file that
  defines the name); a reference no rule may attribute is credited to no
  file and reported beside the list: `unattributed` is the total, split into
  `unresolved` (the name is defined nowhere) and `ambiguous` (the name has
  several definitions and no same-file match)."""

from __future__ import annotations

import json
import os
from typing import Any

from .manifest import Manifest
from .ordering import (TESTS, ClassNames, file_class, hotspot_key,
                       load_class_names)

# Known config/build files, checked at the project root.
_CONFIG_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "tsconfig.json", "Cargo.toml", "go.mod", "CMakeLists.txt",
    "Makefile", "meson.build", ".github/workflows", "Dockerfile",
    "docker-compose.yml", "tox.ini", "noxfile.py", "justfile",
)

_FRAMEWORK_HINTS = (
    ("pytest", ("conftest.py", "pytest.ini", "test_*")),
    ("unittest", ("test_*.py",)),
    ("jest/vitest", ("*.test.ts", "*.test.js", "*.spec.ts")),
    ("go test", ("*_test.go",)),
    ("ctest/catch2", ("*_test.cpp", "test_*.cpp")),
)


def _root_dirname(project_path: str) -> str:
    return os.path.basename(os.path.normpath(project_path)) or project_path


def build_overview(m: Manifest, project_path: str,
                   path_prefix: str = "") -> dict[str, Any]:
    """Assemble the overview dict from manifest data (no source parsing)."""
    names = load_class_names(project_path)
    files = m.all_files()
    if path_prefix:
        files = {p: f for p, f in files.items()
                 if p.startswith(path_prefix)}

    # -- languages ---------------------------------------------------------
    lang_files: dict[str, int] = {}
    lang_loc: dict[str, int] = {}
    total_loc = 0
    for f in files.values():
        lang = f.language or "unknown"
        lang_files[lang] = lang_files.get(lang, 0) + 1
        loc = f.loc or 0
        lang_loc[lang] = lang_loc.get(lang, 0) + loc
        total_loc += loc
    n_files = len(files)
    languages = sorted(
        ({"language": lang, "files": n, "loc": lang_loc.get(lang, 0),
          "share": round(100.0 * n / n_files, 1) if n_files else 0.0}
         for lang, n in lang_files.items()),
        key=lambda d: (-d["files"], d["language"]))

    # -- directories -------------------------------------------------------
    dir_files: dict[str, int] = {}
    dir_symbols: dict[str, int] = {}
    for path in files:
        top = path.split("/", 1)[0] if "/" in path else "."
        dir_files[top] = dir_files.get(top, 0) + 1
    all_syms = m.all_symbols() if hasattr(m, "all_symbols") else None
    if all_syms is None:
        for path in files:
            dir_symbols[path.split("/", 1)[0] if "/" in path else "."] = \
                dir_symbols.get(
                    path.split("/", 1)[0] if "/" in path else ".", 0)
    else:
        for sym in all_syms:
            top = sym.file.split("/", 1)[0] if "/" in sym.file else "."
            if sym.file in files:
                dir_symbols[top] = dir_symbols.get(top, 0) + 1
    directories = sorted(
        ({"dir": d, "files": dir_files[d],
          "symbols": dir_symbols.get(d, 0)}
         for d in dir_files),
        key=lambda d: (-d["files"], d["dir"]))

    # -- entry points ------------------------------------------------------
    entry_points: list[str] = []
    main_syms = [f"{s.file}:{s.name}" for s in (all_syms or [])
                 if s.name in ("main", "__main__") and s.file in files]
    entry_points += sorted(main_syms)[:5]
    for cand in ("__main__.py", "src/__main__.py"):
        if cand in files:
            entry_points.append(cand)
    pyproject = os.path.join(project_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, encoding="utf-8") as fh:
                text = fh.read()
            in_scripts = False
            for line in text.splitlines():
                if line.strip() == "[project.scripts]":
                    in_scripts = True
                    continue
                if line.strip().startswith("["):
                    in_scripts = False
                if in_scripts and "=" in line:
                    entry_points.append(
                        "script: " + line.split("=")[0].strip())
        except OSError:
            pass
    pkg_json = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, encoding="utf-8") as fh:
                pkg = json.load(fh)
            for key in ("bin", "main"):
                val = pkg.get(key)
                if val:
                    entry_points.append(
                        f"package.json {key}: "
                        + (val if isinstance(val, str)
                           else ", ".join(sorted(val))[:80]))
        except (OSError, json.JSONDecodeError):
            pass
    cmake = os.path.join(project_path, "CMakeLists.txt")
    if os.path.isfile(cmake):
        try:
            with open(cmake, encoding="utf-8") as fh:
                for line in fh:
                    if "add_executable" in line:
                        entry_points.append(
                            "cmake: " + line.split("add_executable")[-1]
                            .strip(" ()\t\r\n")[:60])
        except OSError:
            pass
    if "go.mod" in files or os.path.isfile(os.path.join(project_path,
                                                        "go.mod")):
        entry_points.append("go: see func main (checked in main symbols)")

    # -- tests -------------------------------------------------------------
    test_files = sorted(p for p in files
                        if _is_test_path(p, names))
    frameworks: list[str] = []
    root_names = {os.path.basename(p) for p in files}
    for fw, patterns in _FRAMEWORK_HINTS:
        import fnmatch
        for pat in patterns:
            if any(fnmatch.fnmatch(n, pat) for n in root_names):
                frameworks.append(fw)
                break

    # -- config/build ------------------------------------------------------
    config_files = sorted(
        c for c in _CONFIG_FILES
        if os.path.exists(os.path.join(project_path, c)))

    # -- hotspots ----------------------------------------------------------
    # Fan-in is the store's own per-file aggregate -- resolved imports into a
    # file plus references attributed to a definition that file holds. It is
    # complete by construction; the old version counted a 5,000-row sample of
    # `refs`, which on a large index was under 1% of the table and unstable
    # besides. The name half reads the RESOLVED target of each reference, so
    # it is per definition: a test module that sorts before the source owning
    # the same name no longer takes the source's count, and a name whose
    # definitions tie is credited to no file at all. That remainder is
    # reported beside the list instead of being folded into an arbitrary
    # definer -- the old representative-owner rule. The remainder is split by
    # reason: a name defined nowhere is `unresolved`, a name whose definitions
    # tie is `ambiguous`, and `unattributed` stays their sum so an existing
    # reader keeps a total while gaining its shape.
    # Under --path-prefix the aggregate is scoped to the files being
    # reported: only targets (and resolved imports) inside the prefix count.
    fan_in = {f: n for f, n in m.file_fan_in(path_prefix or None)
              if f in files}
    unattributed = m.unattributed_ref_counts(path_prefix or None)
    # Ordered by the policy, not by the raw count: source first, then tests,
    # docs and artifacts. A test fixture and an audit document accumulate
    # identifier mentions because they exercise or quote the code beneath
    # them, so a count-only list reports them as the centre of the codebase.
    hotspots_fan = sorted(
        ({"file": f, "fan_in": n} for f, n in fan_in.items()),
        key=lambda d: hotspot_key(d["file"], d["fan_in"], names))[:10]
    hotspots_size = sorted(
        ({"file": p, "loc": f.loc or 0} for p, f in files.items()),
        key=lambda d: hotspot_key(d["file"], d["loc"], names))[:10]

    return {
        "project": _root_dirname(project_path),
        "files": n_files,
        "loc": total_loc,
        "languages": languages,
        "directories": directories,
        "entry_points": entry_points[:12],
        "tests": {"files": test_files[:10], "count": len(test_files),
                  "framework_hint": frameworks},
        "config_files": config_files,
        "hotspots": {"fan_in": hotspots_fan,
                     "unattributed": unattributed["unattributed"],
                     "unresolved": unattributed["unresolved"],
                     "ambiguous": unattributed["ambiguous"],
                     "size": hotspots_size},
    }


def _is_test_path(path: str, names: ClassNames) -> bool:
    """The overview's test list uses the SAME classifier the ordering does.

    Two spellings of "test" would let a file be listed as a test and ranked
    as source (or the reverse).
    """
    return file_class(path, names) == TESTS


def format_overview(data: dict[str, Any], max_lines: int = 60) -> str:
    """Render the overview dict into capped, stable text lines."""
    lines: list[str] = []

    def add(s: str) -> None:
        if len(lines) < max_lines:
            lines.append(s)

    add(f"project: {data['project']} — {data['files']} files, "
        f"{data['loc']} LOC")
    langs = data["languages"][:5]
    if langs:
        add("languages:")
        for l in langs:
            add(f"  {l['language']}: {l['files']} files, {l['loc']} LOC "
                f"({l['share']}%)")
    dirs = data["directories"][:6]
    if dirs:
        add("directories:")
        for d in dirs:
            add(f"  {d['dir']}/: {d['files']} files, {d['symbols']} symbols")
    ep = data["entry_points"][:6]
    if ep:
        add("entry points:")
        for e in ep:
            add(f"  {e}")
    tests = data["tests"]
    if tests["count"]:
        add(f"tests: {tests['count']} files"
            + (f" (hint: {', '.join(tests['framework_hint'])})"
               if tests["framework_hint"] else ""))
        for t in tests["files"][:4]:
            add(f"  {t}")
    if data["config_files"]:
        add("config/build: " + ", ".join(data["config_files"][:8]))
    hs = data["hotspots"]
    if hs["fan_in"]:
        add("hotspots (fan-in):")
        for h in hs["fan_in"][:5]:
            add(f"  {h['file']}: {h['fan_in']}")
    if hs["unattributed"]:
        add(f"  unattributed: {hs['unattributed']} references credited to no "
            f"file ({hs['unresolved']} names defined nowhere, "
            f"{hs['ambiguous']} tied between definitions)")
    if hs["size"]:
        add("hotspots (size):")
        for h in hs["size"][:5]:
            add(f"  {h['file']}: {h['loc']} LOC")
    if len(lines) >= max_lines and lines[-1] != "(truncated)":
        lines[-1] = "(truncated)"
    return "\n".join(lines)