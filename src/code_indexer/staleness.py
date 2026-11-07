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
verdict as ``withheld`` (path + reason only, never the material).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .sensitive import (SENSITIVE_READ_CHARS, SKIP_REASON_CONTENT,
                        SKIP_REASON_FILENAME, skip_reason_text, skip_verdict)

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
# exactly like the pass's: the same ``SENSITIVE_READ_CHARS`` prefix per file
# decides the same verdict, and the total budget keeps an all-new tree from
# turning a read command into an index pass. The budget is charged in bytes
# actually read -- a huge file costs its prefix, not its size. What is not
# examined stays an honest ``added`` reason (never a silent skip) and is
# counted, so a truncated check says so instead of hiding behind the reason.
SENSITIVE_RECHECK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Reason:
    kind: str
    file: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "file": self.file}


@dataclass(frozen=True)
class WithheldFile:
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
    """One project's verdict. ``detail`` explains an ``unknown``.

    ``withheld`` names the scanned paths the sensitive layer withholds (no
    row, and not a stale reason); ``unexamined_count`` names the rowless paths
    the re-check's budget never reached, which stay ordinary ``added``
    reasons.
    """

    state: str
    reasons: tuple[Reason, ...] = ()
    reason_count: int = 0
    withheld: tuple[WithheldFile, ...] = ()
    withheld_count: int = 0
    unexamined_count: int = 0
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
           index_revision: str | None,
           allow_sensitive: bool = False) -> StalenessVerdict:
    """Compare the indexed files with a scan of the tree.

    ``indexed`` maps a project-relative path to its manifest row (``size``,
    ``mtime_ns``, ``inode``, ``content_hash`` are what is read); ``scanned``
    is the scanner's result for the same tree and the same include/exclude
    rules. A file whose stat changed but whose content hash still matches is
    NOT reported: nothing a reader can observe changed.

    A scanned path with no manifest row is a withheld file when the same
    detector the pass applied still withholds it; only the rest become
    ``added`` reasons. That keeps a deliberately withheld file out of the stale
    banner without making a genuinely new file invisible.

    ``allow_sensitive`` mirrors the project's override: when the pass will
    index a sensitive file, the diff withholds nothing and every rowless
    scanned path is an ``added`` reason -- the state the next pass writes.
    """
    tree = {f.path: f for f in scanned}
    reasons: list[Reason] = []
    withheld, added, unexamined = _classify_withheld(
        [tree[path] for path in sorted(set(tree) - set(indexed))],
        allow_sensitive=allow_sensitive)
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
                            withheld=tuple(withheld),
                            withheld_count=len(withheld),
                            unexamined_count=unexamined,
                            last_indexed=last_indexed,
                            index_revision=index_revision)


def _classify_withheld(candidates: list[Any], *,
                       allow_sensitive: bool = False,
                       ) -> tuple[list[WithheldFile], list[str], int]:
    """Split scanned-but-unindexed paths into withheld files and real adds.

    The pass drops a sensitive file without writing a row, so the diff
    re-applies the SAME decision function to the paths it cannot explain --
    ``sensitive.skip_verdict``, composed once for both sides. The verdict
    carries the path and the layer that fired — never the material.

    With ``allow_sensitive`` set the pass withholds nothing, so this classifies
    nothing: every candidate is an ``added`` reason, which is exactly what the
    next pass will write.

    The read path is bounded: ``SENSITIVE_RECHECK_BYTES`` caps the bytes
    actually read, and each candidate is charged the prefix it was read for,
    never its size on disk. A path past the budget stays an ``added`` reason
    and is counted in the returned unexamined total, so a truncated check
    announces itself. Reporting such a file stale when the detector was not
    re-applied is conservative and honest; reporting it fresh would not be.
    """
    if allow_sensitive:
        return [], [f.path for f in candidates], 0
    withheld: list[WithheldFile] = []
    added: list[str] = []
    unexamined = 0
    budget = SENSITIVE_RECHECK_BYTES
    for f in candidates:
        verdict = skip_verdict(f.path)
        if verdict is not None:
            withheld.append(WithheldFile(f.path, skip_reason_text(verdict)))
            continue
        if budget <= 0:
            added.append(f.path)
            unexamined += 1
            continue
        try:
            with open(f.abs_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read(SENSITIVE_READ_CHARS)
        except OSError:
            added.append(f.path)
            continue
        budget -= len(text.encode("utf-8", "replace"))
        verdict = skip_verdict(f.path, text)
        if verdict is not None:
            withheld.append(WithheldFile(f.path, skip_reason_text(verdict)))
        else:
            added.append(f.path)
    return withheld, added, unexamined


def _withheld_summary(withheld: Iterable[WithheldFile],
                      count: int) -> str | None:
    """``2 files skipped (sensitive content)``, or None when nothing was held.

    The per-reason numeral appears only when more than one reason category is
    present: with a single category it would only repeat the total.
    """
    if not count:
        return None
    files = "file" if count == 1 else "files"
    tallies = [(reason, sum(1 for s in withheld if s.reason == reason))
               for reason in (SKIP_REASON_CONTENT, SKIP_REASON_FILENAME)]
    tallies = [(reason, n) for reason, n in tallies if n]
    if len(tallies) == 1:
        parts = [tallies[0][0]]
    else:
        parts = [f"{n} {reason}" for reason, n in tallies]
    return f"{count} {files} skipped ({', '.join(parts)})"


def unknown(detail: str, *, last_indexed: str | None = None,
            index_revision: str | None = None) -> StalenessVerdict:
    return StalenessVerdict(state=UNKNOWN, last_indexed=last_indexed,
                            index_revision=index_revision, detail=detail)


def block(verdict: StalenessVerdict) -> dict[str, Any]:
    """The additive JSON block, schema 1 (ADR-0003 section 3).

    ``withheld_*`` describes the files the sensitive layer withholds: a count,
    a path+reason list (capped like the reasons) and the same phrase the text
    line prints. It is present on a FRESH verdict, so "nothing changed" and
    "one file withheld on purpose" never read alike. ``withheld_unexamined_count``
    is the part of the re-check's budget that ran out: those paths are ordinary
    ``added`` reasons and nothing is known about their content.
    """
    return {
        "stale": verdict.stale,
        "stale_known": verdict.known,
        "stale_reasons": [r.to_dict() for r in verdict.reasons[:REASON_CAP]],
        "stale_reason_count": verdict.reason_count,
        "withheld_count": verdict.withheld_count,
        "withheld_files": [s.to_dict() for s in verdict.withheld[:REASON_CAP]],
        "withheld_summary": _withheld_summary(verdict.withheld,
                                             verdict.withheld_count),
        "withheld_unexamined_count": verdict.unexamined_count,
        "index_revision": verdict.index_revision,
        "last_indexed": verdict.last_indexed,
    }


def _count_phrase(reasons: tuple[Reason, ...]) -> str:
    """``2 modified files, 1 added file`` -- a count per kind, in fixed order."""
    parts = []
    for kind in _KIND_ORDER:
        n = sum(1 for r in reasons if r.kind == kind)
        if n:
            parts.append(f"{n} {kind} file" + ("" if n == 1 else "s"))
    return ", ".join(parts)


def warning_line(verdict: StalenessVerdict, project_path: str, *,
                 include_withheld: bool = True) -> str | None:
    """Exactly one stderr line, or None for a fresh answer.

    Stale shape -- a stateless count, the meaning it carries, and the repair,
    identical on every read while the tree stays stale, so it is always
    visible and never wallpaper by surprise:

      Warning: index is stale: 2 modified files, 1 deleted file -- <path>;
      answers describe the index, not the tree; refresh with code-indexer
      reindex-project <path>

    The meaning sentence is not optional: a count without it reads as a fact
    about the tree rather than about the answer, which is the one misreading
    the verdict exists to prevent -- so it rides beside the refresh hint the
    way the ``unknown`` branch already carries it. The line is short by
    design: the per-file reasons, the revision and the pass timestamp stay in
    the JSON block every call, and ``index-status`` prints them on request.
    Nothing is remembered between invocations -- a CLI cannot know it is the
    same reader, so a marker file, a manifest mtime or a suppressed first
    occurrence would make silence mean something the process cannot verify.
    There is no session token: every read states the verdict it just computed.

    ``include_withheld`` appends the withheld-file state (a skip, never a
    stale reason) for callers that do not print it themselves; ``index-status``
    passes False because its own line already carries it.
    """
    if verdict.state == FRESH:
        return None
    if verdict.state == UNKNOWN:
        return ("Warning: index staleness check did not run "
                f"({verdict.detail}); answers describe the index, not the tree.")
    facts = [_count_phrase(verdict.reasons)]
    withheld = _withheld_summary(verdict.withheld, verdict.withheld_count)
    if include_withheld and withheld:
        facts.append(withheld)
    return (f"Warning: index is stale: {'; '.join(facts)} -- {project_path}; "
            "answers describe the index, not the tree; "
            f"refresh with code-indexer reindex-project {project_path}")


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
    merged_withheld = [s for v in verdicts.values() for s in v.withheld]
    withheld_total = sum(v.withheld_count for v in verdicts.values())
    unexamined_total = sum(v.unexamined_count for v in verdicts.values())
    known = all(v.known for v in verdicts.values())
    any_stale = any(v.stale for v in verdicts.values())
    return {
        "stale": any_stale,
        "stale_known": known,
        "stale_reasons": [r.to_dict() for r in merged[:REASON_CAP]],
        "stale_reason_count": total,
        "withheld_count": withheld_total,
        "withheld_files": [s.to_dict() for s in merged_withheld[:REASON_CAP]],
        "withheld_summary": _withheld_summary(merged_withheld, withheld_total),
        "withheld_unexamined_count": unexamined_total,
        "index_revision": None,
        "last_indexed": None,
    }
