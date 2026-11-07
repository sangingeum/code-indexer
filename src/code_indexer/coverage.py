"""What a read answer is allowed to claim, carried with the answer.

A read command answers from the index as of ``meta.last_indexed``. When that
index never parsed the files the question is about, or withheld one for
privacy, an empty answer alone cannot say so -- "no symbols matching X" reads
exactly like "the name is absent", "those files were never parsed", "this
language has no AST reader" and "nothing is indexed". This module owns the ONE
vocabulary for saying which, and the coverage object that travels with every
read answer so a caller never has to run ``index-status`` to decide whether to
trust it:

``coverage`` (JSON, every read command):

- ``scope``          -- ``project`` (the whole index), ``answer`` (the files
  this answer actually looked at) or ``none`` (the command reads no index);
- ``files``          -- file rows considered;
- ``files_parsed``   -- of those, the ones whose parse verdict is ``parsed``;
- ``files_not_parsed`` -- the rest (``parse_error`` + ``parse_unavailable``
  + rows with no verdict);
- ``files_withheld`` -- files the sensitive layer withheld (no row at all);
- ``languages_without_ast`` -- the languages in scope that yield no AST
  symbol rows, so nothing in them is indexed as a symbol;
- ``freshness``     -- ``fresh`` | ``stale`` | ``unknown`` (the staleness
  block's own verdict, never a second estimate);
- ``complete``      -- true only when every file in scope parsed and none was
  withheld.

``empty_reason`` (the name reads, when the answer is empty) is one value from
``EMPTY_REASONS`` plus the sentence that states it. The precedence is the one
the card fixes: the name is absent from an index that parsed the files that
could hold it; otherwise the files were not parsed (count and languages named);
otherwise the language has no AST reader (language named); otherwise the
project is not indexed. An empty answer is not an error: exit codes do not
change.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Sequence

from .manifest import Manifest, ManifestFile

# Which files an answer is about. ``none`` is for a read command that answers
# from something other than one project's index (doctor, list-projects, the
# eval reports, the plan readers); it says so instead of pretending to cover.
SCOPE_PROJECT = "project"
SCOPE_ANSWER = "answer"
SCOPE_NONE = "none"

# Which question an empty answer is answering. The four reason VALUES are the
# same on every surface; only the human sentence differs, because a name read
# looks for a name in an index of symbols and a search looks for text.
SURFACE_SYMBOL = "symbol"
SURFACE_SEARCH = "search"
SCOPES = (SCOPE_PROJECT, SCOPE_ANSWER, SCOPE_NONE)

# The empty-answer vocabulary. One enum, shared by every name read.
NAME_ABSENT = "name-absent"
FILES_NOT_PARSED = "files-not-parsed"
LANGUAGE_WITHOUT_AST = "language-without-ast"
INDEX_EMPTY = "index-empty"
EMPTY_REASONS = (NAME_ABSENT, FILES_NOT_PARSED, LANGUAGE_WITHOUT_AST,
                 INDEX_EMPTY)

# The manifest's language spelling (``indexer._lang_from_ext``) and the
# capability matrix's (``ts_chunker.EXT_LANG``) differ for these two; the
# answer keeps the spelling the project's own files carry.
_MATRIX_LANGUAGE = {"shell": "bash", "tsx": "typescript"}


@lru_cache(maxsize=1)
def _matrix_rows() -> dict[str, dict[str, Any]]:
    """The capability matrix's own probe, once per process."""
    from .langmatrix import matrix

    return {row["language"]: row for row in matrix()}


@lru_cache(maxsize=1)
def _no_ast_names() -> frozenset[str]:
    """The matrix's declared no-AST languages (``index-status``'s list)."""
    from .langmatrix import languages_without_ast

    return frozenset(languages_without_ast())


def has_symbol_reader(language: str | None) -> bool:
    """True when a file in this language can yield an AST symbol row.

    The verdict is the capability matrix's own runtime probe for the
    languages it maps with fixtures (bash chunks but extracts no symbols), and
    the matrix's declared no-AST list for the languages it names without a
    fixture (markdown, json, yaml, toml). ``unknown`` and ``None`` are a NO:
    nothing attributable can be claimed about a file whose language the index
    cannot read.
    """
    if not language:
        return False
    name = _MATRIX_LANGUAGE.get(language, language)
    row = _matrix_rows().get(name)
    if row is not None:
        return bool(row["symbols"])
    return name not in _no_ast_names()


def freshness(staleness_block: dict[str, Any] | None) -> str:
    """The staleness block's verdict, never a second estimate."""
    block = staleness_block or {}
    if not block.get("stale_known"):
        return "unknown"
    return "stale" if block.get("stale") else "fresh"


def block(files: Iterable[ManifestFile], *, scope: str,
          freshness: str, withheld_count: int = 0) -> dict[str, Any]:
    """The coverage object for one set of file rows."""
    rows = list(files)
    parsed = sum(1 for row in rows if row.parse_verdict == "parsed")
    languages = sorted({row.language for row in rows
                        if row.language and not has_symbol_reader(row.language)})
    not_parsed = len(rows) - parsed
    return {
        "scope": scope,
        "files": len(rows),
        "files_parsed": parsed,
        "files_not_parsed": not_parsed,
        "files_withheld": withheld_count,
        "languages_without_ast": languages,
        "freshness": freshness,
        "complete": not_parsed == 0 and withheld_count == 0,
    }


def none_block(freshness_value: str | None = None) -> dict[str, Any]:
    """Coverage for a read that answers from no index at all.

    The counts are null rather than zero: zero would claim "nothing to parse"
    about an answer that never looked at a project.
    """
    return {
        "scope": SCOPE_NONE,
        "files": None,
        "files_parsed": None,
        "files_not_parsed": None,
        "files_withheld": None,
        "languages_without_ast": [],
        "freshness": freshness_value,
        "complete": None,
    }


def files_in_scope(manifest: Manifest,
                   paths: Sequence[str] | None = None
                   ) -> list[ManifestFile]:
    """The file rows an answer looked at: all of them, or the named ones."""
    rows = manifest.all_files()
    if paths is None:
        return list(rows.values())
    return [rows[path] for path in paths if path in rows]


def withheld_in_scope(staleness_block: dict[str, Any] | None,
                      paths: Sequence[str] | None = None) -> int:
    """Withheld files in the answer's scope.

    A withheld file has no row, so it is counted from the staleness block's
    own withheld list -- the whole count for a project-wide answer, the
    intersection for an answer scoped to named paths.
    """
    block_ = staleness_block or {}
    if paths is None:
        return int(block_.get("withheld_count") or 0)
    held = {entry["file"] for entry in (block_.get("withheld_files") or [])}
    return sum(1 for path in paths if path in held)


def from_manifest(manifest: Manifest,
                  staleness_block: dict[str, Any] | None, *,
                  paths: Sequence[str] | None = None,
                  scope: str = SCOPE_PROJECT) -> dict[str, Any]:
    """Coverage for a whole index, or for the paths an answer looked at.

    ``paths`` narrows the scope to files the answer actually read (one file
    for ``outline``/``get-code-context``, a ``--file`` filter for
    ``find-symbol``); ``None`` means the whole project. A withheld path has no
    file row, so it is counted from the staleness block's own withheld list --
    only for the scope that names it.
    """
    all_files = manifest.all_files()
    verdict = freshness(staleness_block)
    rows = (list(all_files.values()) if paths is None
            else [all_files[path] for path in paths if path in all_files])
    return block(rows, scope=scope, freshness=verdict,
                 withheld_count=withheld_in_scope(staleness_block, paths))


def text_line(coverage: dict[str, Any]) -> str | None:
    """The ONE text line for incomplete coverage; None for a clean answer.

    A clean answer says nothing: the line exists to stop a reader mistaking a
    partially-covered answer for a complete one, so printing it on a complete
    answer would spend the reader's attention on nothing.
    """
    if coverage.get("scope") == SCOPE_NONE or coverage.get("complete") is None:
        return None
    if coverage.get("complete"):
        return None
    held = int(coverage.get("files_withheld") or 0)
    tail = (f"; {held} file{'s' if held != 1 else ''} withheld for privacy"
            if held else "")
    return (f"coverage: {coverage['files_parsed']} of {coverage['files']} "
            f"files parsed, {coverage['files_not_parsed']} not parsed{tail}")


def explain_empty(files: Sequence[ManifestFile],
                  surface: str = SURFACE_SYMBOL) -> tuple[str, str]:
    """``(empty_reason, sentence)`` for an empty answer over these files.

    Precedence, in the card's order: the subject is absent from an index that
    parsed the files that could hold it; the files that would hold it were not
    parsed; the language has no AST reader at all; the project is not indexed.

    ``surface`` changes only the human sentence, never the reason value. A
    name read asks whether a NAME is in an index of symbols, so its sentences
    say "the name"; ``semantic-search`` asks whether any indexed text matches
    its QUERY, so it says so in its own words -- the same four values must not
    be read as a name lookup on the search surface.
    """
    search = surface == SURFACE_SEARCH
    total = len(files)
    if not total:
        return (INDEX_EMPTY,
                "the project is not indexed: the index holds no files")
    parsed = [row for row in files if row.parse_verdict == "parsed"]
    if any(has_symbol_reader(row.language) for row in parsed):
        if search:
            return (NAME_ABSENT,
                    f"nothing in an index that parsed {len(parsed)} of "
                    f"{total} files matched the query")
        return (NAME_ABSENT,
                f"the name is absent from an index that parsed {len(parsed)} "
                f"of {total} files")
    unparsed = [row for row in files if row.parse_verdict != "parsed"]
    if unparsed:
        languages = ", ".join(sorted({row.language or "unknown"
                                       for row in unparsed}))
        subject = "the query" if search else "the name"
        return (FILES_NOT_PARSED,
                f"{len(unparsed)} of {total} files were not parsed "
                f"({languages}), so {subject} may live in files the index "
                "never read")
    language_names = sorted({row.language or "unknown" for row in parsed})
    languages = ", ".join(language_names)
    verb = "has" if len(language_names) == 1 else "have"
    if search:
        return (LANGUAGE_WITHOUT_AST,
                f"{languages} {verb} no AST reader, so no indexed symbol "
                "comes from the files that would hold the query")
    return (LANGUAGE_WITHOUT_AST,
            f"{languages} {verb} no AST reader, so no symbol is extracted "
            "from the files that would hold it")


def empty_answer(lead: str, files: Sequence[ManifestFile],
                 surface: str = SURFACE_SYMBOL) -> tuple[str, str]:
    """``(empty_reason, the full sentence)``, cause appended to the lead."""
    reason, detail = explain_empty(files, surface)
    return reason, f"{lead}: {detail}"
