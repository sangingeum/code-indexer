"""Changed-symbols / diff-impact (navigation work item).

Read-only post-edit view: map a git diff's changed line ranges onto symbols
by parsing the NEW and OLD contents with tree-sitter (never trusting the
possibly-stale manifest). Works without an index for pure line/symbol views;
--impact enriches modified/removed symbols from the graph tables.

Diffs come from `git diff -U0 --no-color -M` (subprocess); removed symbols
come from parsing `git show <base>:<path>` content. Non-code/binary files
group under 'other files'; syntax-error files fall back to raw line ranges.
"""

from __future__ import annotations

import os
import re
import subprocess

from .manifest import Manifest


class NotAGitRepo(Exception):
    pass


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git_diff_files(project_path: str, base: str = "HEAD",
                   head: str | None = None, staged: bool = False,
                   include_untracked: bool = False) -> dict[str, str]:
    """Changed files -> change kind (added|modified|removed|renamed)."""
    argv = ["git", "-C", project_path, "diff", "--no-color", "-M",
            "--name-status"]
    if staged:
        argv.append("--cached")
    argv.append(base)
    if head:
        argv.append(head)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 and "not a git repository" in proc.stderr.lower():
        raise NotAGitRepo(f"not a git repository: {project_path}")
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][0]  # R100 -> R
        if status == "R":
            out[parts[2]] = "renamed"
        elif status == "A":
            out[parts[1]] = "added"
        elif status == "D":
            out[parts[1]] = "removed"
        else:
            out[parts[1]] = "modified"
    if include_untracked:
        # Untracked files are never staged; report them regardless of the
        # --staged flag (the flag scopes tracked-file diffs only).
        ls = subprocess.run(
            ["git", "-C", project_path, "ls-files", "--others",
             "--exclude-standard"], capture_output=True, text=True, timeout=30)
        for path in ls.stdout.splitlines():
            if path.strip():
                out.setdefault(path.strip(), "added")
    return out


def changed_line_ranges(project_path: str, file: str, base: str = "HEAD",
                        head: str | None = None, staged: bool = False,
                        ) -> list[tuple[int, int]]:
    """New-side changed line ranges from `git diff -U0`."""
    argv = ["git", "-C", project_path, "diff", "-U0", "--no-color"]
    if staged:
        argv.append("--cached")
    argv.append(base)
    if head:
        argv += [head, "--", file]
    else:
        argv += ["--", file]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    ranges: list[tuple[int, int]] = []
    for line in proc.stdout.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                # Pure deletion at old position: no new lines to map.
                continue
            ranges.append((start, start + count - 1))
    return ranges


def _live_or_git_text(project_path: str, file: str, ref: str | None) -> str:
    if ref in (None, "WORKTREE"):
        p = os.path.join(project_path, file)
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""
    proc = subprocess.run(
        ["git", "-C", project_path, "show", f"{ref}:{file}"],
        capture_output=True, text=True, timeout=30)
    return proc.stdout if proc.returncode == 0 else ""


def _binary(text: str) -> bool:
    return "\x00" in text[:8192]


def _lang_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    from .indexer import _lang_from_ext
    return _lang_from_ext(path) if ext else "unknown"


def symbols_in_ranges(text: str, path: str,
                      ranges: list[tuple[int, int]]) -> list[dict]:
    """Parse with tree-sitter and return symbols overlapping the ranges."""
    from . import ts_chunker
    lang = _lang_of(path)
    try:
        chunks = ts_chunker.chunk_text(path, text)
        syms = ts_chunker.extract_symbols(path, text, chunks)
    except Exception:  # noqa: BLE001 — syntax-error files: line fallback
        syms = []
    if not syms:
        # Graceful fallback: report the raw line ranges.
        return [{"name": None, "start_line": s, "end_line": e,
                 "symbol_type": None, "fallback": True}
                for s, e in ranges]
    out: list[dict] = []
    seen: set = set()
    for sym in syms:
        for s, e in ranges:
            if sym["start_line"] <= e and sym["end_line"] >= s:
                key = (sym["name"], sym["start_line"])
                if key not in seen:
                    seen.add(key)
                    out.append({
                        "name": sym["name"],
                        "symbol_type": sym.get("symbol_type"),
                        "start_line": sym["start_line"],
                        "end_line": sym["end_line"],
                        "signature": sym.get("signature"),
                    })
                break
    if not out and ranges:
        out = [{"name": None, "start_line": s, "end_line": e,
                "symbol_type": None, "fallback": True}
               for s, e in ranges]
    return sorted(out, key=lambda d: (d["start_line"] or 0))


def changed_symbols(project_path: str, base: str = "HEAD",
                    head: str | None = None, staged: bool = False,
                    include_untracked: bool = False) -> dict:
    """The changed-symbols view: per-file symbol changes with classification."""
    files = git_diff_files(project_path, base, head, staged, include_untracked)
    result: dict[str, dict] = {}
    other_files: list[str] = []
    for file, kind in sorted(files.items()):
        new_text = _live_or_git_text(project_path, file, head)
        if kind == "removed" or _binary(new_text):
            if kind == "removed":
                result[file] = {"kind": kind, "symbols": []}
            else:
                other_files.append(file)
            continue
        lang = _lang_of(file)
        if lang in ("markdown", "yaml", "json", "toml", "text", "unknown"):
            other_files.append(file)
            continue
        ranges = changed_line_ranges(project_path, file, base, head, staged)
        if not ranges and kind == "added":
            # Whole file is new: map the entire file.
            n = len(new_text.splitlines())
            ranges = [(1, max(n, 1))]
        new_syms = symbols_in_ranges(new_text, file, ranges)
        entry: dict = {"kind": kind, "symbols": new_syms}
        if kind in ("modified", "renamed"):
            old_text = _live_or_git_text(project_path, file,
                                         head or base) if head else \
                _live_or_git_text(project_path, file, base)
            old_syms = ts_symbols_safe(old_text, file)
            # Compare against ALL symbols in the new content (not just the
            # range-mapped ones) — an unchanged symbol must not be reported
            # as removed just because it sits outside the diff hunks.
            all_new_syms = ts_symbols_safe(new_text, file)
            new_names = {s["name"] for s in all_new_syms if s["name"]}
            entry["removed_symbols"] = [
                {"name": s["name"], "start_line": s["start_line"]}
                for s in old_syms
                if s["name"] and s["name"] not in new_names]
        result[file] = entry
    if other_files:
        result["(other files)"] = {"kind": "other", "files": other_files}
    return result


def ts_symbols_safe(text: str, path: str) -> list[dict]:
    if not text or _binary(text):
        return []
    try:
        from . import ts_chunker
        chunks = ts_chunker.chunk_text(path, text)
        return ts_chunker.extract_symbols(path, text, chunks)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# impact
# ---------------------------------------------------------------------------

def _candidate_tests(file: str, names: set[str]) -> set[str]:
    """Test files likely covering this file/symbol (name-stem heuristics)."""
    stems = {os.path.splitext(os.path.basename(file))[0]}
    for name in names:
        stems.add(name.lower())
    candidates: set[str] = set()
    for stem in stems:
        candidates |= {f"test_{stem}.py", f"{stem}_test.go",
                       f"{stem}.spec.ts", f"{stem}.test.ts",
                       f"test_{stem}.js", f"{stem}_test.py"}
    return candidates


def impact(core, slug: str, changed: dict, depth: int = 1,
           max_nodes: int = 40) -> dict:
    """Attach direct callers/referencing symbols + candidate tests for
    modified/removed symbols, from the graph tables (ast + heuristic)."""
    m: Manifest = core.manifest_for(slug)
    try:
        all_syms = m.all_symbols()
        definer: dict[str, str] = {}
        for s in all_syms:
            definer.setdefault(s.name, s.file)
        out: dict[str, dict] = {}
        budget = max_nodes
        for file, entry in changed.items():
            if entry.get("kind") not in ("modified", "removed", "renamed"):
                continue
            targets: list[str] = []
            for s in entry.get("symbols", []):
                if s.get("name"):
                    targets.append(s["name"])
            targets += [r["name"] for r in entry.get("removed_symbols", [])
                        if r.get("name")]
            if not targets:
                continue
            callers: list[dict] = []
            for name in targets:
                for edge in m.callers_of(name, limit=50):
                    if budget <= 0:
                        break
                    callers.append({"symbol": name,
                                    "file": edge["file"],
                                    "from": edge["from_symbol"],
                                    "line": edge["line"],
                                    "confidence": edge["confidence"]})
                    budget -= 1
            tests = sorted(_match_tests(
                _candidate_tests(file, set(targets)), _indexed_files(m)))
            out[file] = {"callers": callers[:max_nodes],
                         "candidate_tests": tests}
        return out
    finally:
        m.close()


def _indexed_files(m: Manifest) -> set[str]:
    return set(m.all_files().keys())


def _match_tests(candidates: set[str], indexed: set[str]) -> set[str]:
    """Match candidate test FILENAMES against indexed paths (basename or
    path suffix — tests usually live under tests/)."""
    out: set[str] = set()
    for path in indexed:
        base = os.path.basename(path)
        if base in candidates:
            out.add(path)
    return out


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(changed: dict, impact_data: dict | None = None) -> str:
    lines: list[str] = []
    for file in sorted(changed):
        entry = changed[file]
        kind = entry["kind"]
        if kind == "other":
            files = entry.get("files") or []
            if files:
                lines.append(f"other files: {', '.join(files[:8])}"
                             + (f" (+{len(files) - 8} more)"
                                if len(files) > 8 else ""))
            continue
        for s in entry.get("symbols", []):
            if s.get("name"):
                lines.append(f"{kind} {file} / {s.get('symbol_type') or ''}"
                             f" {s['name']}"
                             f":{s['start_line']}-{s['end_line']}".replace(
                                 " /  ", " / "))
            else:
                lines.append(f"{kind} {file} / lines "
                             f"{s['start_line']}-{s['end_line']}")
        for r in entry.get("removed_symbols", []):
            lines.append(f"removed {file} / {r['name']}"
                         f":{r['start_line']}")
        if not entry.get("symbols") and not entry.get("removed_symbols"):
            lines.append(f"{kind} {file}")
        if impact_data and file in impact_data:
            imp = impact_data[file]
            for c in imp.get("callers", [])[:6]:
                frm = f" {c['from']}" if c.get("from") else ""
                lines.append(f"  impact: {c['file']}{frm}"
                             f" ({c['confidence']})")
            for t in imp.get("candidate_tests", [])[:3]:
                lines.append(f"  candidate test: {t}")
    return "\n".join(lines) if lines else "no changes"