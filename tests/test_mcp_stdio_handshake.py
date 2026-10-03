"""Live stdio handshake smoke test (CI-01): the real MCP server process.

Launches ``code-indexer-mcp`` as a subprocess, performs the JSON-RPC
``initialize`` + ``tools/list`` handshake over stdio, and asserts the 17
documented tools are present with stdout reserved for the MCP transport
(non-MCP lines would break the protocol). No Ollama/Qdrant traffic: the
handshake path never touches the backends, so the test passes with them
unreachable or absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

DOCUMENTED_TOOLS = frozenset({
    "lookup_project", "add_project", "remove_project", "list_projects",
    "semantic_search", "index_status", "reindex_project", "find_symbol",
    "find_symbols", "skeleton", "outline", "find_definition",
    "find_references", "find_callers", "find_callees", "deps",
    "get_code_context",
})


def _server_command(repo_root: str) -> list[str]:
    """Resolve the server launch command without requiring an install."""
    # Installed entry point first (uv tool installs put it on PATH).
    if shutil.which("code-indexer-mcp"):
        return ["code-indexer-mcp"]
    return [sys.executable, "-m", "code_indexer.server"]


def _send(proc: subprocess.Popen, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _read_response(proc: subprocess.Popen, want_id: int) -> dict:
    """Read one JSON-RPC response line; fail loudly on non-MCP stdout."""
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            raise AssertionError(
                "server stdout closed before a JSON-RPC response arrived — "
                "stdout carried non-MCP output or the server crashed")
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"non-MCP line on stdout (protocol violation): {line[:200]!r}"
            ) from exc
        if msg.get("id") == want_id:
            return msg


def test_stdio_handshake_initialize_and_tools_list(tmp_path):
    env = dict(os.environ)
    # Point INDEX_ROOT at a throwaway dir; the handshake never indexes, but a
    # clean root guarantees no accidental registry/backend traffic.
    env["INDEX_ROOT"] = str(tmp_path / "index-root")
    env.pop("VERBOSE", None)

    proc = subprocess.Popen(
        _server_command(str(tmp_path)),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(tmp_path),
    )
    try:
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0"},
            },
        })
        init = _read_response(proc, 1)
        assert "error" not in init, init
        result = init["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "code-indexer"

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_msg = _read_response(proc, 2)
        assert "error" not in tools_msg, tools_msg
        names = {t["name"] for t in tools_msg["result"]["tools"]}
        assert names == DOCUMENTED_TOOLS, (
            f"tool surface drifted: missing={DOCUMENTED_TOOLS - names} "
            f"extra={names - DOCUMENTED_TOOLS}")
    finally:
        if proc.stdin is not None:
            proc.stdin.close()  # EOF: let the server exit its read loop
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_text = proc.stderr.read() if proc.stderr is not None else ""
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is not None:
                stream.close()

    # Logs go to stderr only: the stderr stream must carry the server's own
    # log lines (any stderr content proves logging is not on stdout), and the
    # transport never broke on non-MCP stdout (the JSON parses above).
    assert stderr_text.strip(), "expected server log lines on stderr, got silence"


def test_server_command_resolution():
    """The resolver prefers the installed entry point, else -m fallback."""
    cmd = _server_command("/nonexistent")
    if shutil.which("code-indexer-mcp"):
        assert cmd == ["code-indexer-mcp"]
    else:
        assert cmd[-2:] == ["-m", "code_indexer.server"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))