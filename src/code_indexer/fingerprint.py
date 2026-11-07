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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .embed_text import EMBED_FORMAT
from .ts_chunker import CHUNKER_VERSION
from .graph_extract import EXTRACTION_VERSION
from .vectors import MODE_OFF, STATE_STALE

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
    """Differences on the VECTOR axis, one line per key that did not match.

    The recorded identity carries its own dimension, and a read path cannot
    know the configured dimension without dialling the service; the comparison
    therefore states model and embedded-text format and takes the recorded
    dimension as given. A write path compares the whole string instead (it
    knows the dimension it would build with).

    Each line names the key it is about, in the same spelling the row-side
    differences use, because a reader has to know WHICH key did not match: a
    model change and an embedded-text change need the same rebuild, but the
    sentence reporting them says which one it was.
    """
    if recorded is None:
        return []
    parts = recorded.embedding_version().split("/")
    model = parts[0] if parts else ""
    fmt = parts[2] if len(parts) > 2 else ""
    diffs: list[str] = []
    if model != embed_model:
        diffs.append(f"embed_model: {model} -> {embed_model}")
    if fmt != embed_text_version:
        diffs.append(f"embed_text_version: {fmt} -> {embed_text_version}")
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


def _inert_vector_facts(vectors: Mapping[str, Any]) -> str:
    """What the recorded vector facts mean while NO vectors are stored.

    With the mode off there is no collection to be stale and no vector query
    to refuse, so a recorded identity difference is an inert fact. It is
    still worth stating: it decides what a later vectors-on pass writes. No
    rebuild is prescribed, because there is nothing to rebuild.
    """
    parts: list[str] = []
    if vectors.get("vectors_state") == STATE_STALE:
        parts.append("the manifest still records the collection as stale from "
                     "an earlier build, which is inert while vectors are off")
    reason = vectors.get("identity_reason")
    if reason:
        parts.append(f"the recorded embedding identity differs from this "
                     f"build's ({reason}), which changes nothing until a "
                     f"vectors-on pass, and that pass writes a new collection "
                     f"under its own identity rather than reusing the old one")
    return "; ".join(parts)


# The branches of `meaning`, named by the FIELD VALUES that reach them.
# `meaning` dispatches on `meaning_branch`, so this tuple is the sentence's
# own branch list: a branch with no id here cannot be reached, and the doc's
# table is pinned to this tuple by name (docs/json-schema.md and the doc
# test), so a new branch cannot ship with no row.
ROW_KEYS_DIFFER = "row-keys-differ"
ON_IDENTITY_MATCHED = "vectors-on-identity-matched"
ON_IDENTITY_DIFFERS = "vectors-on-identity-differs"
ON_MARKER_STALE = "vectors-on-marker-stale"
OFF_IDENTITY_DIFFERS = "vectors-off-identity-differs"
OFF_IDENTITY_MATCHED = "vectors-off-identity-matched"
NO_VERDICT_RECORDED = "no-verdict-recorded"
MEANING_BRANCHES = (ROW_KEYS_DIFFER, ON_IDENTITY_MATCHED, ON_IDENTITY_DIFFERS,
                    ON_MARKER_STALE, OFF_IDENTITY_DIFFERS,
                    OFF_IDENTITY_MATCHED, NO_VERDICT_RECORDED)


def meaning_branch(state: str | None, *, vectors: Mapping[str, Any]) -> str:
    """Which branch of :func:`meaning` these field values select.

    The key is mode + marker + identity, in the order the sentence branches
    on them -- never ``vectors_state`` alone, which is the marker the
    manifest RECORDS, not a second comparison. A row key differing
    (``needs-reindex``) outranks every vector-side branch, and no recorded
    fingerprint is its own branch: the comparison did not run.
    """
    if state == "needs-reindex":
        return ROW_KEYS_DIFFER
    if state != "unverified":
        return NO_VERDICT_RECORDED
    if vectors.get("vectors") == MODE_OFF:
        return (OFF_IDENTITY_DIFFERS if vectors.get("identity_reason")
                else OFF_IDENTITY_MATCHED)
    if vectors.get("identity_reason"):
        return ON_IDENTITY_DIFFERS
    if vectors.get("vectors_state") == STATE_STALE:
        return ON_MARKER_STALE
    return ON_IDENTITY_MATCHED


def meaning(state: str | None, reason: str | None, *,
            vectors: Mapping[str, Any]) -> str:
    """What a fingerprint verdict means, in words, for the reader of a read.

    ``fingerprint=unverified reason=embedding-dimension-not-compared`` is a
    pair of flags; a reader cannot tell from the pair what was NOT compared or
    what to conclude. The states keep their names (a caller branches on
    ``fingerprint``/``reason``); this sentence is the human half.

    ``vectors`` is the SAME verdict object the line prints -- the
    ``core.vectors_status`` answer, carrying the mode, the marker-aware state
    and the identity comparison. The sentence reads no other source, so it
    cannot contradict the ``vectors=``/``vectors_state=`` fields beside it:
    the identity comparison is one CLUE to the vector verdict, never the
    verdict itself.

    * row keys matched, vectors on, identity matches: say so, and keep the
      dimension gap (the one key a read cannot compare without a probe);
    * row keys matched, vectors on, identity differs: ``identity_reason``
      names the key, and the reader is told a vector rebuild clears it;
    * row keys matched, vectors on, the manifest records the collection
      stale: say that the marker is what is stale, since no key differs;
    * row keys matched, vectors OFF: there is no vector side (none stored, no
      vector query runs); an identity difference is stated as an inert fact
      about the next vectors-on pass, and no vector rebuild is prescribed;
    * a row key differs (needs-reindex): ``reason`` names it, and the row
      reads refuse until a reindex;
    * no verdict was reached (no recorded fingerprint): the comparison did
      not run, which is not the same as a pass.
    """
    vectors_off = vectors.get("vectors") == MODE_OFF
    identity_reason = vectors.get("identity_reason")
    marker_stale = vectors.get("vectors_state") == STATE_STALE
    inert = _inert_vector_facts(vectors)
    branch = meaning_branch(state, vectors=vectors)
    if branch == ROW_KEYS_DIFFER:
        sentence = (f"the recorded fingerprint disagrees with this build "
                    f"({reason}); the index was built by other extraction or "
                    f"chunking code, so the row reads refuse until a reindex "
                    f"runs")
        if vectors_off:
            sentence += ("; this project keeps vectors off, so no vectors are "
                         "stored and there is no vector side to rebuild — the "
                         "reindex that clears this is a row pass")
            if inert:
                sentence += "; " + inert
        elif identity_reason:
            sentence += f"; its vectors are stale too ({identity_reason})"
        elif marker_stale:
            sentence += ("; the manifest also records the vector collection as "
                         "stale, and this reindex rebuilds it")
        return sentence + " -- run: code-indexer reindex-project"
    if branch == OFF_IDENTITY_DIFFERS:
        sentence = (f"the row keys matched (chunker and extraction "
                    f"version), but the recorded embedding identity "
                    f"differs from this build's ({identity_reason}); "
                    f"this project keeps vectors off, so no vectors "
                    f"are stored and no vector query runs — there is "
                    f"no vector side to be stale, and the difference "
                    f"is inert until a vectors-on pass, which writes "
                    f"a new collection under its own identity rather "
                    f"than reusing the old one")
        if marker_stale:
            sentence += ("; the manifest still records the collection "
                         "as stale from an earlier build, which is "
                         "inert while vectors are off")
        return sentence
    if branch == OFF_IDENTITY_MATCHED:
        head = ("every fingerprint key a read can compare matched "
                "(embedding model, embedded-text format, chunker and "
                "extraction version), and the recorded embedding model "
                "and embedded-text format are the ones this build would "
                "write; this project keeps vectors off, so no vectors are "
                "stored and no vector query runs — there is no vector "
                "side here")
        if marker_stale:
            head += "; " + _inert_vector_facts(vectors)
        return head + ("; the embedding dimension is the one key a read "
                       "cannot compare, because learning the configured "
                       "dimension needs the service")
    if branch == NO_VERDICT_RECORDED:
        sentence = ("the fingerprint comparison did not run: this index records no "
                    "fingerprint, or an incomplete one, so this build has no "
                    "verdict on whether the recorded configuration matches; the "
                    "next index pass records one")
        if vectors_off:
            sentence += ("; this project keeps vectors off, so there is no vector "
                         "side here either")
        return sentence
    matched_head = (
        "every fingerprint key a read can compare matched (embedding model, "
        "embedded-text format, chunker and extraction version), and the "
        "recorded embedding model and embedded-text format are the ones "
        "this build would write")
    dimension = (
        "; the embedding dimension is the one key left uncompared, "
        "because learning the configured dimension needs the service, "
        "so vectors of another dimension recorded under the same model "
        "name would not be flagged here")
    if branch == ON_IDENTITY_DIFFERS:
        return (f"a fingerprint key a read can compare did not match "
                f"({identity_reason}), so the vector side is stale and its "
                f"vectors are not comparable with this build; the row "
                f"keys matched, so a vector rebuild is what clears this "
                f"-- run: code-indexer reindex-project")
    if branch == ON_MARKER_STALE:
        return (matched_head + "; separately, the manifest records the "
                "vector collection itself as stale, and a vector rebuild "
                "is what clears that -- run: code-indexer reindex-project")
    # ON_IDENTITY_MATCHED is the last branch in MEANING_BRANCHES.
    return matched_head + dimension


def mismatch_error(project_path: str, recorded: Fingerprint,
                   current: Fingerprint) -> ConfigError:
    return ConfigError(
        f"index for {project_path} was built with "
        f"{recorded.describe()}; current is {current.describe()}; "
        f"run: code-indexer reindex-project {project_path}")