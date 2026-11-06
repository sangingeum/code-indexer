"""The MCP surface's usage record (ADR-0007 section 3).

One line per invocation, appended under the index root by the MCP process
only. Nothing else is recorded and no argument is captured: a query string
may be source code, so the record must never carry one. A failed write must
never fail the call — this file is evidence, not a gate.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("code-indexer.usage")

USAGE_FILE_NAME = "mcp-usage.jsonl"


def usage_path(index_root: str) -> str:
    return os.path.join(index_root, USAGE_FILE_NAME)


def record_invocation(*, tool: str, outcome: str, exit_code: int,
                      response_bytes: int, duration_ms: int,
                      index_root: str) -> bool:
    """Append one line; return False when the write failed (never raise)."""
    line = json.dumps({
        "tool": tool,
        "outcome": outcome,
        "exit_code": int(exit_code),
        "response_bytes": int(response_bytes),
        "duration_ms": int(duration_ms),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, separators=(",", ":"))
    try:
        os.makedirs(index_root, exist_ok=True)
        with open(usage_path(index_root), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except OSError as exc:  # read-only root, full disk, bad permission
        logger.debug("usage record write failed: %s", exc)
        return False


def read_invocations(index_root: str) -> list[dict[str, Any]]:
    """Every recorded line (used by tests and by the future re-decision)."""
    path = usage_path(index_root)
    if not os.path.isfile(path):
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
    return out
