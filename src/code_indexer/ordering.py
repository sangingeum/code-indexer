"""Ordering policy for the structural answers (query-time only).

Every RANKED structural answer is ordered by one policy: file class first,
then identity quality, then a deterministic tie-break over the row's own
identity fields. This is an ORDER, not a filter: every hit is still returned,
the demoted ones after the promoted ones. A surface that caps its answer keeps
its cap and its default; the policy decides which hits fill the cap, so a
demoted hit can fall outside a capped answer and never out of an uncapped one.

The surfaces, and the keys that can fire on each one:

* `find-definition` and exact-name `find-symbol` -- class, recorded kind,
  attribution (``ast`` before a textual row), then name, file, start line,
  qualified name, declaration hash. The exact-name tier is enforced by the
  QUERY, not by a key: the substring tier runs only when the exact tier is
  empty, so no row of one answer can be "a substring hit" beside an exact one.
* browse-mode `find-symbol` (no name, filters only) -- the same key list; its
  identity clause is constant there, because there is no queried name to match,
  so class leads and the name is the first tie-break.
* `find-references` -- class, whether the reference sits in a named enclosing
  symbol (a call inside `Runner.run` above a module-level mention), attribution,
  then file, line, relationship, enclosing symbol, receiver.
* `find-callers` -- class, named caller above module-level, attribution, then
  file, line, caller, relationship, receiver.
* `overview` hotspots (fan-in and size) -- class, then the count descending,
  then file.

Two surfaces deliberately do NOT use it, for the same reason:

* `find-callees` returns call sites in store order (line), because the answer is
  "what does this symbol reach, and where" -- a reading of one body, not a
  relevance list over the tree -- and its cap counts nodes of a walk whose
  levels would have to be re-ordered together to change it.
* `deps` DOES order each level by class before the node cap fills (see
  `graph.deps_for_file`); it is named here because it is a walk, and a walk's
  levels are re-sorted rather than globally ranked.

Case policy, one per surface: the symbol read compares names with SQLite
``COLLATE NOCASE`` (ASCII case-insensitive), and no other surface compares
names at all -- the reference read matches ``to_name = ?`` exactly and orders
by non-name fields, the graph keys compare paths and integers. Non-ASCII
identifiers therefore compare exactly, in every surface, because no surface
case-folds them.

One classifier serves every surface, so a path cannot be "tests" to the
overview and "source" to `find-references`. The prose form of this policy
lives in `skill/references/query.md` (Ordering); the SQL form used where a
read is capped below the ranking (the symbol and reference reads) calls
``class_weight`` as the SQLite function ``file_class_weight`` rather than
restating the path rules.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

SOURCE = "source"
EXAMPLES = "examples"
TESTS = "tests"
DOCS = "docs"
GENERATED = "generated"

#: The class order, promoted first. The weight is the sort position.
CLASS_ORDER: tuple[str, ...] = (SOURCE, EXAMPLES, TESTS, DOCS, GENERATED)
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
# The documents every repository has, spelled without an extension. Their
# `.md` / `.txt` / `.rst` variants are documents by SUFFIX already; this set is
# only about the bare name, which no suffix rule can see. Matching is exact and
# case-insensitive, so `README.weird` stays an unknown shape (source) instead
# of being guessed into the docs rung.
_DOC_BASENAMES = frozenset({
    "authors", "changelog", "contributing", "copying", "licence",
    "license", "notice", "readme",
})

# Sample code shipped to be read, not implementation: after source (it can
# demonstrate the API) and before tests (it is not evidence about this tree).
_EXAMPLE_DIRS = frozenset({"demo", "demos", "example", "examples", "sample",
                           "samples"})

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


@dataclass(frozen=True)
class ClassNames:
    """The DIRECTORY names each rung recognises, from the classifier.

    The built-in ladder is one value of this type; a project's
    ``[tool.code-indexer.ordering]`` table adds names to a rung (see
    ``load_class_names``), and both the Python surfaces and the SQL function
    read the same value, so the two layers cannot disagree.
    """

    docs: frozenset[str] = _DOC_DIRS
    examples: frozenset[str] = _EXAMPLE_DIRS
    tests: frozenset[str] = _TEST_DIRS


DEFAULT_CLASS_NAMES = ClassNames()


def load_class_names(project_path: str | None) -> ClassNames:
    """The project's ladder: the built-in names plus any it adds.

    Read from ``[tool.code-indexer.ordering]`` in the project root's
    ``pyproject.toml``: ``docs``, ``examples`` and ``tests`` are lists of
    DIRECTORY names that join the built-in rung of the same name. Extra names
    are additive, so an override can add a rung member but can never remove
    one: a project cannot make ``tests/`` source by omission.

    An absent, unreadable or malformed file yields the built-in ladder -- a
    read answers with the default policy rather than failing over config.
    """
    if not project_path:
        return DEFAULT_CLASS_NAMES
    try:
        with open(Path(project_path) / "pyproject.toml", "rb") as fh:
            table = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_CLASS_NAMES
    section = table.get("tool", {}).get("code-indexer", {}).get(
        "ordering", {})
    if not isinstance(section, dict):
        return DEFAULT_CLASS_NAMES

    def _names(key: str, builtin: frozenset[str]) -> frozenset[str]:
        extra = section.get(key)
        if not isinstance(extra, list):
            return builtin
        return builtin | {str(name).strip("/").lower() for name in extra
                          if str(name).strip("/")}

    return ClassNames(docs=_names("docs", _DOC_DIRS),
                      examples=_names("examples", _EXAMPLE_DIRS),
                      tests=_names("tests", _TEST_DIRS))


def file_class(path: str, names: ClassNames = DEFAULT_CLASS_NAMES) -> str:
    """``source`` | ``examples`` | ``tests`` | ``docs`` | ``generated``.

    Rules, in the order they are applied (so a generated document under
    ``build/`` is still an artifact, and a Markdown file under ``tests/`` is
    still a document):

    * generated/artifact -- a build or dependency-cache directory, a lock
      file, a minified asset or source map, or a ``*_generated*`` / ``*.g.cs``
      / ``*.pb.go`` / ``*_pb2.py`` style output;
    * docs -- a ``docs``/``doc``/``documentation`` directory, a Markdown,
      reStructuredText, AsciiDoc or plain-text extension anywhere, or one of
      the extensionless document names (``LICENSE``, ``README``, ``NOTICE``,
      ``CHANGELOG``, ...) a repository keeps without a suffix;
    * tests -- a ``tests``/``test``/``spec``/``__tests__`` directory (a C#
      ``Foo.Tests`` project too), ``conftest.py``, a ``test_*`` name, or a
      ``*_test.py`` / ``*.spec.ts`` / ``*Tests.cs`` style name -- checked
      before examples, so a test inside a sample tree stays a test;
    * examples -- an ``examples``/``example``/``sample``/``demo`` directory:
      code shipped to be read, so it promotes above tests and docs but never
      above the tree's own source;
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
    if (any(segment in names.docs for segment in segments)
            or lowered.endswith(_DOC_SUFFIXES)
            or lowered in _DOC_BASENAMES):
        return DOCS
    if (any(segment in names.tests
            or segment.endswith(".tests") or segment.endswith(".test")
            for segment in segments)
            or _is_test_basename(base)):
        return TESTS
    if any(segment in names.examples for segment in segments):
        return EXAMPLES
    return SOURCE


def class_weight(path: str, names: ClassNames = DEFAULT_CLASS_NAMES) -> int:
    """The class sort position: 0 source, 1 examples, 2 tests, 3 docs,
    4 generated.

    Registered on every manifest connection as the SQLite function
    ``file_class_weight``, closed over the same ``names`` the Python surfaces
    use, so a capped SQL read orders by the SAME classifier.
    """
    return CLASS_WEIGHT[file_class(path, names)]


def edge_key(file: str, symbol: str | None, line: int | None,
             relationship: str, confidence: str,
             receiver: str | None = None,
             names: ClassNames = DEFAULT_CLASS_NAMES) -> tuple:
    """Sort key for one graph edge/call site, used by `find-callers`.

    The identity clause is the caller: a hit that names its enclosing symbol
    sorts above a module-level or otherwise unattributed one, and an
    attributed (``ast``) row above a textual one. This is the Python half of
    the reference order; the store's `find-references` expresses the same
    keys in SQL (``Manifest.refs_to``).
    """
    return (
        class_weight(file, names),
        0 if symbol else 1,
        0 if confidence == "ast" else 1,
        file,
        line if line is not None else 0,
        symbol or "",
        relationship or "",
        receiver or "",
    )


def hotspot_key(file: str, value: int,
                names: ClassNames = DEFAULT_CLASS_NAMES) -> tuple:
    """Sort key for one overview hotspot: class first, then the count.

    ``value`` is fan-in or LOC; both lists use the same policy, so a big test
    file or an audit document does not win orientation.
    """
    return (class_weight(file, names), -value, file)
