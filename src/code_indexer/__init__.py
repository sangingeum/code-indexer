"""code-indexer: semantic code index via Ollama + Qdrant.

Two entry points over the same core (code_indexer.core):
- ``code-indexer-mcp`` — MCP server (server.py, stdio transport)
- ``code-indexer``     — one-shot CLI (cli.py, typer)
"""

from .server import main

__all__ = ["main"]
