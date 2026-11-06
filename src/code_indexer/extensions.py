"""Optional-extension availability (ADR-0001 section 1).

The answer is a module lookup, never an import: asking whether the semantic
extension is installed must not cost a client library or a service
configuration. The import itself happens at the point of use, inside the
embedder's and the store's constructors.
"""

from __future__ import annotations

import importlib.util

SEMANTIC_EXTENSION = "semantic"
SEMANTIC_PACKAGES = ("ollama", "qdrant_client")

SEMANTIC_EXTENSION_MESSAGE = (
    "the semantic extension is not installed; install code-indexer[semantic]")

MCP_PACKAGES = ("mcp",)
MCP_EXTENSION_MESSAGE = (
    "the MCP extension is not installed; install code-indexer[mcp]")


def _installed(packages: tuple[str, ...]) -> bool:
    return all(importlib.util.find_spec(name) is not None for name in packages)


def semantic_available() -> bool:
    """True when both client libraries of the semantic extra are importable."""
    return _installed(SEMANTIC_PACKAGES)


def mcp_available() -> bool:
    """True when the MCP SDK of the MCP extra is importable."""
    return _installed(MCP_PACKAGES)
