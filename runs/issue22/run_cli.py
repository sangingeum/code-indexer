"""Run the code-indexer CLI from whichever `code_indexer` package is on
PYTHONPATH (`python -m code_indexer` starts the MCP server instead)."""

from code_indexer.cli import app

app()
