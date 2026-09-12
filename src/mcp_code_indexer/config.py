"""Configuration resolution for mcp-code-indexer.

Order (highest wins): CLI flags -> environment variables -> built-in defaults.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

_DEFAULTS: dict[str, str] = {
    "ollama_url": "http://192.168.1.103:11434",
    "qdrant_url": "http://192.168.1.105:6333",
    "embed_model": "qwen3-embedding:8b",
    "index_root": "~/.mcp-code-indexer",
    "stale_ttl": "60",          # seconds; staleness re-scan interval
    "embed_batch": "48",        # texts per Ollama embed request
    "upsert_batch": "256",      # points per Qdrant upsert
    "max_file_bytes": "1048576",  # skip files > 1MB
}


@dataclass
class Config:
    ollama_url: str
    qdrant_url: str
    embed_model: str
    index_root: str
    stale_ttl: int
    embed_batch: int
    upsert_batch: int
    max_file_bytes: int
    extra: dict[str, str] = field(default_factory=dict)


def load_config(argv: list[str] | None = None) -> Config:
    """Resolve configuration from env vars, overridden by CLI flags."""
    cfg = {key: os.environ.get(key.upper(), default) for key, default in _DEFAULTS.items()}

    parser = argparse.ArgumentParser(
        prog="mcp-code-indexer",
        description="MCP server: automatic semantic code index via Ollama + Qdrant",
    )
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL")
    parser.add_argument("--qdrant-url", default=None, help="Qdrant base URL")
    parser.add_argument("--embed-model", default=None, help="Embedding model name")
    parser.add_argument("--index-root", default=None, help="State directory (manifests, registry, locks)")
    args, _unknown = parser.parse_known_args(argv)

    for key, value in (
        ("ollama_url", args.ollama_url),
        ("qdrant_url", args.qdrant_url),
        ("embed_model", args.embed_model),
        ("index_root", args.index_root),
    ):
        if value is not None:
            cfg[key] = value

    index_root = os.path.abspath(os.path.expanduser(cfg["index_root"]))
    return Config(
        ollama_url=cfg["ollama_url"],
        qdrant_url=cfg["qdrant_url"],
        embed_model=cfg["embed_model"],
        index_root=index_root,
        stale_ttl=int(cfg["stale_ttl"]),
        embed_batch=int(cfg["embed_batch"]),
        upsert_batch=int(cfg["upsert_batch"]),
        max_file_bytes=int(cfg["max_file_bytes"]),
    )