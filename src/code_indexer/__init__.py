"""code-indexer: a code index over a repository, queried by one-shot CLI.

Importing this package costs no client library and no service configuration:
``code_indexer.__init__`` imports no submodule. The CLI lives in
``code_indexer.cli``; the MCP server lives in ``code_indexer.server`` and is
imported only by the ``code-indexer-mcp`` entry point.
"""

__all__: list[str] = []
