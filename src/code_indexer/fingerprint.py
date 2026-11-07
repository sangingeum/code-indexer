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
    extraction_version  graph_extract.EXTRACTION_VERSION (imports/refs queries)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .embed_text import EMBED_FORMAT
from .ts_chunker import CHUNKER_VERSION
from .graph_extract import EXTRACTION_VERSION

logger = logging.getLogger("code-indexer.fingerprint")

FINGERPRINT_KEYS = ("embed_model", "embed_dim", "embed_text_version",
                    "chunker_version", "extraction_version")

# The three axes of ADR-0001 section 2, as recorded in meta:
#   extraction_version  -- parser, chunker, reference/import extraction
#   embedding_version   -- one composed string, compared as a whole
#   index_schema_version -- the manifest DDL only (written by manifest.open)
EMBEDDING_VERSION_KEY = "embedding_version"
ROW_SIDE_KEYS = ("chunker_version", "extraction_version")


def identity_digest(identity: str) -> str:
    """A short, stable digest of an embedding identity for collection names."""
    import hashlib
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]


def compose_embedding_version(embed_model: str, embed_dim: int,
                              embed_text_version: str) -> str:
    """model/dimension/format as ONE string (the embedding axis)."""
    return f"{embed_model}/{embed_dim}/{embed_text_version}"


# Version-ordered fingerprint keys. Only these carry an ordering, so only
# these can say "the manifest was recorded by NEWER code than this process".
# embed_model / embed_dim / embed_text_version differences are configuration
# changes, not a version ordering, and must still rebuild in either
# direction.
VERSION_KEYS = ("chunker_version", "extraction_version")


# Keys whose configured value is knowable from configuration alone. The
# embedding dimension is a property of the model and is only knowable by
# asking the service, so a read path never compares it (ADR-0001 section 1).
PROBE_FREE_KEYS = ("embed_model", "embed_text_version", "chunker_version",
                   "extraction_version")


def configured_values(embed_model: str) -> dict[str, str]:
    """The fingerprint keys this process can state without a service.

    These come from configuration and module constants alone: no client is
    constructed and no round trip is made, so a read command can compare them
    with the recorded values and report a verdict for free.
    """
    return {
        "embed_model": embed_model,
        "embed_text_version": EMBED_FORMAT,
        "chunker_version": str(CHUNKER_VERSION),
        "extraction_version": str(EXTRACTION_VERSION),
    }


def probe_free_mismatches(recorded: Fingerprint | None,
                          configured: dict[str, str]) -> list[str]:
    """Differing probe-free components (empty when none differ).

    ``embed_dim`` is deliberately absent from the comparison: there is no
    recorded-vs-configured dimension question a read path can answer without
    a probe, and guessing either side would manufacture a verdict.
    """
    if recorded is None:
        return []
    recorded_values = dict(recorded.as_items())
    return [f"{key}: {recorded_values[key]} -> {configured[key]}"
            for key in PROBE_FREE_KEYS
            if recorded_values[key] != configured[key]]


def row_side_mismatches(recorded: Fingerprint | None,
                        configured: dict[str, str]) -> list[str]:
    """Differences on the ROW axis only (chunker + extraction).

    A row pass is invalidated by the code that parsed and chunked the source,
    and by nothing else. An embedding-model or embedded-text change says
    nothing about these rows; treating it as one would force a reparse for a
    vector rebuild, which is exactly what the two independent sides exist to
    prevent (ADR-0001 sections 2 and 5).
    """
    if recorded is None:
        return []
    recorded_values = dict(recorded.as_items())
    return [f"{key}: {recorded_values[key]} -> {configured[key]}"
            for key in ROW_SIDE_KEYS
            if recorded_values[key] != configured[key]]


def vector_side_mismatches(recorded: Fingerprint | None,
                           embed_model: str,
                           embed_text_version: str) -> list[str]:
    """Differences on the VECTOR axis, as the model/format of the identity.

    The recorded identity carries its own dimension, and a read path cannot
    know the configured dimension without dialling the service; the comparison
    therefore states model and embedded-text format and takes the recorded
    dimension as given. A write path compares the whole string instead (it
    knows the dimension it would build with).
    """
    if recorded is None:
        return []
    identity = recorded.embedding_version()
    parts = identity.split("/")
    model = parts[0] if parts else ""
    fmt = parts[2] if len(parts) > 2 else ""
    diffs: list[str] = []
    if model != embed_model:
        diffs.append(f"embedding_version: {identity} -> "
                     f"{compose_embedding_version(embed_model, recorded.embed_dim, fmt)}")
    if fmt != embed_text_version:
        diffs.append(f"embedding_version: {identity} -> "
                     f"{compose_embedding_version(model, recorded.embed_dim, embed_text_version)}")
    return diffs


class ConfigError(Exception):
    """Raised when a project's index does not match the current configuration.

    The message carries the remediation command and deliberately does NOT
    bake in an error-type prefix: the CLI adapter routes the exception
    through the shared failure taxonomy (``errors.classify`` / ``fail``),
    which renders the one stdout/stderr line as ``<ErrorType>: <message>``.
    Prefixing the message here would double the type on that line.
    """


@dataclass
class Fingerprint:
    embed_model: str
    embed_dim: int
    embed_text_version: str
    chunker_version: int
    extraction_version: int = EXTRACTION_VERSION

    def as_items(self) -> list[tuple[str, str]]:
        return [
            ("embed_model", self.embed_model),
            ("embed_dim", str(self.embed_dim)),
            ("embed_text_version", self.embed_text_version),
            ("chunker_version", str(self.chunker_version)),
            ("extraction_version", str(self.extraction_version)),
            (EMBEDDING_VERSION_KEY,
             compose_embedding_version(self.embed_model, self.embed_dim,
                                       self.embed_text_version)),
        ]

    def describe(self) -> str:
        return (f"{self.embed_model}/{self.embed_dim}/"
                f"etext={self.embed_text_version}/"
                f"chunker={self.chunker_version}/"
                f"extr={self.extraction_version}")

    def embedding_version(self) -> str:
        """The composed embedding identity this fingerprint describes."""
        return compose_embedding_version(self.embed_model, self.embed_dim,
                                         self.embed_text_version)


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
            chunker_version=int(values["chunker_version"]),  # type: ignore[arg-type]
            # The extraction version MUST be read back: dropping it defaulted
            # to the CURRENT EXTRACTION_VERSION, so bumping the queries/refs
            # extraction could never flag an existing index as needing a
            # rebuild.
            extraction_version=int(values["extraction_version"]))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def write_fingerprint(manifest, fp: Fingerprint) -> None:
    for key, value in fp.as_items():
        manifest.set_meta(key, value)


def write_row_side(manifest) -> None:
    """Record only the axes a row pass can state without a service.

    The embedding identity includes a dimension only the service can answer,
    so a vectors-off pass never writes it. This is what keeps the two sides
    independent: a service-free pass makes the rows current and leaves the
    vector side's recorded identity untouched (ADR-0001 sections 2 and 5).
    """
    manifest.set_meta("chunker_version", str(CHUNKER_VERSION))
    manifest.set_meta("extraction_version", str(EXTRACTION_VERSION))


def write_embedding_side(manifest, embed_model: str, embed_dim: int) -> str:
    """Record the composed embedding identity and return it."""
    identity = compose_embedding_version(embed_model, embed_dim, EMBED_FORMAT)
    manifest.set_meta("embed_model", embed_model)
    manifest.set_meta("embed_dim", str(embed_dim))
    manifest.set_meta("embed_text_version", EMBED_FORMAT)
    manifest.set_meta(EMBEDDING_VERSION_KEY, identity)
    return identity


def fingerprint_mismatches(recorded: Fingerprint | None,
                           current: Fingerprint) -> list[str]:
    """Human-readable list of differing RECORDED components.

    Compares the five stored components one by one. The composed
    ``embedding_version`` is derived from three of them, so it is not
    repeated here; per-side verdicts are ``row_side_mismatches`` and
    ``vector_side_mismatches``.
    """
    if recorded is None:
        return []
    recorded_values = dict(recorded.as_items())
    current_values = dict(current.as_items())
    return [f"{key}: {recorded_values[key]} -> {current_values[key]}"
            for key in FINGERPRINT_KEYS
            if recorded_values[key] != current_values[key]]


def manifest_newer_than(recorded: Fingerprint | None,
                        current: Fingerprint) -> bool:
    """True when ``recorded`` was built by code newer than this process.

    Compares the version-ordered keys only (``VERSION_KEYS``): a recorded
    value GREATER than this process's own means the manifest was written by a
    newer build, so this (older) process must not re-embed with its older
    extraction code nor stamp its own older version over the newer one. A
    recorded value LOWER (or equal) is not "newer" — an index built by older
    code is the legitimate upgrade path and rebuilds normally.
    """
    if recorded is None:
        return False
    return any(getattr(recorded, key) > getattr(current, key)
               for key in VERSION_KEYS)


def current_version_stamp() -> Fingerprint:
    """A fingerprint carrying only the version-ordered keys.

    The manifest-only surfaces (find-references, find-callers, find-callees,
    deps, skeleton, outline) compare the STORED ROWS they read against the
    extraction code that this build would use, and that comparison is
    described by ``VERSION_KEYS`` alone — both of which are module constants.
    Building this stamp therefore answers the question without an embedder
    dimension probe (i.e. without an Ollama round trip on a read-only
    command). The three non-version keys are placeholders that no
    version-key comparison reads.
    """
    return Fingerprint(embed_model="", embed_dim=0, embed_text_version="",
                       chunker_version=CHUNKER_VERSION)


def meaning(state: str | None, reason: str | None) -> str | None:
    """What a fingerprint verdict means, in words, for the reader of a read.

    ``fingerprint=unverified reason=embedding-dimension-not-compared`` is a
    pair of flags; a reader cannot tell from the pair what was NOT compared or
    what to conclude. The states keep their names (a caller branches on
    ``fingerprint``/``reason``); this sentence is the human half, and None
    when there is no verdict to explain (an ``ok`` project prints no pair).
    """
    if state == "unverified":
        return ("every fingerprint key a read can compare matched (embedding "
                "model, embedded-text format, chunker and extraction "
                "version); the embedding dimension was not compared, because "
                "learning the configured dimension needs the service, so "
                "vectors of another dimension recorded under the same model "
                "name would not be flagged here")
    if state == "needs-reindex":
        return (f"the recorded fingerprint disagrees with this build "
                f"({reason}); the index was built by other extraction or "
                f"chunking code, so the row reads refuse until a reindex runs")
    return None


def mismatch_error(project_path: str, recorded: Fingerprint,
                   current: Fingerprint) -> ConfigError:
    return ConfigError(
        f"index for {project_path} was built with "
        f"{recorded.describe()}; current is {current.describe()}; "
        f"run: code-indexer reindex-project {project_path}")