"""Applying a plan through filekeeper, and reading what happened afterwards.

The shape is ADR-0005's: a read-only pre-pass that shares its code with
``plan-validate``, then one filekeeper line-range mutation per edit, each
carrying the hash observed immediately before it. What a caller may assume is
four observables and nothing more: nothing is written before every
precondition passes, each file's bytes move from old to new through keeper's
hash guard, the operations that ran can be undone in reverse order, and no
promise is made across files -- one file's outcome does not describe another's.

A failure stops the run. No compensating write is issued, because an undo is
most likely to be refused just after a failure and a half-undone tree is the
state no report describes. The report is assembled from what happened, and the
ordered operation ids are the recovery reference.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import plans, ts_chunker
from .errors import (
    EXIT_LOCK,
    IndexerError,
    KeeperFailureError,
    PartialApplyError,
    PlanStaleError,
)

RECEIPTS_DIR = ".code-indexer/receipts"
APPLIED = "applied"
WOULD_APPLY = "would_apply"
FAILED = "failed"
PARTIALLY_APPLIED = "partially_applied"
NOT_ATTEMPTED = "not_attempted"

PARSE_CAVEAT = (
    "parse verdicts only -- a file the parser accepts is not a file that "
    "compiles or type-checks, and a file whose language has no grammar here "
    "reports parse_unavailable, which is a failed claim rather than a pass")


@dataclass
class EditOutcome:
    """One edit as it went: its range, its operation id, or keeper's refusal."""

    kind: str
    start_line: int | None = None
    end_line: int | None = None
    position: str | None = None
    operation_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "start_line": self.start_line,
                               "end_line": self.end_line}
        if self.position is not None:
            out["position"] = self.position
        out["operation_id"] = self.operation_id
        if self.error_code is not None:
            out["error_code"] = self.error_code
            out["error_message"] = self.error_message
        return out

    def line(self) -> str:
        span = ("end of file" if self.position == "end"
                else f"lines {self.start_line}-{self.end_line}"
                if self.end_line is not None
                else f"line {self.start_line}")
        if self.error_code is not None:
            return f"{self.kind} {span}: {self.error_code}: {self.error_message}"
        if self.operation_id is not None:
            return f"{self.kind} {span}: {self.operation_id}"
        return f"{self.kind} {span}"


@dataclass
class FileOutcome:
    path: str
    status: str = NOT_ATTEMPTED
    edits: list[EditOutcome] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {"path": self.path, "status": self.status,
                "edits": [edit.record() for edit in self.edits]}


@dataclass
class Application:
    """What one run did, and what it could not do."""

    document: dict[str, Any]
    dry_run: bool
    files: list[FileOutcome] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    post_apply: list[dict[str, Any]] = field(default_factory=list)
    refresh: str = "skipped"
    receipt_path: str | None = None
    error: IndexerError | None = None

    @property
    def partial(self) -> bool:
        """True when a failure left bytes written and the plan incomplete.

        Read from the operations, not from the file statuses: a file whose
        first edit landed and whose second was refused WAS written, and a
        count of per-file statuses cannot say so. A dry run writes nothing,
        so it has no operations and is never partial.
        """
        if self.error is None:
            return False
        return bool(self.operations)

    @property
    def exit_code(self) -> int:
        return 0 if self.error is None else self.error.exit_code

    @property
    def error_type(self) -> str | None:
        return None if self.error is None else self.error.error_type

    def payload(self) -> dict[str, Any]:
        """The machine-readable report, carrying ADR-0004's identity verdict."""
        out: dict[str, Any] = {
            "schema": 1,
            "plan_id": self.document.get("plan_id"),
            "identity": plans.identity_block(),
            "dry_run": self.dry_run,
            "partial": self.partial,
            "files": [item.record() for item in self.files],
            "operations": list(self.operations),
            "undo": undo_commands(self.operations),
            "receipt_path": self.receipt_path,
            "post_apply": self.post_apply,
            "refresh": self.refresh,
            "caveat": PARSE_CAVEAT,
        }
        if self.error is not None:
            out["ErrorType"] = self.error.error_type
            out["error"] = str(self.error)
            out["exit_code"] = self.error.exit_code
        return out

    def render(self) -> str:
        """The text report: every file, every edit, and the way back."""
        lines = [f"plan {self.document.get('plan_id')}",
                 "identity structural ("
                 + ", ".join(plans.IDENTITY_BASIS) + ")"]
        if self.dry_run:
            lines.append("dry run: keeper's --dry-run went to every call, so "
                         "no file was written")
        for item in self.files:
            lines.append(f"- {item.path}: {item.status}")
            for edit in item.edits:
                lines.append(f"    {edit.line()}")
        written = sum(1 for item in self.files if item.status in (
            APPLIED, WOULD_APPLY))
        lines.append(f"{written} of {len(self.files)} file(s) "
                     + ("would apply" if self.dry_run else "applied"))
        for item in self.files:
            if item.status != PARTIALLY_APPLIED:
                continue
            landed = [edit for edit in item.edits if edit.operation_id]
            lines.append(f"{len(landed)} of {len(item.edits)} edit(s) in "
                         f"{item.path} landed; the rest were refused")
        lines.append(f"partial: {str(self.partial).lower()}")
        if self.operations:
            lines.append("recovery (undo in reverse order):")
            lines.extend(f"  {command}" for command in
                         undo_commands(self.operations))
        if self.receipt_path:
            lines.append(f"receipt {self.receipt_path}")
        if self.post_apply:
            lines.append("post-apply:")
            for item in self.post_apply:
                detail = item["verdict"]
                if item.get("lines"):
                    detail += " at line(s) " + ", ".join(
                        str(line) for line in item["lines"])
                lines.append(f"  {item['path']} {detail}")
        lines.append(PARSE_CAVEAT)
        lines.append(f"refresh: {self.refresh}")
        return "\n".join(lines)


def undo_commands(operations: list[str]) -> list[str]:
    """The recovery reference: one command per operation, in reverse order."""
    return [f"keeper history undo {op}" for op in reversed(operations)]


@dataclass
class RecoveryStep:
    operation_id: str
    ok: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class Recovery:
    """The outcome of undoing a report's operations, refusal and all."""

    steps: list[RecoveryStep] = field(default_factory=list)
    stopped: bool = False

    @property
    def failure(self) -> RecoveryStep | None:
        return self.steps[-1] if self.stopped and self.steps else None


def recover(operations: list[str], keeper) -> Recovery:
    """Undo an ordered operation list in reverse, stopping at the first refusal.

    This is the reference the report's recovery lines name, not a second
    command: the deliverable is the list. keeper refuses an undo with exit 12
    when an affected path moved after the operation, and the refusal ends
    recovery -- it is reported with its code and never retried, because a
    retry would try to force a state no report describes.
    """
    steps: list[RecoveryStep] = []
    for operation_id in reversed(list(operations)):
        envelope = keeper.undo(operation_id)
        steps.append(RecoveryStep(operation_id, envelope.ok,
                                  envelope.error_code, envelope.error_message))
        if not envelope.ok:
            return Recovery(steps=steps, stopped=True)
    return Recovery(steps=steps, stopped=False)


# ---------------------------------------------------------------------------
# one edit, one keeper call
# ---------------------------------------------------------------------------


def _translate(text: str, newline: str) -> str:
    """A plan's LF block in the file's own convention (ADR-0004 rule 5)."""
    if newline == plans.NEWLINE_CRLF:
        return text.replace("\n", "\r\n")
    return text


def region_text(tf: plans.TextFile, start_line: int, end_line: int) -> str:
    """The stored text of a line range, terminator included exactly as stored.

    keeper's ``--expected-content`` compares the region's own bytes, which
    carry the last line's terminator; a final line that has none carries none
    here either.
    """
    start = tf.line_starts[start_line - 1]
    end = (tf.line_starts[end_line] if end_line < tf.line_count()
           else len(tf.body))
    return tf.body[start:end].decode("utf-8")


def _call(keeper, tf: plans.TextFile, edit: dict[str, Any],
          current_hash: str, dry_run: bool):
    """The one keeper mutation this edit asks for."""
    newline = tf.newline
    if edit["kind"] == "insert_symbol":
        content = _translate(edit["new_text"], newline)
        if edit.get("position") == "end":
            return keeper.insert_lines(tf.rel_path, content=content,
                                       position="end",
                                       expected_hash=current_hash,
                                       dry_run=dry_run)
        return keeper.insert_lines(tf.rel_path, content=content,
                                   line=int(edit["start_line"]),
                                   position="before",
                                   expected_hash=current_hash,
                                   dry_run=dry_run)
    start = int(edit["start_line"])
    end = int(edit["end_line"])
    expected = (region_text(tf, start, end)
                if "expected_old_text" in edit else None)
    if edit["kind"] == "delete_declaration":
        return keeper.delete_lines(tf.rel_path, start=start, end=end,
                                   expected_hash=current_hash,
                                   expected_content=expected, dry_run=dry_run)
    return keeper.replace_lines(tf.rel_path, start=start, end=end,
                                replacement=_translate(edit["new_text"],
                                                       newline),
                                expected_hash=current_hash,
                                expected_content=expected, dry_run=dry_run)


def _outcome(edit: dict[str, Any]) -> EditOutcome:
    return EditOutcome(
        kind=edit["kind"],
        start_line=(int(edit["start_line"]) if "start_line" in edit else None),
        end_line=(int(edit["end_line"]) if "end_line" in edit else None),
        position=edit.get("position") if edit["kind"] == "insert_symbol" else None)


def _refusal(envelope, path: str, applied: int) -> IndexerError:
    """Map one keeper refusal onto this tool's taxonomy, keeping its code."""
    message = (f"{path}: keeper refused the edit ({envelope.error_code}): "
               f"{envelope.error_message}")
    if envelope.exit_code == EXIT_LOCK:
        return PlanStaleError(
            f"{message}; keeper compared the hash it was given with the file "
            "on disk, so the file changed after the pre-pass read it -- no "
            "further edit was attempted")
    if applied:
        return PartialApplyError(
            f"{message}; {applied} operation(s) had already run and stay "
            "written until they are undone in reverse order")
    return KeeperFailureError(message)


# ---------------------------------------------------------------------------
# post-apply: the parser's verdict, and nothing it cannot prove
# ---------------------------------------------------------------------------


def _problem_lines(root_node, limit: int = 12) -> list[int]:
    """The lines the parser flagged, read from its own error and missing nodes."""
    seen: set[int] = set()
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            seen.add(node.start_point[0] + 1)
        stack.extend(reversed(node.children))
    return sorted(seen)[:limit]


def post_apply_verdict(root: str, paths: list[str]) -> list[dict[str, Any]]:
    """Reparse every modified file and report what the parser said.

    The verdict is the parser's own -- ``has_error`` and ``is_missing`` -- not
    the chunker's, which never fails because it falls back. Nothing is
    written: this is a read of the bytes keeper just wrote.
    """
    out: list[dict[str, Any]] = []
    for path in paths:
        tf = plans.read_text_file(root, path)
        tree, verdict = ts_chunker.parse_tree(path, tf.text)
        entry: dict[str, Any] = {"path": path, "verdict": verdict, "lines": []}
        if tree is not None and verdict == ts_chunker.PARSE_ERROR:
            entry["lines"] = _problem_lines(tree.root_node)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# the receipt
# ---------------------------------------------------------------------------


def receipt_path_for(root: str, plan_id: str,
                     now: datetime | None = None) -> str:
    """``.code-indexer/receipts/<plan-hash>-<utc-timestamp>.json``."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    digest = plan_id.split(":", 1)[-1]
    return os.path.join(root, RECEIPTS_DIR, f"{digest}-{stamp}.json")


def write_receipt(path: str, payload: dict[str, Any]) -> str:
    """Write one receipt and return its path. Losing it loses convenience."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def apply_plan(document: dict[str, Any], root: str, keeper, *,
               requested: str | None = None, dry_run: bool = False) -> Application:
    """Check every precondition, then apply edit by edit, stopping on failure.

    The pre-pass is ``plans.run_preconditions`` -- the same code
    ``plan-validate`` runs -- so a refusal here is exit 5 ``PlanStale`` with
    nothing written, and the two commands cannot disagree about what stale
    means. The loop then walks the format's own order (files ascending, start
    lines descending) and hands keeper the hash observed immediately before
    each edit.
    """
    files = plans.run_preconditions(document, root, requested)
    ordered = plans.ordered_edits(document["edits"], files)
    application = Application(document=document, dry_run=dry_run)
    # One record per distinct file: the pre-pass refuses a files[] path named
    # twice, so this is the report's own shape rather than a second guard --
    # a report that carried two records for one file would say not_attempted
    # about a file it had just written.
    application.files = [FileOutcome(path) for path in
                         sorted({record["path"]
                                 for record in document["files"]})]
    by_path = {item.path: item for item in application.files}
    current = {path: tf.content_hash() for path, tf in files.items()}

    applied_edits = 0
    for edit in ordered:
        path = edit["path"]
        outcome = by_path[path]
        edit_record = _outcome(edit)
        outcome.edits.append(edit_record)
        envelope = _call(keeper, files[path], edit, current[path], dry_run)
        if envelope.ok:
            applied_edits += 1
            outcome.status = WOULD_APPLY if dry_run else APPLIED
            edit_record.operation_id = envelope.operation_id
            if not dry_run:
                if envelope.operation_id:
                    application.operations.append(envelope.operation_id)
                current[path] = envelope.after_hash or current[path]
            continue
        outcome.status = (
            PARTIALLY_APPLIED
            if any(item.operation_id for item in outcome.edits) else FAILED)
        edit_record.error_code = envelope.error_code
        edit_record.error_message = envelope.error_message
        application.error = _refusal(envelope, path, applied_edits)
        break

    if not dry_run:
        modified = [item.path for item in application.files
                    if item.status == APPLIED]
        application.post_apply = post_apply_verdict(root, modified)
        if application.operations:
            planned = receipt_path_for(root, document["plan_id"])
            application.receipt_path = planned
            write_receipt(planned, application.payload())
    return application
