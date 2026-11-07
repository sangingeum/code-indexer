"""Gate capture for round 4, the plan format (bars 1-7).

Every run happens in a throwaway project under a temporary directory; the real
checkout is read as source only. Output is masked on the way out.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "src"))

from code_indexer import plans  # noqa: E402

CLI = os.path.join(ROOT, ".venv", "bin", "code-indexer")
MASKS: list[tuple[str, str]] = []
RESULTS: list[tuple[str, str, str]] = []


def mask(text: str) -> str:
    for real, token in MASKS:
        text = text.replace(real, token)
    return text.replace(os.path.expanduser("~"), "<home>")


def out(text: str = "") -> None:
    print(mask(text))


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    out(f"CHECK {name}: {'PASS' if ok else 'FAIL'} -- {detail}")


def run(argv, env, label="", binary=False):
    proc = subprocess.run(argv, capture_output=True, text=not binary, env=env)
    out(f"$ {' '.join(argv)}")
    if label:
        out(f"# {label}")
    body = (proc.stdout if binary else (proc.stdout or ""))
    if not binary:
        body = (proc.stdout or "") + (proc.stderr or "")
    if binary:
        out(f"(stdout {len(body)} bytes, shown as text below)")
        out(body.decode("utf-8", "replace"))
        err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        if err:
            out(err)
    else:
        out(body.rstrip() or "(no output)")
    out(f"exit {proc.returncode}")
    out()
    return proc.returncode, body, proc.stderr


def first_line(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def sha(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return "MISSING"


def dir_hashes(path: str) -> dict[str, str]:
    if not os.path.isdir(path):
        return {"(absent)": "MISSING"}
    return {name: sha(os.path.join(path, name))
            for name in sorted(os.listdir(path))}


def write(project: str, name: str, payload) -> str:
    path = os.path.join(project, name)
    os.makedirs(os.path.dirname(path) or project, exist_ok=True)
    mode = "wb" if isinstance(payload, bytes) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8",
                                                    "newline": ""})) as fh:
        fh.write(payload)
    return path


def canonical(document: dict) -> bytes:
    """An independent canonicaliser: sorted keys, no spaces, utf-8, no \\n."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def document_for(root: str, rel: str, text: str, operation="replace_body",
                 project="gateproj", decl_index=0):
    tf = plans.read_text_file(root, rel)
    derived, _verdict = plans.declarations(rel, tf.text)
    decl = derived[decl_index]
    if operation == "delete_declaration":
        edits = [plans.delete_declaration_edit(tf, decl)]
    else:
        edits = [plans.replace_body_edit(tf, decl, text)]
    return plans.build_document(
        operation=operation,
        repository={"project": project, "index_revision": "gate"},
        files=[plans.file_record(tf, "python")],
        target=plans.locator(tf, decl, "python"), edits=edits,
        created_at="2026-11-07T00:00:00Z")


def save(document: dict, plan_dir: str) -> str:
    return plans.write_plan(plans.seal(document), plan_dir)


def raw_edit(tf, kind, start_line, end_line, new_text=None, insert=None):
    if insert is not None:
        edit = {"kind": "insert_symbol", "path": tf.rel_path,
                "position": "before", "start_line": insert,
                "start_byte": tf.line_starts[insert - 1],
                "end_byte": tf.line_starts[insert - 1],
                "new_text": new_text}
        return edit
    start_byte = tf.line_starts[start_line - 1]
    end_byte = tf.line_end_bytes[end_line - 1]
    old = tf.body[start_byte:end_byte]
    edit = {"kind": kind, "path": tf.rel_path,
            "start_line": start_line, "end_line": end_line,
            "start_byte": start_byte, "end_byte": end_byte,
            "expected_old_text_hash": "sha256:" + hashlib.sha256(old).hexdigest()}
    if len(old) <= plans.REGION_TEXT_CAP:
        edit["expected_old_text"] = old.decode("utf-8")
    if new_text is not None:
        edit["new_text"] = new_text
    return edit


SAMPLE = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
ADJACENT = "def a():\n    return 1\ndef b():\n    return 2\n"
UNICODE = "def alpha():\n    return '\u00e9t\u00e9'\n\ndef beta():\n    return 2\n"


def main() -> int:
    work = tempfile.mkdtemp(prefix="r4gate-format-")
    project = os.path.join(work, "project")
    print_only = os.path.join(work, "printonly")
    os.makedirs(project)
    os.makedirs(print_only)
    MASKS.extend([(project, "<project-root>"), (print_only, "<printonly-root>"),
                  (os.path.join(work, "index"), "<index-root>"),
                  (work, "<work-dir>"), (ROOT, "<repo-root>")])
    env = dict(os.environ)
    env["INDEX_ROOT"] = os.path.join(work, "index")
    env["FILEKEEPER_HOME"] = os.path.join(work, "keeper-home")
    plan_dir = os.path.join(project, ".code-indexer", "plans")

    for name, payload in {
            "sample.py": SAMPLE, "adj.py": ADJACENT, "uni.py": UNICODE,
            "bom.py": b"\xef\xbb\xbf" + SAMPLE.encode(),
            "nonl.py": b"def gamma():\n    return 3",
            "crlf.py": b"def alpha():\r\n    return 1\r\n",
            "bad.py": b"# caf\xe9\ndef one():\n    return 1\n",
            "mixed.py": b"def one():\r\n    return 1\n",
            "dup.py": b"def dup():\n    return 1\n\n\ndef dup():\n    return 1\n",
            "za_first.py": "def one():\n    return 1\n",
            "zb_second.py": "def two():\n    return 2\n",
    }.items():
        write(project, name, payload)
    write(print_only, "solo.py", SAMPLE)

    try:
        run([CLI, "add-project", project, "--name", "gateproj", "--vectors", "off"],
            env, "register the throwaway project")
        run([CLI, "add-project", print_only, "--name", "printonly", "--vectors", "off"],
            env, "register the --print control project")

        out("=" * 72)
        out("BAR 1 -- plan_id is content-addressed over the canonical bytes")
        out("=" * 72)
        code, stdout, _err = run(
            [CLI, "plan-replace-body", "alpha", "--project", "gateproj",
             "--file", "sample.py", "--text", "    return 41", "--print"],
            env, "prepare with --print", binary=True)
        document = json.loads(stdout)
        without = {key: value for key, value in document.items()
                   if key != "plan_id"}
        recomputed = "sha256:" + hashlib.sha256(canonical(without)).hexdigest()
        check("1a plan_id == sha256(canonical bytes, plan_id absent)",
              recomputed == document["plan_id"],
              f"printed {document['plan_id']}, recomputed {recomputed}")
        check("1b --print output IS the canonical bytes (sorted, no padding, "
              "no trailing newline)",
              stdout == canonical(document) and not stdout.endswith(b"\n"),
              f"{len(stdout)} bytes; ends with {stdout[-1:]!r}")
        check("1c plan_id is the file name the prepare command uses",
              plans.plan_file_name(document["plan_id"])
              == document["plan_id"].split(":", 1)[1] + ".json",
              plans.plan_file_name(document["plan_id"]))

        out("=" * 72)
        out("BAR 2 -- three prepare commands: one file each; --print writes nothing")
        out("=" * 72)
        for command, extra, expect in (
            ("plan-replace-body", ["alpha", "--file", "sample.py", "--text", "    return 41"], 1),
            ("plan-insert-symbol", ["beta", "--file", "sample.py", "--text", "def gamma():\n    return 3"], 1),
            ("plan-delete-declaration", ["a", "--file", "adj.py"], 1),
        ):
            before = dir_hashes(plan_dir)
            run([CLI, command, *extra, "--project", "gateproj"], env,
                f"{command} writes")
            after = dir_hashes(plan_dir)
            added = sorted(set(after) - set(before))
            check(f"2 {command} wrote exactly one plan file",
                  len(added) == expect, f"added {added}")
            before = dir_hashes(plan_dir)
            run([CLI, command, *extra, "--project", "gateproj", "--print"], env,
                f"{command} --print")
            after = dir_hashes(plan_dir)
            check(f"2 {command} --print wrote nothing in the plan dir",
                  before == after, f"added {sorted(set(after) - set(before))}")

        before = os.path.isdir(os.path.join(print_only, ".code-indexer", "plans"))
        run([CLI, "plan-replace-body", "alpha", "--project", "printonly",
             "--file", "solo.py", "--text", "    return 41", "--print"], env,
            "--print in a project that has never written a plan dir")
        after = os.path.isdir(os.path.join(print_only, ".code-indexer", "plans"))
        check("2 --print does not even create the plan dir",
              before is False and after is False,
              f"plan dir existed before={before} after={after}")

        before = dir_hashes(plan_dir)
        code, body, _err = run([CLI, "plan-rename-symbol", "alpha", "--project",
                                "gateproj"], env, "rename_symbol is reserved")
        after = dir_hashes(plan_dir)
        check("2 rename_symbol is refused with exit 2 and writes no plan",
              code == 2 and before == after,
              f"exit {code}, {first_line(body)[:80]}")

        out("=" * 72)
        out("BAR 3 -- plan-validate and plan-show --diff write nothing")
        out("=" * 72)
        plan_path = save(document_for(project, "sample.py", "    return 41"), plan_dir)
        ref = json.load(open(plan_path, encoding="utf-8"))["plan_id"]
        manifest_dir = os.path.join(work, "index", "gateproj")
        watched = []
        for name in ("manifest.db", "manifest.db-wal", "manifest.db-shm"):
            watched.append(os.path.join(manifest_dir, name))
        touched = os.path.join(project, "sample.py")

        def snapshot():
            state = {"plan dir": dir_hashes(plan_dir), "touched file": sha(touched)}
            for path in watched:
                state[os.path.basename(path)] = sha(path)
            return state

        before = snapshot()
        out("# before the read batch")
        for name, value in before.items():
            out(f"  {name}: {value}")
        for _ in range(3):
            run([CLI, "plan-validate", ref, "--project", "gateproj", "--json"],
                env, "plan-validate")
        for _ in range(2):
            run([CLI, "plan-show", ref, "--project", "gateproj", "--diff"],
                env, "plan-show --diff")
        after = snapshot()
        check("3 five read calls changed no hash (plan dir, touched file, "
              "manifest db/-wal/-shm)", before == after,
              "identical" if before == after else f"{before} != {after}")

        control_before = snapshot()
        run([CLI, "plan-replace-body", "alpha", "--project", "gateproj",
             "--file", "uni.py", "--text", "    return 9"], env,
            "positive control: a prepare command IS a write")
        control_after = snapshot()
        check("3 positive control: the hash method notices a write",
              control_before != control_after,
              f"plan dir changed: {control_before['plan dir'] != control_after['plan dir']}")

        out("=" * 72)
        out("BAR 4 -- the refusal matrix")
        out("=" * 72)
        cases = []
        cases.append(("path escapes (absolute)",
                      [CLI, "plan-delete-declaration", "alpha", "--project",
                       "gateproj", "--file", "/etc/passwd"]))
        cases.append(("path escapes (..)",
                      [CLI, "plan-delete-declaration", "alpha", "--project",
                       "gateproj", "--file", "../outside.py"]))
        cases.append(("non-UTF-8",
                      [CLI, "plan-delete-declaration", "one", "--project",
                       "gateproj", "--file", "bad.py"]))
        cases.append(("mixed newlines",
                      [CLI, "plan-delete-declaration", "one", "--project",
                       "gateproj", "--file", "mixed.py"]))
        cases.append(("plan encodes a carriage return",
                      [CLI, "plan-replace-body", "alpha", "--project",
                       "gateproj", "--file", "sample.py", "--text",
                       "    return 1\r"]))

        newline_doc = json.loads(json.dumps(document_for(project, "sample.py", "    return 41")))
        newline_doc["files"][0]["newline"] = "crlf"
        cases.append(("newline mismatch",
                      [CLI, "plan-validate", save(newline_doc, plan_dir),
                       "--project", "gateproj"]))

        overlap_doc = json.loads(json.dumps(document_for(project, "sample.py", "    return 41")))
        tf = plans.read_text_file(project, "sample.py")
        overlap_doc["edits"] = [overlap_doc["edits"][0],
                                raw_edit(tf, "replace_body", 1, 2, "    return 8")]
        cases.append(("overlapping edits",
                      [CLI, "plan-validate", save(overlap_doc, plan_dir),
                       "--project", "gateproj"]))

        ambiguous_doc = json.loads(json.dumps(document_for(project, "sample.py", "    return 41")))
        ambiguous_doc["edits"] = [raw_edit(tf, "insert_symbol", 0, 0,
                                           "def x():\n    pass", insert=4),
                                  raw_edit(tf, "insert_symbol", 0, 0,
                                           "def y():\n    pass", insert=4)]
        cases.append(("two insertions at one point",
                      [CLI, "plan-validate", save(ambiguous_doc, plan_dir),
                       "--project", "gateproj"]))

        semantic_doc = json.loads(json.dumps(document_for(project, "sample.py", "    return 41")))
        semantic_doc["validation"]["semantic_required"] = True
        cases.append(("semantic_required",
                      [CLI, "plan-validate", save(semantic_doc, plan_dir),
                       "--project", "gateproj"]))

        dup_doc = document_for(project, "dup.py", "    return 2")
        cases.append(("ambiguous node",
                      [CLI, "plan-validate", save(dup_doc, plan_dir),
                       "--project", "gateproj"]))

        stale_path = os.path.join(project, "stale_case.py")
        write(project, "stale_case.py", SAMPLE)
        stale_doc = document_for(project, "stale_case.py", "    return 41")
        stale_saved = save(stale_doc, plan_dir)
        write(project, "stale_case.py", SAMPLE.replace("return 1", "return 7"))
        cases.append(("stale file",
                      [CLI, "plan-validate", stale_saved, "--project",
                       "gateproj"]))

        for label, argv in cases:
            code, body, err = run(argv, env, f"refusal case: {label}")
            line = first_line(err) or first_line(body)
            check(f"4 {label}", code not in (0,), f"exit {code}: {line[:150]}")

        out("=" * 72)
        out("BAR 5 -- coordinates and encoding as ADR-0004 section 3 states them")
        out("=" * 72)
        tf = plans.read_text_file(project, "sample.py")
        sample_doc = document_for(project, "sample.py", "    return 41")
        edit = sample_doc["edits"][0]
        check("5a lines are 1-based inclusive (alpha's body is line 2)",
              (edit["start_line"], edit["end_line"]) == (2, 2),
              f"start_line={edit['start_line']} end_line={edit['end_line']}")
        check("5b the terminator is not part of the line",
              edit["expected_old_text"] == "    return 1"
              and not edit["expected_old_text"].endswith("\n"),
              repr(edit["expected_old_text"]))
        check("5c start_byte/end_byte agree with the stored bytes",
              edit["start_byte"] == 13 and edit["end_byte"] == 25,
              f"{edit['start_byte']}-{edit['end_byte']}, bytes "
              f"{tf.body[edit['start_byte']:edit['end_byte']]!r}")

        corrupt = json.loads(json.dumps(sample_doc))
        corrupt["edits"][0]["start_byte"] += 1
        code, body, err = run([CLI, "plan-validate", save(corrupt, plan_dir),
                               "--project", "gateproj"], env,
                              "corrupted offset: cross-check must catch it")
        check("5d a corrupted start_byte is caught by the cross-check",
              code != 0 and "start_byte" in (err or body),
              f"exit {code}: {first_line(err)[:140]}")

        bom_plan = save(document_for(project, "bom.py", "    return 41"), plan_dir)
        bom = json.load(open(bom_plan, encoding="utf-8"))
        with open(os.path.join(project, "bom.py"), "rb") as fh:
            raw = fh.read()
        check("5e BOM outside every range: offsets start after it, the target "
              "starts at 0",
              raw[:3] == b"\xef\xbb\xbf"
              and bom["target"]["range"]["start_byte"] == 0
              and bom["edits"][0]["start_byte"] == 13,
              f"raw[:3]={raw[:3]!r} target.start_byte="
              f"{bom['target']['range']['start_byte']}")

        nonl_plan = save(document_for(project, "nonl.py", "    return 4"), plan_dir)
        nonl = json.load(open(nonl_plan, encoding="utf-8"))
        with open(os.path.join(project, "nonl.py"), "rb") as fh:
            nonl_raw = fh.read()
        check("5f a final line without a terminator is legal, and its end_byte "
              "is the end of the bytes",
              nonl["edits"][0]["end_byte"] == len(nonl_raw)
              and not nonl_raw.endswith(b"\n"),
              f"end_byte={nonl['edits'][0]['end_byte']} of {len(nonl_raw)} bytes")
        code, body, err = run([CLI, "plan-insert-symbol", "gamma", "--project",
                               "gateproj", "--file", "nonl.py", "--position",
                               "end", "--text", "def delta():\n    return 4",
                               "--print"], env,
                              "position: end for an insertion after the last line")
        end_doc = json.loads(body) if code == 0 else {}
        end_edit = (end_doc.get("edits") or [{}])[0]
        check("5g position: end carries no line number",
              code == 0 and end_edit.get("position") == "end"
              and "start_line" not in end_edit,
              f"exit {code}, edit keys {sorted(end_edit)}")

        out("=" * 72)
        out("BAR 7 -- ordering, verified from behaviour")
        out("=" * 72)
        two_doc = json.loads(json.dumps(document_for(project, "sample.py", "    return 41")))
        tf = plans.read_text_file(project, "sample.py")
        derived, _ = plans.declarations("sample.py", tf.text)
        beta_edit = plans.replace_body_edit(tf, derived[1], "    return 42")
        two_doc["edits"] = [two_doc["edits"][0], beta_edit]
        two_doc = plans.seal(two_doc)
        two_path = save(two_doc, plan_dir)
        out(f"# the two edits, in document order: "
            f"{[(e['start_line'], e['end_line']) for e in two_doc['edits']]}")
        out(f"# plans.ordered_edits gives start lines "
            f"{[e['start_line'] for e in plans.ordered_edits(two_doc['edits'], {'sample.py': tf})]}")
        ordering_file = "order_two.py"
        write(project, ordering_file, SAMPLE)
        ordering_doc = json.loads(json.dumps(document_for(project, ordering_file, "    return 41")))
        tf2 = plans.read_text_file(project, ordering_file)
        d2, _ = plans.declarations(ordering_file, tf2.text)
        ordering_doc["edits"] = [plans.replace_body_edit(tf2, d2[0], "    return 41"),
                                 plans.replace_body_edit(tf2, d2[1], "    return 42")]
        ordering_doc = plans.seal(ordering_doc)
        save(ordering_doc, plan_dir)
        before_text = tf2.text
        expected = before_text
        for e in sorted(ordering_doc["edits"], key=lambda e: e["start_byte"], reverse=True):
            expected = (expected[:e["start_byte"]] + e["new_text"]
                        + expected[e["end_byte"]:])
        code, body, err = run([CLI, "plan-apply", ordering_doc["plan_id"],
                               "--project", "gateproj"], env,
                              "apply a two-edit, one-file plan (throwaway project)")
        with open(os.path.join(project, ordering_file), encoding="utf-8") as fh:
            observed = fh.read()
        check("7a descending start_line inside a file: bytes match an independent "
              "descending byte splice",
              code == 0 and observed == expected,
              f"exit {code}; observed {observed!r}")

        cross_doc = json.loads(json.dumps(document_for(project, "zb_second.py", "    return 22")))
        tf_a = plans.read_text_file(project, "za_first.py")
        da, _ = plans.declarations("za_first.py", tf_a.text)
        cross_doc["files"] = [plans.file_record(tf_a, "python"),
                              plans.file_record(plans.read_text_file(project, "zb_second.py"), "python")]
        cross_doc["edits"] = [plans.replace_body_edit(tf_a, da[0], "    return 11"),
                              cross_doc["edits"][0]]
        cross_doc = plans.seal(cross_doc)
        save(cross_doc, plan_dir)
        code, body, err = run([CLI, "plan-apply", cross_doc["plan_id"],
                               "--project", "gateproj"], env,
                              "apply a two-file plan whose document order is reversed")
        order_lines = [line.strip() for line in (body or "").splitlines()
                       if line.strip().startswith("- ")]
        check("7b across files ascending path (za before zb)",
              code == 0 and order_lines and order_lines[0].startswith("- za_first.py")
              and order_lines[-1].startswith("- zb_second.py"),
              f"exit {code}; report order {order_lines}")

        adj_doc = json.loads(json.dumps(document_for(project, "adj.py", "    return 1",
                                                     operation="delete_declaration")))
        tf_adj = plans.read_text_file(project, "adj.py")
        dadj, _ = plans.declarations("adj.py", tf_adj.text)
        adj_doc["edits"] = [plans.delete_declaration_edit(tf_adj, dadj[0]),
                            plans.delete_declaration_edit(tf_adj, dadj[1])]
        adj_doc = plans.seal(adj_doc)
        code, body, err = run([CLI, "plan-validate", save(adj_doc, plan_dir),
                               "--project", "gateproj"], env,
                              "adjacent edits (lines 1-2 then 3-4) are legal")
        check("7c adjacency is legal", code == 0,
              f"exit {code}: {first_line(err)[:140]}")
    finally:
        out("=" * 72)
        out("SUMMARY")
        out("=" * 72)
        for name, verdict, detail in RESULTS:
            out(f"{verdict:4} {name}")
        out()
        failed = [name for name, verdict, _ in RESULTS if verdict == "FAIL"]
        out(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        subprocess.run([CLI, "remove-project", "--project", "gateproj"],
                       capture_output=True, text=True, env=env)
        subprocess.run([CLI, "remove-project", "--project", "printonly"],
                       capture_output=True, text=True, env=env)
        shutil.rmtree(work, ignore_errors=True)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
