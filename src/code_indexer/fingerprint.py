"""Index fingerprint: the embedding/chunking configuration a manifest was
built with (improvement-plan work item: index fingerprint and mismatch
protection).

Switching EMBED_MODEL (even to another model with the same dimension),
changing the embedded-text construction, or changing the chunker silently
mixes incompatible vectors. The fingerprint records all of them in the
manifest's meta table; opening a project compares the recorded fingerprint
against the current configuration and, on mismatch, blocks queries with a
ConfigError instead of silently searching a stale index.

Fingerprint keys (manifest meta):
    embed_model         e.g. qwen3-embedding:8b
    embed_dim           vector dimension (probed from the embedder)
    embed_text_version  EMBED_FORMAT string (e.g. contextual-header-v1)
    chunker_version     CHUNKER_VERSION (bumped when chunking behavior changes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .embed_text import EMBED_FORMAT
from .ts_chunker import CHUNKER_VERSION

logger = logging.getLogger("code-indexer.fingerprint")

FINGERPRINT_KEYS = ("embed_model", "embed_dim", "embed_text_version",
                    "chunker_version")


class ConfigError(Exception):
    """Raised when a project's index does not match the current configuration.

    The CLI/MCP adapters render ``str(exc)`` as the error line; the message
    carries the remediation command.
    """


@dataclass
class Fingerprint:
    embed_model: str
    embed_dim: int
    embed_text_version: str
    chunker_version: int

    def as_items(self) -> list[tuple[str, str]]:
        return [
            ("embed_model", self.embed_model),
            ("embed_dim", str(self.embed_dim)),
            ("embed_text_version", self.embed_text_version),
            ("chunker_version", str(self.chunker_version)),
        ]

    def describe(self) -> str:
        return (f"{self.embed_model}/{self.embed_dim}/"
                f"etext={self.embed_text_version}/"
                f"chunker={self.chunker_version}")


def current_fingerprint(embed_model: str, embed_dim: int) -> Fingerprint:
    """The fingerprint of the configuration this process would build with."""
    return Fingerprint(embed_model=embed_model, embed_dim=embed_dim,
                       embed_text_version=EMBED_FORMAT,
                       chunker_version=CHUNKER_VERSION)


def read_fingerprint(manifest) -> Fingerprint | None:
    """The recorded fingerprint, or None when absent/incomplete/blank
    (legacy manifest, or meta rows pre-created with empty values)."""
    values: dict[str, str | None] = {}
    for key in FINGERPRINT_KEYS:
        raw = manifest.get_meta(key)
        values[key] = raw if raw not in (None, "") else None
    if any(values[key] is None for key in FINGERPRINT_KEYS):
        return None
    try:
        return Fingerprint(
            embed_model=str(values["embed_model"]),
            embed_dim=int(values["embed_dim"]),  # type: ignore[arg-type]
            embed_text_version=str(values["embed_text_version"]),
            chunker_version=int(values["chunker_version"]))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def write_fingerprint(manifest, fp: Fingerprint) -> None:
    for key, value in fp.as_items():
        manifest.set_meta(key, value)


def fingerprint_mismatches(recorded: Fingerprint | None,
                           current: Fingerprint) -> list[str]:
    """Human-readable list of differing components (empty when compatible)."""
    if recorded is None:
        return []
    diffs: list[str] = []
    for rk, ck in zip(recorded.as_items(), current.as_items()):
        if rk[1] != ck[1]:
            diffs.append(f"{rk[0]}: {rk[1]} -> {ck[1]}")
    return diffs


def mismatch_error(project_path: str, recorded: Fingerprint,
                   current: Fingerprint) -> ConfigError:
    return ConfigError(
        f"ConfigError: index for {project_path} was built with "
        f"{recorded.describe()}; current is {current.describe()}; "
        f"run: code-indexer reindex-project {project_path}")