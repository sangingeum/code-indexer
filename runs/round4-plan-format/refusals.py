"""Capture the refusal matrix as it actually prints (leg evidence).

Every case is prepared in a throwaway project, then the CLI is run for real
and only its exit code and first stderr line are kept. Cases a plan has to be
built for are constructed with the same builders the commands use and sealed,
so each document is a valid plan that the reader must still refuse.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import hashlib

ROOT = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(ROOT, "fixture-refusals")
PLAN_DIR = os.path.join(FIXTURE, ".code-indexer", "plans")
sys.path.insert(0, os.path.join(ROOT, "..", "..", "src"))

from code_indexer import plans  # noqa: E402

PROJECT = "plan-refusals"
TWO = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"


def write(name, text, mode="w"):
    path = os.path.join(FIXTURE, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as handle:
        handle.write(text)
    return path


def run(args):
    proc = subprocess.run(["code-indexer", *args], capture_output=True, text=True)
    first = (proc.stderr.strip().splitlines() or [proc.stdout.strip().splitlines()
                                                  and proc.stdout.strip().splitlines()[0]
                                                  or ""])[0]
    return proc.returncode, first


def save(document):
    path = plans.write_plan(plans.seal(document), PLAN_DIR)
    return path


def base_document(document):
    return json.loads(json.dumps(document))


def document_for(name, operation="replace_body", text="    return 41"):
    tf = plans.read_text_file(FIXTURE, name)
    derived, _ = plans.declarations(name, tf.text)
    decl = derived[0]
    if operation == "delete_declaration":
        edits = [plans.delete_declaration_edit(tf, decl)]
    else:
        edits = [plans.replace_body_edit(tf, decl, text)]
    return plans.build_document(
        operation=operation,
        repository={"project": PROJECT, "index_revision": "fixture"},
        files=[plans.file_record(tf, "python")],
        target=plans.locator(tf, decl, "python"), edits=edits,
        created_at="2026-11-07T00:00:00Z")


def main():
    import shutil
    shutil.rmtree(FIXTURE, ignore_errors=True)
    os.makedirs(FIXTURE)
    write("mod.py", TWO)
    write("inline.py", "def inline(): return 1\n")
    write("mixed.py", "def one():\r\n    return 1\n")
    write("bad.py", b"# caf\xe9\ndef one():\n    return 1\n", mode="wb")
    write("dup.py", "def dup():\n    return 1\n\n\ndef dup():\n    return 1\n")
    added = subprocess.run(["code-indexer", "add-project", FIXTURE, "--name",
                            PROJECT, "--vectors", "off"],
                           capture_output=True, text=True)
    if added.returncode != 0:
        sys.stderr.write(f"add-project exit {added.returncode}; "
                         f"stdout={added.stdout!r} stderr={added.stderr!r}\n")

    cases = []

    cases.append(("PathEscapesProject (absolute)",
                  ["plan-delete-declaration", "alpha", "--project", PROJECT,
                   "--file", "/etc/passwd"]))
    cases.append(("PathEscapesProject (..)",
                  ["plan-delete-declaration", "alpha", "--project", PROJECT,
                   "--file", "../outside.py"]))
    cases.append(("NotUtf8",
                  ["plan-delete-declaration", "one", "--project", PROJECT,
                   "--file", "bad.py"]))
    cases.append(("MixedNewlines",
                  ["plan-delete-declaration", "one", "--project", PROJECT,
                   "--file", "mixed.py"]))
    cases.append(("CarriageReturnInPlan",
                  ["plan-replace-body", "alpha", "--project", PROJECT,
                   "--file", "mod.py", "--text", "    return 1\r"]))
    cases.append(("SubLineEditUnsupported",
                  ["plan-replace-body", "inline", "--project", PROJECT,
                   "--file", "inline.py", "--text", "    return 2"]))
    cases.append(("rename_symbol reserved",
                  ["plan-rename-symbol", "alpha", "--project", PROJECT]))
    cases.append(("Plan not found",
                  ["plan-validate", "sha256:" + "9" * 64, "--project",
                   PROJECT]))

    newline = base_document(document_for("mod.py"))
    newline["files"][0]["newline"] = "crlf"
    cases.append(("NewlineMismatch", ["plan-validate", save(newline),
                                       "--project", PROJECT]))

    overlap = base_document(document_for("mod.py"))
    tf = plans.read_text_file(FIXTURE, "mod.py")
    wide_end = tf.line_end_bytes[1]
    wide = {"kind": "replace_body", "path": "mod.py",
            "start_line": 1, "end_line": 2, "start_byte": 0,
            "end_byte": wide_end,
            "expected_old_text_hash": "sha256:" + hashlib.sha256(
                tf.body[0:wide_end]).hexdigest(),
            "new_text": "def alpha():"}
    overlap["edits"] = [overlap["edits"][0], wide]
    cases.append(("OverlappingEdits", ["plan-validate", save(overlap),
                                        "--project", PROJECT]))

    ambiguous_order = base_document(document_for("mod.py"))
    point = tf.line_starts[3]
    first = {"kind": "insert_symbol", "path": "mod.py",
             "position": "before", "start_line": 4, "start_byte": point,
             "end_byte": point, "new_text": "def x():\n    pass"}
    second = dict(first, new_text="def y():\n    pass")
    ambiguous_order["edits"] = [first, second]
    cases.append(("AmbiguousOrder", ["plan-validate", save(ambiguous_order),
                                      "--project", PROJECT]))

    semantic = base_document(document_for("mod.py"))
    semantic["validation"]["semantic_required"] = True
    cases.append(("SemanticProviderUnavailable",
                  ["plan-validate", save(semantic), "--project", PROJECT]))

    wrong_hash = base_document(document_for("mod.py"))
    wrong_hash["target"]["decl_hash"] = "sha256:" + "1" * 64
    cases.append(("LocatorMismatch", ["plan-validate", save(wrong_hash),
                                       "--project", PROJECT]))

    dup = plans.read_text_file(FIXTURE, "dup.py")
    derived, _ = plans.declarations("dup.py", dup.text)
    ambiguous = plans.build_document(
        operation="replace_body",
        repository={"project": PROJECT, "index_revision": "fixture"},
        files=[plans.file_record(dup, "python")],
        target=plans.locator(dup, derived[0], "python"),
        edits=[plans.replace_body_edit(dup, derived[0], "    return 2")],
        created_at="2026-11-07T00:00:00Z")
    cases.append(("AmbiguousNode", ["plan-validate", save(ambiguous),
                                     "--project", PROJECT]))

    stale_path = save(document_for("mod.py"))
    cases.append(("PlanStale (a stale file)",
                  ["plan-validate", stale_path, "--project", PROJECT],
                  lambda: write("mod.py", TWO.replace("return 1", "return 7"))))

    print("| case | exit | line it printed |")
    print("|---|---|---|")
    for case in cases:
        label, args = case[0], case[1]
        if len(case) > 2 and case[2] is not None:
            case[2]()
        code, line = run(args)
        print(f"| {label} | {code} | `{line}` |")

    subprocess.run(["code-indexer", "remove-project", "--project", PROJECT],
                   capture_output=True, text=True)


if __name__ == "__main__":
    main()
