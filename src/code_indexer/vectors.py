"""Vectors as an option: mode resolution and the recorded vector state.

The manifest is authoritative for rows; a vector collection is an optional
extension of it (ADR-0001 sections 3 and 4). Three recorded facts answer
every question a read path asks, without dialling a service:

    meta.vectors         on | off -- the mode the project is kept in
    meta.vectors_state   current | absent | stale | error -- the last known
                         state of the collection relative to the rows
    meta.embedding_version  one composed identity: model/dimension/format

A read never contacts Qdrant, so ``vectors_state`` is the state the LAST
write recorded; ``absent`` on a project that was never vectored, ``current``
after a successful embedding pass, ``error`` when that pass lost chunks.
"""

from __future__ import annotations

import logging

from .embed_text import EMBED_FORMAT
from .graph_extract import EXTRACTION_VERSION

logger = logging.getLogger("code-indexer.vectors")

MODE_ON = "on"
MODE_OFF = "off"
MODE_AUTO = "auto"
MODES = (MODE_AUTO, MODE_ON, MODE_OFF)

STATE_CURRENT = "current"
STATE_ABSENT = "absent"
STATE_STALE = "stale"
STATE_ERROR = "error"
STATES = (STATE_CURRENT, STATE_ABSENT, STATE_STALE, STATE_ERROR)


def embedding_identity(embed_model: str, dimension: int,
                       embed_format: str = EMBED_FORMAT) -> str:
    """The ONE composed embedding identity (ADR-0001 section 2).

    One string, compared as a whole, because the three components are only
    meaningful together: the same model at a different dimension, or the same
    model and dimension with a different embedded-text construction, is a
different collection. Stamped into the collection name and recorded in
``meta.embedding_version``.
    """
    return f"{embed_model}/{dimension}/{embed_format}"


def recorded_mode(manifest) -> str | None:
    raw = manifest.get_meta("vectors")
    return raw if raw in (MODE_ON, MODE_OFF) else None


def resolve_mode(manifest, requested: str) -> str:
    """Resolve ``auto`` against the project's recorded mode.

    ``auto`` on a project that has never recorded a mode means ON: that is
    the pre-extension behaviour, preserved for existing projects across the
    upgrade. A NEW project records ``off`` when it is registered, so auto
    never silently enables embedding on a fresh install (ADR-0001 section 4).
    """
    if requested not in MODES:
        raise ValueError(f"unknown vectors mode: {requested}")
    if requested != MODE_AUTO:
        return requested
    return recorded_mode(manifest) or MODE_ON


def rows_current(manifest) -> bool:
    """True when the stored rows were produced by this build's extraction.

    The extraction version is the axis that covers parser, chunker and
    reference/import extraction, and the chunks table is the evidence that
    the rows include their chunk content. A manifest upgraded from an older
    extraction reports absent until one pass rewrites it.
    """
    if manifest.get_meta("last_indexed") is None:
        return False
    recorded = manifest.get_meta("extraction_version")
    try:
        if recorded is None or int(recorded) != EXTRACTION_VERSION:
            return False
    except ValueError:
        return False
    return manifest.count_chunks() > 0


def vectors_state(manifest) -> str:
    """The recorded vector state, inferred honestly for a legacy manifest.

    Pre-extension manifests have no marker, but every pass that wrote their
    fingerprint embedded as it went, so ``current`` is the truthful reading
    of ``mode on`` with no marker. No read path can improve on that without
    dialling Qdrant, and a read never does.
    """
    recorded = manifest.get_meta("vectors_state")
    if recorded in STATES:
        return recorded
    if (recorded_mode(manifest) or MODE_ON) == MODE_OFF:
        return STATE_ABSENT
    return STATE_CURRENT if manifest.get_meta("embed_model") else STATE_ABSENT


def record_mode(manifest, mode: str) -> None:
    manifest.set_meta("vectors", mode)
    if manifest.get_meta("vectors_state") in (None, ""):
        manifest.set_meta("vectors_state",
                          STATE_ABSENT if mode == MODE_OFF else STATE_ABSENT)


def record_rows(manifest) -> None:
    """A row pass just committed: the row side is current for this build."""
    manifest.set_meta("rows", "current")


def rows_state(manifest) -> str:
    """``current`` when the rows were written by this extraction version."""
    return "current" if rows_current(manifest) else "absent"


def collection_for(slug: str, identity: str) -> str:
    """A physical collection name that carries its embedding identity.

    Vectors are never mixed across models: the identity's digest is part of
    the name, so a changed identity can only ever be a NEW collection. The
    slug itself stays the alias every read and write uses.
    """
    from .fingerprint import identity_digest
    return f"idx_{slug}__{identity_digest(identity)}"
