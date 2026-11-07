"""A stub ``keeper`` for the apply tests, and the rig that installs it.

The apply path talks to filekeeper as a subprocess, so the honest way to test
it is another subprocess on PATH. This stub is a small, faithful keeper: it
edits line ranges, honours ``--expected-hash`` and ``--expected-content``,
answers ``--dry-run``, records a journal and undoes an operation. It also
fails on demand, which is how the partial-apply and refused-undo cases are
reached without a real failure.

Modes are read from the environment so one installed stub covers every test:

  STUB_KEEPER_HOME        where the journal lives (required)
  STUB_KEEPER_FAIL_AT     fail the Nth mutation of the run (1-based)
  STUB_KEEPER_FAIL_CODE   error code to report (default INVALID_ARGUMENT)
  STUB_KEEPER_FAIL_EXIT   exit code to report (default 2)
  STUB_KEEPER_UNDO_REFUSE refuse every undo with UNDO_CONFLICT, exit 12
  STUB_KEEPER_DROP_FLAG   do not define this flag at all (help and parser)
"""

from __future__ import annotations

import os
import stat

STUB_SOURCE = r'''#!/usr/bin/env python3
"""A stub keeper: line-range mutations with the guards this project passes."""
import argparse
import base64
import hashlib
import json
import os
import re
import sys

TERMINATOR = re.compile(rb"\r\n|\n")


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def split_lines(data):
    """[(start, end_without_terminator, end_with_terminator)] per line."""
    rows = []
    start = 0
    for match in TERMINATOR.finditer(data):
        rows.append((start, match.start(), match.end()))
        start = match.end()
    if start < len(data):
        rows.append((start, len(data), len(data)))
    return rows


def emit(payload, code=0):
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()
    raise SystemExit(code)


def fail(code, message, exit_code, path, extra=None):
    details = {"path": path}
    if extra:
        details.update(extra)
    emit({"ok": False, "error": {"code": code, "message": message,
                                 "retryable": False, "details": details}},
         exit_code)


def drop_flag():
    return os.environ.get("STUB_KEEPER_DROP_FLAG", "")


def add_guards(sub):
    """The three guards this project passes, unless one is being withheld."""
    if drop_flag() != "--expected-hash":
        sub.add_argument("--expected-hash")
    if drop_flag() != "--expected-content":
        sub.add_argument("--expected-content")
    if drop_flag() != "--dry-run":
        sub.add_argument("--dry-run", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(prog="keeper")
    parser.add_argument("--root")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--actor")
    parser.add_argument("--task-id")
    parser.add_argument("--workspace")
    parser.add_argument("--home")
    groups = parser.add_subparsers(dest="group")
    filep = groups.add_parser("file")
    files = filep.add_subparsers(dest="command")
    hashp = files.add_parser("hash")
    hashp.add_argument("path")
    repl = files.add_parser("replace-lines")
    repl.add_argument("--start", type=int, required=True)
    repl.add_argument("--end", type=int, required=True)
    repl.add_argument("--replacement", required=True)
    add_guards(repl)
    repl.add_argument("path")
    dele = files.add_parser("delete-lines")
    dele.add_argument("--start", type=int, required=True)
    dele.add_argument("--end", type=int, required=True)
    add_guards(dele)
    dele.add_argument("path")
    ins = files.add_parser("insert-lines")
    ins.add_argument("--content", required=True)
    ins.add_argument("--line", type=int)
    ins.add_argument("--position", default="before")
    add_guards(ins)
    ins.add_argument("path")
    histp = groups.add_parser("history")
    hists = histp.add_subparsers(dest="command")
    undop = hists.add_parser("undo")
    undop.add_argument("operation_id")
    listp = hists.add_parser("list")
    listp.add_argument("--task-id")
    listp.add_argument("--path")
    listp.add_argument("--limit", type=int)
    return parser


def journal_path():
    home = os.environ.get("STUB_KEEPER_HOME")
    if not home:
        raise SystemExit("STUB_KEEPER_HOME is not set")
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "journal.jsonl")


def load_journal():
    path = journal_path()
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_journal(entry):
    path = journal_path()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def read_file(root, rel):
    full = os.path.join(root, rel)
    with open(full, "rb") as handle:
        return handle.read()


def write_file(root, rel, data):
    full = os.path.join(root, rel)
    with open(full, "wb") as handle:
        handle.write(data)


def mutation_count():
    return sum(1 for entry in load_journal() if entry.get("kind") == "mutation")


def guard(args, before, rel, region_start, region_end):
    expected = getattr(args, "expected_hash", None)
    if expected and expected != digest(before):
        fail("CONTENT_CONFLICT",
             "file content changed since it was read; refusing to edit", 5,
             rel, {"expected_hash": expected, "actual_hash": digest(before)})
    wanted = getattr(args, "expected_content", None)
    if wanted is not None:
        actual = before[region_start:region_end].decode("utf-8")
        if actual != wanted:
            fail("CONTENT_CONFLICT",
                 "the target region does not match expected_content; refusing "
                 "to edit", 5, rel,
                 {"expected_content": wanted, "actual_content": actual})


def edit(args, rel, apply_edit):
    root = args.root or os.getcwd()
    try:
        before = read_file(root, rel)
    except OSError as exc:
        fail("NOT_FOUND", str(exc), 3, rel)
    lines = split_lines(before)
    rows = lines
    if getattr(args, "start", None) is not None:
        start, end = args.start, args.end
        if start < 1 or end < start or end > len(rows):
            fail("OUT_OF_RANGE", "line range is outside the file", 2, rel)
        region_start = rows[start - 1][0]
        region_end = rows[end - 1][2]
    else:
        if args.position == "end":
            region_start = region_end = len(before)
        else:
            if not 1 <= args.line <= len(rows) + 1:
                fail("OUT_OF_RANGE", "line is outside the file", 2, rel)
            region_start = region_end = (rows[args.line - 1][0]
                                         if args.line <= len(rows) else len(before))
    guard(args, before, rel, region_start, region_end)
    after = apply_edit(before, rows, region_start, region_end)
    count = mutation_count() + 1
    fail_at = os.environ.get("STUB_KEEPER_FAIL_AT")
    if fail_at and count == int(fail_at):
        fail(os.environ.get("STUB_KEEPER_FAIL_CODE", "INVALID_ARGUMENT"),
             "the stub keeper was told to fail this mutation",
             int(os.environ.get("STUB_KEEPER_FAIL_EXIT", "2")), rel)
    operation_id = "op_%014x" % (count + 0x1000)
    dry = bool(getattr(args, "dry_run", False))
    if not dry:
        write_file(root, rel, after)
        append_journal({"kind": "mutation", "operation_id": operation_id,
                        "path": rel, "task_id": getattr(args, "task_id", None),
                        "actor": getattr(args, "actor", None),
                        "before": base64.b64encode(before).decode("ascii"),
                        "before_hash": digest(before),
                        "after_hash": digest(after)})
    emit({"ok": True, "path": rel, "changed": not dry,
          "before_hash": digest(before),
          "after_hash": None if dry else digest(after),
          "predicted_after_hash": digest(after) if dry else None,
          "operation_id": None if dry else operation_id,
          "undo_available": not dry, "dry_run": dry,
          "would_change": True if dry else None})


def do_replace(args, rel):
    def apply_edit(before, rows, start, end):
        terminator = rows[args.end - 1][2] - rows[args.end - 1][1]
        block = args.replacement.encode("utf-8")
        if terminator:
            block += before[rows[args.end - 1][1]:rows[args.end - 1][2]]
        return before[:start] + block + before[end:]
    edit(args, rel, apply_edit)


def do_delete(args, rel):
    return edit(args, rel, lambda before, rows, start, end:
                before[:start] + before[end:])


def do_insert(args, rel):
    def apply_edit(before, rows, start, end):
        block = args.content.encode("utf-8") + b"\n"
        if start and before[start - 1:start] not in (b"\n", b""):
            block = b"\n" + block
        return before[:start] + block + before[start:]
    edit(args, rel, apply_edit)


def main(argv):
    args = build_parser().parse_args(argv)
    if args.group == "file":
        if args.command == "hash":
            root = args.root or os.getcwd()
            try:
                data = read_file(root, args.path)
            except OSError as exc:
                fail("NOT_FOUND", str(exc), 3, args.path)
            emit({"ok": True, "path": args.path,
                  "content_hash": digest(data)})
        if args.command == "replace-lines":
            do_replace(args, args.path)
        if args.command == "delete-lines":
            do_delete(args, args.path)
        if args.command == "insert-lines":
            do_insert(args, args.path)
    if args.group == "history":
        if args.command == "list":
            entries = [entry for entry in load_journal()
                       if not args.task_id or entry.get("task_id") == args.task_id]
            emit({"ok": True, "operations": entries})
        if args.command == "undo":
            if os.environ.get("STUB_KEEPER_UNDO_REFUSE"):
                fail("UNDO_CONFLICT",
                     "a path changed after the operation; refusing to undo",
                     12, "")
            for entry in load_journal():
                if entry.get("operation_id") != args.operation_id:
                    continue
                root = args.root or os.getcwd()
                current = read_file(root, entry["path"])
                if digest(current) != entry["after_hash"]:
                    fail("UNDO_CONFLICT",
                         "a path changed after the operation; refusing to undo",
                         12, entry["path"])
                write_file(root, entry["path"],
                           base64.b64decode(entry["before"]))
                emit({"ok": True, "operation_id": args.operation_id,
                      "undo_available": False})
            fail("NOT_FOUND", "no such operation", 2, "")
    emit({"ok": False, "error": {"code": "USAGE", "message": "nothing to do",
                                 "retryable": False, "details": {}}}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
'''


def install(bin_dir: str, name: str = "keeper") -> str:
    """Write the stub into ``bin_dir`` and make it executable."""
    os.makedirs(bin_dir, exist_ok=True)
    path = os.path.join(bin_dir, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(STUB_SOURCE)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP
             | stat.S_IXOTH)
    return path
