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

A file the sensitive layer withholds is NOT a fourth verdict and NOT a stale
reason: the index pass drops it deliberately, so the diff re-applies the same
detector to the scanned paths it cannot find and reports them beside the
verdict as ``skipped`` (path + reason only, never the material).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .sensitive import (SKIP_REASON_CONTENT, SKIP_REASON_FILENAME,
                        content_is_sensitive, filename_is_sensitive,
                        skip_reason_text)

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

# The skip decision is re-derived on the read path, so its reads are bounded
# exactly like the pass's: the same 256 KiB prefix per file decides the same
# verdict, and the total byte budget keeps an all-new tree from turning a
# read command into an index pass (what is not examined stays an honest
# ``added`` reason, never a silent skip).
SENSITIVE_READ_CHARS = 65536 * 4
SENSITIVE_RECHECK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Reason:
    kind: str
    file: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "file": self.file}


@dataclass(frozen=True)
class SkippedFile:
    """A scanned path the sensitive layer withholds.

    ``file`` is the path and ``reason`` names the layer that fired
    ('sensitive filename' or 'sensitive content') — never the material.
    """

    file: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"file": self.file, "reason": self.reason}


@dataclass(frozen=True)
class StalenessVerdict:
    """One project's verdict. ``detail`` explains an ``unknown``."""

    state: str
    reasons: tuple[Reason, ...] = ()
    reason_count: int = 0
    skipped: tuple[SkippedFile, ...] = ()
    skipped_count: int = 0
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

    A scanned path with no manifest row is a sensitive skip when the same
detector the pass applied still withholds it; only the rest become
``added`` reasons. That keeps a deliberately withheld file out of the stale
banner without making a genuinely new file invisible.
    """
    tree = {f.path: f for f in scanned}
    reasons: list[Reason] = []
    skipped, added = _classify_skipped(
        [tree[path] for path in sorted(set(tree) - set(indexed))])
    for path in added:
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
                            skipped=tuple(skipped), skipped_count=len(skipped),
                            last_indexed=last_indexed,
                            index_revision=index_revision)


def _classify_skipped(candidates: list[Any]) -> tuple[list[SkippedFile],
                                                       list[str]]:
    """Split scanned-but-unindexed paths into sensitive skips and real adds.

    The pass drops a sensitive file without writing a row, so the diff
    re-applies the SAME detector to the paths it cannot explain: the filename
    layer is decided from the path alone (no read), the content layer reads
    the same prefix the pass reads, so the two agree on the file. The verdict
    carries the path and the layer that fired — never the material.

    The read path is bounded: ``SENSITIVE_RECHECK_BYTES`` caps the total
    examined, and a path past the budget stays an ``added`` reason. Reporting
    a file stale when the detector was not re-applied is conservative and
    honest; reporting it fresh would not be.
    """
    skipped: list[SkippedFile] = []
    added: list[str] = []
    budget = SENSITIVE_RECHECK_BYTES
    for f in candidates:
        if filename_is_sensitive(f.path):
            skipped.append(SkippedFile(f.path, skip_reason_text("filename")))
            continue
        if budget <= 0:
            added.append(f.path)
            continue
        try:
            with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(SENSITIVE_READ_CHARS)
        except OSError:
            added.append(f.path)
            continue
        budget -= f.size or len(text)
        if content_is_sensitive(text):
            skipped.append(SkippedFile(f.path, skip_reason_text("content")))
        else:
            added.append(f.path)
    return skipped, added


def _skipped_summary(skipped: Iterable[SkippedFile], count: int) -> str | None:
    """``1 file skipped (sensitive content)``, or None when nothing was held."""
    if not count:
        return None
    files = "file" if count == 1 else "files"
    parts = []
    for reason in (SKIP_REASON_CONTENT, SKIP_REASON_FILENAME):
        n = sum(1 for s in skipped if s.reason == reason)
        if n:
            # The numeral is dropped when it would only repeat the total, so
            # a single withholding reads '1 file skipped (sensitive content)'.
            parts.append(reason if count == 1 else f"{n} {reason}")
    return f"{count} {files} skipped ({', '.join(parts)})"


def unknown(detail: str, *, last_indexed: str | None = None,
            index_revision: str | None = None) -> StalenessVerdict:
    return StalenessVerdict(state=UNKNOWN, last_indexed=last_indexed,
                            index_revision=index_revision, detail=detail)


def block(verdict: StalenessVerdict) -> dict[str, Any]:
    """The additive JSON block, schema 1 (ADR-0003 section 3).

    ``skipped_*`` describes the files the sensitive layer withholds: a count,
    a path+reason list (capped like the reasons) and the same phrase the text
    line prints. It is present on a FRESH verdict, so "nothing changed" and
    "one file withheld on purpose" never read alike.
    """
    return {
        "stale": verdict.stale,
        "stale_known": verdict.known,
        "stale_reasons": [r.to_dict() for r in verdict.reasons[:REASON_CAP]],
        "stale_reason_count": verdict.reason_count,
        "skipped_count": verdict.skipped_count,
        "skipped_files": [s.to_dict() for s in verdict.skipped[:REASON_CAP]],
        "skipped_summary": _skipped_summary(verdict.skipped,
                                            verdict.skipped_count),
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
    merged_skipped = [s for v in verdicts.values() for s in v.skipped]
    skipped_total = sum(v.skipped_count for v in verdicts.values())
    known = all(v.known for v in verdicts.values())
    any_stale = any(v.stale for v in verdicts.values())
    return {
        "stale": any_stale,
        "stale_known": known,
        "stale_reasons": [r.to_dict() for r in merged[:REASON_CAP]],
        "stale_reason_count": total,
        "skipped_count": skipped_total,
        "skipped_files": [s.to_dict() for s in merged_skipped[:REASON_CAP]],
        "skipped_summary": _skipped_summary(merged_skipped, skipped_total),
        "index_revision": None,
        "last_indexed": None,
    }
