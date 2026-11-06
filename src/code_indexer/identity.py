"""Symbol identity helpers shared by the query surfaces (ADR-0002 section 4).

A structural answer is only as good as the syntax behind it. The reference
store holds attributed rows (`ast`) for the files a grammar covers and textual
rows (`heuristic`) for every other language — and on this repository's own
index 91.8% of reference rows are textual. So `--inherits` and `--has-call`
answer from attributed syntax ONLY, and the answer must say where it stands:

* ``ast`` — the row was extracted by a parser, so the claim is attributed;
* ``heuristic`` — RESERVED, and produced by nothing today. The chunker labels
  every row of a parsed file ``ast`` and takes the regex fallback only
  together with a ``parse_unavailable`` verdict, whose rows are ``unknown``,
  so no symbol row can be ``heuristic``. The value stays in the vocabulary
  for a future extractor that supplements an AST with textual rows; it is not
  a state a row is in now, and no description should sell it as one;
* ``unknown`` — the file has no AST extractor (or its parse failed), so NOTHING
  is attributed for it. `unknown` is never a negative: it is the statement
  that absence of an attributed call is not absence of a call.

The evidence of a symbol belongs to its FILE, not to the query, so it is
read from the file's parse verdict and carried on the row by the filtered
read (``Manifest.find_symbols``). One column list and this one function serve
every surface that reports evidence, so one payload cannot answer two ways
about one row.
"""

from __future__ import annotations

from .errors import AmbiguousSymbolError, NotFoundError
from .manifest import SymbolRow

EVIDENCE_AST = "ast"
EVIDENCE_HEURISTIC = "heuristic"
EVIDENCE_UNKNOWN = "unknown"


def evidence_of(row: SymbolRow) -> str:
    """``ast`` | ``unknown`` for one symbol row (``heuristic`` is reserved).

    ``parse_verdict`` is the recorded verdict for the row's file (`parsed`,
    `parse_error`, `parse_unavailable`). Only a parsed file carries attributed
    syntax; anything else is unknown. When the read carries no verdict at all
    the extraction's own ``source`` is all there is — an ``ast`` row was
    attributed when it was written, a textual one is not attributable.

    Every surface that reports evidence calls THIS function over the one
    symbol column list, which joins the verdict in; a surface that skipped the
    join would label the same row differently from its neighbour.
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


# -- the ambiguity envelope (ADR-0002 section 5) --------------------------

def candidate(row: SymbolRow) -> dict:
    """One disambiguating record: everything needed to pick this symbol.

    The field set is the one the identity record names — file, range, kind,
    container, signature, language and decl_hash — plus the qualified name
    and the evidence label, so a caller can tell two same-shaped rows apart
    without a second read.
    """
    return {
        "file": row.file,
        "range": {"start_line": row.start_line, "end_line": row.end_line},
        "kind": row.symbol_type,
        "container": row.container,
        "signature": row.signature,
        "language": row.language,
        "decl_hash": row.decl_hash,
        "qualified_name": row.qualified_name,
        "evidence": evidence_of(row),
    }


def ambiguity_block(rows: list[SymbolRow]) -> dict:
    """``ambiguity`` for an answer whose name matched more than one symbol.

    Every candidate is reported. A shorter list would be a silent pick with
    extra steps — exactly what the identity record refuses.
    """
    return {"candidates": [candidate(row) for row in rows]}


def resolve_one(name: str, rows: list[SymbolRow]) -> SymbolRow:
    """The ONE symbol a single-symbol command may act on.

    No rows is ``NotFoundError`` (exit 1, the documented empty answer of
    ``--exactly-one``); more than one is ``AmbiguousSymbolError`` (an
    ``ArgumentError``, exit 2) carrying every candidate. A caller that
    cannot disambiguate must refuse and show the list, never take a row that
    happened to sort first.
    """
    if not rows:
        raise NotFoundError(f"no indexed definition for {name!r}")
    if len(rows) > 1:
        raise AmbiguousSymbolError(name, [candidate(row) for row in rows])
    return rows[0]
