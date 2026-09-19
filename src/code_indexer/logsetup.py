"""Quiet-by-default logging policy for the house indexer/memory tools.

lib_httpx / qdrant-client / ollama log at INFO and dump raw HTTP traffic
(incl. LAN IPs) to whatever stream the root handler uses — that pollutes
chat transcripts. Policy:

- stdout carries RESULTS ONLY, never logs.
- diagnostics go to stderr, at WARNING by default.
- ``--verbose`` (or VERBOSE=1) opts in to INFO on stderr.

Call :func:`configure_logging` once at each entry point (CLI main and MCP
server main) BEFORE any client is constructed.
"""

from __future__ import annotations

import logging
import os
import sys

NOISY_LIBRARY_LOGGERS = ("httpx", "httpcore", "qdrant_client", "ollama")


def configure_logging(verbose: bool = False) -> None:
    """Demote third-party HTTP loggers to WARNING; route logs to stderr.

    Safe to call multiple times (idempotent for repeated handler setup).
    ``verbose=True`` or env VERBOSE=1 raises everything to INFO for opt-in
    diagnostics.
    """
    is_verbose = verbose or os.environ.get("VERBOSE", "") not in ("", "0")
    level = logging.INFO if is_verbose else logging.WARNING

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    for name in NOISY_LIBRARY_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False
        if not logger.handlers:
            logger.addHandler(handler)

    # Our own packages: WARNING by default too (their loggers carry
    # informational pass details that pollute one-shot CLI output).
    for name in ("code-indexer", "vector-memory"):
        logging.getLogger(name).setLevel(level)
