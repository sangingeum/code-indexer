"""Symbol identity helpers shared by the query surfaces (ADR-0002 section 4).

A structural answer is only as good as the syntax behind it. The reference
store holds attributed rows (`ast`) for the files a grammar covers and textual
rows (`heuristic`) for every other language — and on this repository's own
index 91.8% of reference rows are textual. So `--inherits` and `--has-call`
answer from attributed syntax ONLY, and the answer must say where it stands:

* ``ast`` — the row was extracted by a parser, so the claim is attributed;
* ``heuristic`` — the row came from a textual scan (no grammar, or a parse
  that fell back), so the claim is a navigation aid;
* ``unknown`` — the file has no AST extractor (or its parse failed), so NOTHING
  is attributed for it. `unknown` is never a negative: it is the statement
  that absence of an attributed call is not absence of a call.

The evidence of a symbol belongs to its FILE, not to the query, so it is
read from the file's parse verdict and carried on the row by the filtered
read (``Manifest.find_symbols``).
"""

from __future__ import annotations

from .manifest import SymbolRow

EVIDENCE_AST = "ast"
EVIDENCE_HEURISTIC = "heuristic"
EVIDENCE_UNKNOWN = "unknown"
EVIDENCE_VALUES = (EVIDENCE_AST, EVIDENCE_HEURISTIC, EVIDENCE_UNKNOWN)


def evidence_of(row: SymbolRow) -> str:
    """``ast`` | ``heuristic`` | ``unknown`` for one symbol row.

    ``parse_verdict`` is the recorded verdict for the row's file (`parsed`,
    `parse_error`, `parse_unavailable`). Only a parsed file carries attributed
    syntax; a textual row inside one is a heuristic answer; anything else is
    unknown. When the read did not join the file row, the extraction's own
    ``source`` is all there is — an ``ast`` row was attributed when it was
    written, a textual one is not attributable at all.
    """
    verdict = row.parse_verdict
    if verdict is None:
        return EVIDENCE_AST if row.source == "ast" else EVIDENCE_UNKNOWN
    if verdict == "parsed":
        return EVIDENCE_AST if row.source == "ast" else EVIDENCE_HEURISTIC
    return EVIDENCE_UNKNOWN


def unattributed_note(name: str, files_without_ast: int) -> str:
    """The explanation for a zero from an attributed-syntax filter.

    A zero is reported, never converted into a negative: the files without an
    AST extractor were not examined, so their references are unknown.
    """
    return (
        f"no symbol carries an attributed reference to {name!r}; "
        f"{files_without_ast} file(s) holding symbols have no AST extractor, "
        "so their references are unknown, not absent")
