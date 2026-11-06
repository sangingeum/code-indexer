"""Shared output contract + error taxonomy (output-contract work item).

CLI surface rules:
- stdout carries ONLY the result (data lines).
- Diagnostics go to stderr, and only on failure (or with --verbose).
- The only non-verbose stderr output on success is the documented single-line
  staleness Warning (see ``staleness.warning_line``).
- Failures print exactly one line `ErrorType: description` on stderr and exit
  with the taxonomy's exit code.

Exit codes (ADR-0003 section 5):

| Code | Meaning |
|---|---|
| 0 | success, including zero hits -- "no results" is an answer |
| 1 | any other runtime failure |
| 2 | usage or argument error |
| 3 | backend or extension unavailable (nothing reachable/installed) |
| 4 | the index requires a rebuild (fingerprint mismatch, needs-reindex) |
| 5 | lock or concurrency conflict (the same meaning filekeeper gives 5) |

Taxonomy: ArgumentError (2), BackendError (3), RebuildRequiredError (4),
LockedError (5), ConfigError (1), NotFoundError (1), InternalError (1).

JSON outputs carry "schema": 1 and stable field names (docs/json-schema.md).
"""

from __future__ import annotations

import sys
from typing import NoReturn

# Exit codes shared by CLI and tests.
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BACKEND = 3
EXIT_REINDEX = 4
EXIT_LOCK = 5


class IndexerError(Exception):
    """Base for the documented taxonomy; subclasses set exit_code."""

    error_type = "InternalError"
    exit_code = EXIT_FAIL

    def render(self) -> str:
        return f"{self.error_type}: {self}"


class ArgumentError(IndexerError):
    error_type = "ArgumentError"
    exit_code = EXIT_USAGE


class AmbiguousSymbolError(ArgumentError):
    """A name that must resolve to ONE symbol resolved to several.

    Renders as ``ArgumentError: ...`` (exit 2) and carries ``candidates`` —
    the same records the JSON ambiguity envelope reports — so a machine
    caller receives the list as data, not only as prose. The message names
    every candidate and the disambiguators that exist, because "pick one"
    is not an instruction a caller can act on.
    """

    def __init__(self, name: str, candidates: list[dict]) -> None:
        self.name = name
        self.candidates = candidates
        listed = "; ".join(
            f"{c['file']}:{c['range']['start_line']}-{c['range']['end_line']}"
            f" ({c.get('kind')}, container={c.get('container')},"
            f" decl_hash={c.get('decl_hash')})" for c in candidates)
        super().__init__(
            f"name {name!r} resolves to {len(candidates)} symbols and this "
            "command needs exactly one — narrow it with --file, --kind or "
            f"--container, or list them with find-symbol: {listed}")


class ConfigError(IndexerError):
    error_type = "ConfigError"
    exit_code = EXIT_FAIL


class NotFoundError(IndexerError):
    error_type = "NotFoundError"
    exit_code = EXIT_FAIL


class BackendError(IndexerError):
    """Nothing installed or reachable: the remedy is to install or start it."""

    error_type = "BackendError"
    exit_code = EXIT_BACKEND


class ExtensionUnavailableError(ConfigError):
    """An optional extra is not installed (ADR-0001 section 1).

    Renders as ``ConfigError: <message>`` because the absence is a
    configuration fact, and exits 3 because ADR-0003 gives "extension
    unavailable" that code. One actionable line, no traceback.
    """

    exit_code = EXIT_BACKEND


class RebuildRequiredError(IndexerError):
    """The index was built by a different configuration and must be rebuilt."""

    error_type = "RebuildRequiredError"
    exit_code = EXIT_REINDEX


class VectorsDisabledError(ConfigError):
    """Semantic search was asked of a project kept with vectors off.

    A configuration fact (the project records ``vectors=off``), rendered as
    ``ConfigError`` like the absent-extension case, but exit 4: ADR-0003's
    rebuild class names "vectors absent" explicitly, and the remedy is a
    rebuild of the vector side (``--vectors on``). The alternative number, 3,
    would tell a caller to install or start a service -- which would not
    enable vectors on an index that records off.
    """

    exit_code = EXIT_REINDEX


class LockedError(IndexerError):
    error_type = "LockedError"
    exit_code = EXIT_LOCK


class InternalError(IndexerError):
    error_type = "InternalError"
    exit_code = EXIT_FAIL


def classify(exc: BaseException) -> IndexerError:
    """Map any exception onto the taxonomy (mechanical, no content change).

    Existing error strings keep their meaning: known messages map to their
    historical type (NotFoundError-ish 'not registered'/'no project', lock
    contention becomes LockedError, a fingerprint mismatch (the message names
    ``reindex-project``) becomes RebuildRequiredError, a missing model or an
    unreachable service becomes BackendError, everything else InternalError).
    The CLI's legacy 'error: ' prefix is stripped so the render is exactly
    'ErrorType: description'.
    """
    if isinstance(exc, IndexerError):
        return exc
    from .fingerprint import ConfigError as LegacyConfigError
    text = _strip_prefix(str(exc))
    if isinstance(exc, LegacyConfigError):
        # The fingerprint/configuration family. A mismatch is a rebuild (4);
        # an absent model or unreachable embedder is a missing install (3).
        if "reindex-project" in text or "does not match the current" in text:
            return RebuildRequiredError(text)
        return BackendError(text)
    if ("not registered" in text or "no projects registered" in text
            or "path does not exist" in text or "no results for project" in text
            # watch could not start because nothing requested is watchable —
            # the same "what you asked for is not there" family.
            or "no watchable project roots" in text):
        return NotFoundError(text)
    if "indexing in progress" in text.lower() or "lock" in text.lower():
        return LockedError(text)
    if ("ollama" in text.lower() or "qdrant" in text.lower()
            or "embedding service" in text.lower()
            or "connection refused" in text.lower()):
        return BackendError(text)
    return InternalError(text)


def _strip_prefix(text: str) -> str:
    return text[len("error: "):] if text.startswith("error: ") else text


def fail(exc: BaseException) -> NoReturn:
    """Print exactly one taxonomy line to stderr and exit (CLI seam)."""
    err = classify(exc)
    print(err.render(), file=sys.stderr)
    raise SystemExit(err.exit_code)
