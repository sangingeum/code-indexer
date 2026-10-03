"""Shared output contract + error taxonomy (output-contract work item).

CLI surface rules:
- stdout carries ONLY the result (data lines).
- Diagnostics go to stderr, and only with --verbose (or on failure).
- The only non-verbose stderr output on success is the documented single-line
  stale-file Warning (`Warning: file changed since last index; line numbers
  may be shifted`).
- Failures print exactly one line `ErrorType: description` on stderr and exit
  with the taxonomy's exit code.

Exit codes: 0 success; 1 failure (any error below); 2 usage/argument error
(Typer already emits 2 for CLI parsing errors — ArgumentError keeps that).

Taxonomy: ArgumentError (2), ConfigError (1), NotFoundError (1),
BackendError (1), LockedError (1), InternalError (1).

JSON outputs carry "schema": 1 and stable field names (docs/json-schema.md).
"""

from __future__ import annotations

import sys

# Exit codes shared by CLI and tests.
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


class IndexerError(Exception):
    """Base for the documented taxonomy; subclasses set exit_code."""

    error_type = "InternalError"
    exit_code = EXIT_FAIL

    def render(self) -> str:
        return f"{self.error_type}: {self}"


class ArgumentError(IndexerError):
    error_type = "ArgumentError"
    exit_code = EXIT_USAGE


class ConfigError(IndexerError):
    error_type = "ConfigError"
    exit_code = EXIT_FAIL


class NotFoundError(IndexerError):
    error_type = "NotFoundError"
    exit_code = EXIT_FAIL


class BackendError(IndexerError):
    error_type = "BackendError"
    exit_code = EXIT_FAIL


class LockedError(IndexerError):
    error_type = "LockedError"
    exit_code = EXIT_FAIL


class InternalError(IndexerError):
    error_type = "InternalError"
    exit_code = EXIT_FAIL


def classify(exc: BaseException) -> IndexerError:
    """Map any exception onto the taxonomy (mechanical, no content change).

    Existing error strings keep their meaning: known messages map to their
    historical type (NotFoundError-ish 'not registered'/'no project',
    ConfigError from the fingerprint/embedder stays ConfigError, lock
    contention becomes LockedError, everything else InternalError). The
    CLI's legacy 'error: ' prefix is stripped so the render is exactly
    'ErrorType: description'.
    """
    if isinstance(exc, IndexerError):
        return exc
    from .fingerprint import ConfigError as LegacyConfigError
    if isinstance(exc, LegacyConfigError):
        return ConfigError(_strip_prefix(str(exc)))
    text = _strip_prefix(str(exc))
    if ("not registered" in text or "no projects registered" in text
            or "path does not exist" in text or "no results for project" in text):
        return NotFoundError(text)
    if "lock" in text.lower() or "indexing in progress" in text.lower():
        return LockedError(text)
    if "ollama" in text.lower() or "qdrant" in text.lower():
        return BackendError(text)
    return InternalError(text)


def _strip_prefix(text: str) -> str:
    return text[len("error: "):] if text.startswith("error: ") else text


def fail(exc: BaseException) -> None:
    """Print exactly one taxonomy line to stderr and exit (CLI seam)."""
    err = classify(exc)
    print(err.render(), file=sys.stderr)
    raise SystemExit(err.exit_code)