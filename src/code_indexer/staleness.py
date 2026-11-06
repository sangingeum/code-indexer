"""Staleness detection and reporting: detect, never repair.

A read answer describes the index as of ``meta.last_indexed``, never the
working tree. Detection compares the indexed path set and each file's stat
identity (``size``, ``mtime_ns``, ``inode``) against the tree; content is
hashed only for files whose stat changed (the scanner's existing stat
fast-path). No service is contacted.

Three verdicts, never a boolean guess:

- ``fresh`` -- nothing differs;
- ``stale`` -- at least one reason (modified, added, deleted file);
- ``unknown`` -- the check could not run (project path absent, no completed
  pass, or the scan exceeded its own budget). ``unknown`` is never ``fresh``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"

# At most five reasons ride on an answer; the count before the cap is always
# reported beside them, so a capped list never reads as the whole picture.
REASON_CAP = 5

# The staleness walk carries its own budget: a very large tree degrades to
# ``unknown`` instead of a long silence before the answer.
SCAN_BUDGET_SECONDS = 5.0
SCAN_BUDGET_FILES = 200_000

# Order in which reasons are counted and listed.
_KIND_ORDER = ("modified", "added", "deleted")


@dataclass(frozen=True)
class Reason:
    kind: str
    file: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "file": self.file}


@dataclass(frozen=True)
class StalenessVerdict:
    """One project's verdict. ``detail`` explains an ``unknown``."""

    state: str
    reasons: tuple[Reason, ...] = ()
    reason_count: int = 0
    last_indexed: str | None = None
    index_revision: str | None = None
    detail: str | None = None

    @property
    def stale(self) -> bool:
        return self.state == STALE

    @property
    def known(self) -> bool:
        return self.state in (FRESH, STALE)


def iso_timestamp(epoch: str | None) -> str | None:
    """Manifest epoch seconds -> ``2026-11-06T22:10:00Z``; None stays None."""
    if not epoch:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))
    except (TypeError, ValueError):
        return None


def detect(indexed: Mapping[str, Any], scanned: Iterable[Any], *,
           last_indexed: str | None,
           index_revision: str | None) -> StalenessVerdict:
    """Compare the indexed files with a scan of the tree.

    ``indexed`` maps a project-relative path to its manifest row (``size``,
    ``mtime_ns``, ``inode``, ``content_hash`` are what is read); ``scanned``
    is the scanner's result for the same tree and the same include/exclude
    rules. A file whose stat changed but whose content hash still matches is
    NOT reported: nothing a reader can observe changed.
    """
    tree = {f.path: f for f in scanned}
    reasons: list[Reason] = []
    for path in sorted(set(tree) - set(indexed)):
        reasons.append(Reason("added", path))
    for path in sorted(set(indexed) - set(tree)):
        reasons.append(Reason("deleted", path))
    for path in sorted(set(indexed) & set(tree)):
        if tree[path].content_hash != indexed[path].content_hash:
            reasons.append(Reason("modified", path))
    reasons.sort(key=lambda r: (_KIND_ORDER.index(r.kind), r.file))
    state = STALE if reasons else FRESH
    return StalenessVerdict(state=state, reasons=tuple(reasons),
                            reason_count=len(reasons),
                            last_indexed=last_indexed,
                            index_revision=index_revision)


def unknown(detail: str, *, last_indexed: str | None = None,
            index_revision: str | None = None) -> StalenessVerdict:
    return StalenessVerdict(state=UNKNOWN, last_indexed=last_indexed,
                            index_revision=index_revision, detail=detail)


def block(verdict: StalenessVerdict) -> dict[str, Any]:
    """The additive JSON block, schema 1 (ADR-0003 section 3)."""
    return {
        "stale": verdict.stale,
        "stale_known": verdict.known,
        "stale_reasons": [r.to_dict() for r in verdict.reasons[:REASON_CAP]],
        "stale_reason_count": verdict.reason_count,
        "index_revision": verdict.index_revision,
        "last_indexed": verdict.last_indexed,
    }


def _counts(reasons: tuple[Reason, ...]) -> str:
    parts = []
    for kind in _KIND_ORDER:
        n = sum(1 for r in reasons if r.kind == kind)
        if n:
            parts.append(f"{n} {kind}")
    return ", ".join(parts)


def warning_line(verdict: StalenessVerdict, project_path: str) -> str | None:
    """Exactly one stderr line, or None for a fresh answer.

    Stale shape (the record's own wording):
      Warning: index is stale (3 files changed since 2026-11-06T22:10:00Z:
      2 modified, 1 deleted); answers describe the index, not the tree.
      Refresh: code-indexer reindex-project <path>
    """
    if verdict.state == FRESH:
        return None
    if verdict.state == UNKNOWN:
        return ("Warning: index staleness check did not run "
                f"({verdict.detail}); answers describe the index, not the tree.")
    files = "file" if verdict.reason_count == 1 else "files"
    return (f"Warning: index is stale ({verdict.reason_count} {files} changed "
            f"since {verdict.last_indexed or 'an unknown pass'}: "
            f"{_counts(verdict.reasons)}); answers describe the index, not the "
            f"tree. Refresh: code-indexer reindex-project {project_path}")


def aggregate(verdicts: Mapping[str, StalenessVerdict]) -> dict[str, Any]:
    """One block for an answer that spans several projects.

    ``stale`` is true when any project is stale, ``stale_known`` is true only
    when every project's verdict was reachable, and the reasons are merged
    (still capped) so a fan-out cannot hide a stale project. The per-project
    blocks ride beside it, so nothing is lost to the merge.
    """
    if not verdicts:
        return block(unknown("no project answered"))
    merged: list[Reason] = []
    for verdict in verdicts.values():
        merged.extend(verdict.reasons)
    total = sum(v.reason_count for v in verdicts.values())
    known = all(v.known for v in verdicts.values())
    any_stale = any(v.stale for v in verdicts.values())
    return {
        "stale": any_stale,
        "stale_known": known,
        "stale_reasons": [r.to_dict() for r in merged[:REASON_CAP]],
        "stale_reason_count": total,
        "index_revision": None,
        "last_indexed": None,
    }
