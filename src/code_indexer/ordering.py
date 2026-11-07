"""Ordering policy for the structural answers (query-time only).

Every structural list this package returns -- `find-definition`,
`find-symbol`, `find-references`, `find-callers`, and the two `overview`
hotspot lists -- is ordered by ONE policy, applied in this order:

1. **File class**: source, then tests, then docs, then generated/artifact.
   Test fixtures and audit documents accumulate identifier mentions BECAUSE
   they exercise or quote the code under them, so a list ordered by count or
   by path alone reports the fixture as the codebase's centre. The class is a
   property of the path, so the order does not move with the index's
   incidental hit distribution.
2. **Identity quality**: a hit that can name what it matched -- an exact
   name, a recorded kind, a target that resolves to a definition, the
   caller's own symbol -- sorts above one that merely shares a name, and a
   hit that cannot name its match sorts last.
3. **A deterministic tie-break** over the row's identity fields, never over
   insertion order: the same index answers the same way twice.

This is an ORDER, not a filter. Every hit is still returned, with the
demoted ones after the promoted ones. A surface that caps its answer keeps
its existing cap and its existing default; the policy decides which hits
fill it, so a demoted hit can fall outside a capped answer -- it is never
dropped from an uncapped one.

One classifier serves every surface, so a path cannot be "tests" to the
overview and "source" to `find-references`. The prose form of this policy
lives in `skill/references/query.md` (Ordering); the SQL form used where a
read is capped below the ranking (the symbol and reference reads) mirrors
these keys and calls ``class_weight`` as the SQLite function
``file_class_weight`` rather than restating the path rules.
"""

from __future__ import annotations

SOURCE = "source"
TESTS = "tests"
DOCS = "docs"
GENERATED = "generated"

#: The class order, promoted first. The weight is the sort position.
CLASS_ORDER: tuple[str, ...] = (SOURCE, TESTS, DOCS, GENERATED)
CLASS_WEIGHT: dict[str, int] = {name: index for index, name in
                                enumerate(CLASS_ORDER)}

# Build output and dependency caches: a copy of somebody's code, or a
# machine-written file, never the tree's own source.
_GENERATED_DIRS = frozenset({
    ".mypy_cache", ".next", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "__pycache__", "build", "coverage", "dist", "htmlcov",
    "node_modules", "out", "site-packages", "target", "venv",
})
_GENERATED_SUFFIXES = (
    ".designer.cs", ".freezed.dart", ".g.cs", ".g.dart", ".map",
    ".min.css", ".min.js", ".pb.go", "_pb2.py", "_pb2_grpc.py",
)
_GENERATED_MARKERS = ("_generated.", ".generated.")
_GENERATED_NAMES = frozenset({
    "cargo.lock", "composer.lock", "gemfile.lock", "package-lock.json",
    "pnpm-lock.yaml", "poetry.lock", "uv.lock", "yarn.lock",
})

_DOC_DIRS = frozenset({"doc", "docs", "documentation"})
_DOC_SUFFIXES = (".adoc", ".asciidoc", ".markdown", ".md", ".rst", ".txt")

_TEST_DIRS = frozenset({"__tests__", "spec", "specs", "test", "tests"})
_TEST_BASENAMES = frozenset({"conftest.py"})
_TEST_PREFIXES = ("test_", "test-")
_TEST_SUFFIXES = (
    ".spec.js", ".spec.jsx", ".spec.ts", ".spec.tsx",
    ".test.js", ".test.jsx", ".test.ts", ".test.tsx",
    "_test.c", "_test.cc", "_test.cpp", "_test.cxx", "_test.go",
    "_test.py", "_test.rs",
)
# Camel-case test names carry the marker with a capital: `FooTests.cs`,
# `FooTest.java`. Case-sensitive so `Contest.cs` is not a test.
_TEST_CAMEL_SUFFIXES = ("Test.cs", "Tests.cs", "Test.java", "Tests.java",
                        "Test.kt", "Tests.kt")


def _is_test_basename(base: str) -> bool:
    if base in _TEST_BASENAMES or base.startswith(_TEST_PREFIXES):
        return True
    if base.endswith(_TEST_SUFFIXES):
        return True
    return base.endswith(_TEST_CAMEL_SUFFIXES)


def file_class(path: str) -> str:
    """``source`` | ``tests`` | ``docs`` | ``generated`` for one project path.

    Rules, in the order they are applied (so a generated document under
    ``build/`` is still an artifact, and a Markdown file under ``tests/`` is
    still a document):

    * generated/artifact -- a build or dependency-cache directory, a lock
      file, a minified asset or source map, or a ``*_generated*`` / ``*.g.cs``
      / ``*.pb.go`` / ``*_pb2.py`` style output;
    * docs -- a ``docs``/``doc``/``documentation`` directory, or a Markdown,
      reStructuredText, AsciiDoc or plain-text extension anywhere;
    * tests -- a ``tests``/``test``/``spec``/``__tests__`` directory (a C#
      ``Foo.Tests`` project too), ``conftest.py``, a ``test_*`` name, or a
      ``*_test.py`` / ``*.spec.ts`` / ``*Tests.cs`` style name;
    * source -- everything else. Unknown shapes stay source: demoting a file
      this classifier does not recognise would hide it behind a fixture.
    """
    normalised = str(path).replace("\\", "/")
    parts = [part for part in normalised.split("/") if part and part != "."]
    if not parts:
        return SOURCE
    base = parts[-1]
    lowered = base.lower()
    segments = [part.lower() for part in parts[:-1]]
    if (any(segment in _GENERATED_DIRS for segment in segments)
            or lowered in _GENERATED_NAMES
            or lowered.endswith(_GENERATED_SUFFIXES)
            or any(marker in lowered for marker in _GENERATED_MARKERS)):
        return GENERATED
    if (any(segment in _DOC_DIRS for segment in segments)
            or lowered.endswith(_DOC_SUFFIXES)):
        return DOCS
    if (any(segment in _TEST_DIRS
            or segment.endswith(".tests") or segment.endswith(".test")
            for segment in segments)
            or _is_test_basename(base)):
        return TESTS
    return SOURCE


def class_weight(path: str) -> int:
    """The class sort position: 0 source, 1 tests, 2 docs, 3 generated.

    Registered on every manifest connection as the SQLite function
    ``file_class_weight`` so a capped SQL read orders by the SAME classifier
    the Python surfaces use.
    """
    return CLASS_WEIGHT[file_class(path)]


def _same_name(left: str | None, right: str | None) -> bool:
    return (left or "").casefold() == (right or "").casefold()


def symbol_key(row, query_name: str | None = None) -> tuple:
    """Sort key for one symbol row (``SymbolRow``; ``QueryHit``-shaped).

    ``query_name`` is the name the caller asked for: a row carrying it is an
    exact hit, a row that does not (the substring tier) merely shares it. A
    row with no recorded ``symbol_type`` cannot say what it matched, so it
    sorts after its class's named rows; an attributed (``ast``) row then sorts
    above a textual one. The tail is identity data, so no tie is left to
    insertion order.
    """
    return (
        class_weight(row.file),
        0 if query_name is None or _same_name(row.name, query_name) else 1,
        0 if row.symbol_type else 1,
        0 if row.source == "ast" else 1,
        row.file,
        row.start_line,
        row.qualified_name or "",
        row.decl_hash or "",
        row.name,
    )


def edge_key(file: str, symbol: str | None, line: int | None,
             relationship: str, confidence: str,
             receiver: str | None = None) -> tuple:
    """Sort key for one graph edge/call site, used by `find-callers`.

    The identity clause is the caller: a hit that names its enclosing symbol
    sorts above a module-level or otherwise unattributed one, and an
    attributed (``ast``) row above a textual one.
    """
    return (
        class_weight(file),
        0 if symbol else 1,
        0 if confidence == "ast" else 1,
        file,
        line if line is not None else 0,
        symbol or "",
        relationship or "",
        receiver or "",
    )


def hotspot_key(file: str, value: int) -> tuple:
    """Sort key for one overview hotspot: class first, then the count.

    ``value`` is fan-in or LOC; both lists use the same policy, so a big test
    file or an audit document does not win orientation.
    """
    return (class_weight(file), -value, file)
