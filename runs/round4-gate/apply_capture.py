"""Gate capture for round 4, the apply path (bars 8-14).

Every apply happens in a throwaway project under a temporary directory and
against the real keeper 0.1.8. A keeper shim on PATH logs every argv the
adapter builds and can inject one deterministic failure at the documented
subprocess boundary; it is labelled as fault injection wherever it is used.
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

from code_indexer import apply as apply_mod  # noqa: E402
from code_indexer import keeper as keeper_mod  # noqa: E402
from code_indexer import plans  # noqa: E402
from code_indexer import ts_chunker  # noqa: E402

CLI = os.path.join(ROOT, ".venv", "bin", "code-indexer")
REAL_KEEPER = shutil.which("keeper")
MASKS: list[tuple[str, str]] = []
RESULTS: list[tuple[str, str, str]] = []

SHIM = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("KEEPER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\\n")
fail_path = os.environ.get("KEEPER_FAIL_PATH")
if fail_path and args and args[-1] == fail_path and "--help" not in args:
    sys.stdout.write(json.dumps({"ok": False, "error": {"code": "IO_ERROR",
        "message": "injected failure (gate fault injection)",
        "details": {"path": fail_path}}}) + "\\n")
    sys.exit(1)
real = os.environ["KEEPER_REAL"]
os.execv(real, [real] + args)
'''

BAD_SHIM = '''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if "--help" in args:
    sys.stdout.write("Usage: keeper file replace-lines [OPTIONS]\\n"
                     "  --start INTEGER\\n  --end INTEGER\\n"
                     "  --replacement TEXT\\n  --dry-run\\n")
    sys.exit(0)
sys.stdout.write('{"ok": true, "operation_id": "op_stub", '
                 '"undo_available": true, "before_hash": "sha256:0", '
                 '"after_hash": "sha256:1"}')
sys.exit(0)
'''


SAMPLE = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"

def mask(text: str) -> str:
    for real, token in MASKS:
        text = text.replace(real, token)
    return text.replace(os.path.expanduser("~"), "<home>")


def out(text: str = "") -> None:
    print(mask(text))


def check(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    out(f"CHECK {name}: {'PASS' if ok else 'FAIL'} -- {detail}")


def run(argv, env, label=""):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    out(f"$ {' '.join(argv)}")
    if label:
        out(f"# {label}")
    out(((proc.stdout or "") + (proc.stderr or "")).rstrip() or "(no output)")
    out(f"exit {proc.returncode}")
    out()
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return sha_bytes(handle.read())
    except FileNotFoundError:
        return "MISSING"


def write(project: str, name: str, payload) -> str:
    path = os.path.join(project, name)
    mode = "wb" if isinstance(payload, bytes) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8",
                                                     "newline": ""})) as fh:
        fh.write(payload)
    return path


def journal(env, project: str, plan_id: str, label: str):
    code, body, err = run([REAL_KEEPER, "--root", project, "--json",
                           "--actor", "code-indexer", "history", "list",
                           "--task-id", plan_id], env, label)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return code, None
    return code, payload


def entries_of(payload):
    if not isinstance(payload, dict):
        return []
    for key in ("entries", "operations", "data", "history", "items"):
        if isinstance(payload.get(key), list):
            return payload[key]
    if "ok" in payload and len(payload) == 1:
        return []
    if "results" in payload and isinstance(payload["results"], list):
        return payload["results"]
    return []


def doc_for(root: str, records: list[tuple[str, int, str]], operation="replace_body"):
    files = {}
    edits = []
    target = None
    for rel, index, text in records:
        tf = plans.read_text_file(root, rel)
        derived, _v = plans.declarations(rel, tf.text)
        decl = derived[index]
        if rel not in files:
            files[rel] = plans.file_record(tf, "python")
        if operation == "delete_declaration":
            edits.append(plans.delete_declaration_edit(tf, decl))
        else:
            edits.append(plans.replace_body_edit(tf, decl, text))
        if target is None:
            target = plans.locator(tf, decl, "python")
    return plans.build_document(
        operation=operation,
        repository={"project": "gateapply", "index_revision": "gate"},
        files=[files[key] for key in sorted(files)], target=target, edits=edits,
        created_at="2026-11-07T00:00:00Z")


FIVE = {name: f"def one():\n    return {index}\n"
        for index, name in enumerate(
            ["a_one.py", "b_two.py", "c_three.py", "d_four.py", "e_five.py"], 1)}


def main() -> int:
    work = tempfile.mkdtemp(prefix="r4gate-apply-")
    project = os.path.join(work, "project")
    os.makedirs(project)
    shim_dir = os.path.join(work, "shim")
    bad_dir = os.path.join(work, "badshim")
    os.makedirs(shim_dir)
    os.makedirs(bad_dir)
    log_path = os.path.join(work, "keeper-calls.jsonl")
    for directory, body in ((shim_dir, SHIM), (bad_dir, BAD_SHIM)):
        target = os.path.join(directory, "keeper")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(target, 0o755)
    MASKS.extend([(project, "<project-root>"), (shim_dir, "<shim-dir>"),
                  (bad_dir, "<bad-shim-dir>"),
                  (os.path.join(work, "index"), "<index-root>"),
                  (work, "<work-dir>"), (ROOT, "<repo-root>")])
    env = dict(os.environ)
    env["INDEX_ROOT"] = os.path.join(work, "index")
    env["FILEKEEPER_HOME"] = os.path.join(work, "keeper-home")
    env["KEEPER_REAL"] = REAL_KEEPER
    env["KEEPER_LOG"] = log_path
    shim_env = dict(env, PATH=shim_dir + os.pathsep + env["PATH"])
    plan_dir = os.path.join(project, ".code-indexer", "plans")

    for name, text in FIVE.items():
        write(project, name, text)
    write(project, "pair.py", SAMPLE)
    write(project, "broken.py", "def one():\n    return 1\n")
    write(project, "notes.txt", "def one():\n    return 1\n")

    try:
        run([CLI, "add-project", project, "--name", "gateapply", "--vectors", "off"],
            env, "register the throwaway project")

        out("=" * 72)
        out("BAR 8 -- a changed file refuses the WHOLE plan: zero mutations")
        out("=" * 72)
        stale = doc_for(project, [("a_one.py", 0, "    return 11"),
                                  ("b_two.py", 0, "    return 22")])
        plans.write_plan(stale, plan_dir)
        a_before, b_before = sha(os.path.join(project, "a_one.py")), sha(os.path.join(project, "b_two.py"))
        write(project, "a_one.py", FIVE["a_one.py"].replace("1", "9"))
        a_changed = sha(os.path.join(project, "a_one.py"))
        code, body, err = run([CLI, "plan-apply", stale["plan_id"], "--project",
                               "gateapply"], env,
                              "file a_one.py changed after the plan was prepared")
        code_j, payload = journal(env, project, stale["plan_id"],
                                  "keeper journal for this plan id")
        count = len(entries_of(payload))
        check("8a exit 5 and PlanStale",
              code == 5 and "PlanStale" in (err + body),
              f"exit {code}: {(err or body).splitlines()[0][:140]}")
        check("8b the changed file kept its bytes",
              sha(os.path.join(project, "a_one.py")) == a_changed,
              "byte-identical")
        check("8c the later file in the same plan was never attempted",
              sha(os.path.join(project, "b_two.py")) == b_before,
              f"b_two.py unchanged ({b_before[:12]})")
        check("8d keeper journal count for the plan id is zero -- no mutation "
              "of any kind", count == 0, f"entries={count}, payload keys "
              f"{sorted(payload) if isinstance(payload, dict) else payload}")

        out("=" * 72)
        out("BAR 9 -- one keeper mutation per edit, through the hash guard")
        out("=" * 72)
        multi = doc_for(project, [("pair.py", 0, "    return 41"),
                                  ("pair.py", 1, "    return 42")])
        multi["files"].append(plans.file_record(
            plans.read_text_file(project, "d_four.py"), "python"))
        tf4 = plans.read_text_file(project, "d_four.py")
        d4, _ = plans.declarations("d_four.py", tf4.text)
        multi["edits"].append(plans.replace_body_edit(tf4, d4[0], "    return 44"))
        multi = plans.seal(multi)
        plans.write_plan(multi, plan_dir)
        original_pair = sha(os.path.join(project, "pair.py"))
        original_four = sha(os.path.join(project, "d_four.py"))
        if os.path.exists(log_path):
            os.remove(log_path)
        code, body, err = run([CLI, "plan-apply", multi["plan_id"],
                               "--project", "gateapply", "--json"], shim_env,
                              "three edits over two files, keeper calls logged")
        try:
            report = json.loads(body)
        except json.JSONDecodeError:
            report = {}
        out("# the report, as JSON")
        out(json.dumps(report, indent=2)[:4000])
        out()
        files = report.get("files", [])
        check("9a report carries plan_id, identity.verified/basis, partial",
              report.get("plan_id") == multi["plan_id"]
              and report.get("identity", {}).get("verified") == "structural"
              and report.get("identity", {}).get("basis")
              == ["file_hash", "decl_hash", "region_hash"]
              and report.get("partial") is False,
              f"verified={report.get('identity', {}).get('verified')} basis="
              f"{report.get('identity', {}).get('basis')} partial={report.get('partial')}")
        statuses = [(item["path"], item["status"]) for item in files]
        check("9b every file reports applied",
              statuses == [("d_four.py", "applied"), ("pair.py", "applied")],
              str(statuses))
        edit_records = [edit for item in files for edit in item["edits"]]
        check("9c per edit the range and an operation_id",
              len(edit_records) == 3
              and all(edit.get("operation_id") for edit in edit_records)
              and all("start_line" in edit and "end_line" in edit
                      for edit in edit_records),
              str([(e["start_line"], e["end_line"], e["operation_id"])
                   for e in edit_records]))
        calls = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as handle:
                calls = [json.loads(line) for line in handle if line.strip()]
        mutations = [call for call in calls if "--help" not in call]
        out(f"# keeper argv calls logged: {len(calls)} ({len(mutations)} not a probe)")
        for call in calls:
            out("  keeper " + " ".join(call))
        out()
        check("9d exactly one mutation call per edit", len(mutations) == 3,
              f"{len(mutations)} mutation calls for 3 edits")
        check("9e every mutation carries --expected-hash",
              all(any(arg.startswith("--expected-hash=") for arg in call)
                  for call in mutations),
              str([a for call in mutations for a in call
                   if a.startswith("--expected-hash=")]))
        content_flags = [any(arg.startswith("--expected-content=")
                             for arg in call) for call in mutations]
        check("9f --expected-content travels where the plan carries the region",
              all(content_flags),
              f"per-call presence {content_flags}")

        out("=" * 72)
        out("BAR 13 -- the journal reconstructs the run; the receipt exists")
        out("=" * 72)
        code_j, payload = journal(env, project, multi["plan_id"],
                                  "keeper's own journal, by plan id, no receipt")
        entries = entries_of(payload)
        actors = sorted({entry.get("actor") for entry in entries
                         if isinstance(entry, dict)})
        check("13a journal lists one entry per edit under the plan id",
              len(entries) == 3, f"{len(entries)} entries")
        check("13b every entry is actor code-indexer",
              actors == ["code-indexer"], f"actors={actors}")
        check("13c every keeper call carried --task-id <plan_id> and --actor "
              "code-indexer",
              all("--task-id" in call and multi["plan_id"] in call
                  and "--actor" in call and "code-indexer" in call
                  for call in mutations),
              f"{len(mutations)} mutation calls checked (the probe is not a "
              "mutation)")
        receipt = report.get("receipt_path")
        receipt_exists = bool(receipt) and os.path.isfile(receipt)
        receipt_payload = {}
        if receipt_exists:
            with open(receipt, encoding="utf-8") as handle:
                receipt_payload = json.load(handle)
        check("13d the printed receipt path exists and matches the report",
              receipt_exists and receipt_payload.get("plan_id") == report.get("plan_id")
              and receipt_payload.get("files") == report.get("files"),
              f"{mask(receipt or 'none')} exists={receipt_exists}")

        out("=" * 72)
        out("BAR 11 -- recovery: the ordered list, reverse undo, a refused undo")
        out("=" * 72)
        operations = report.get("operations", [])
        out(f"# operations in application order: {operations}")
        out(f"# undo commands in the report: {report.get('undo')}")
        check("11a the undo list is the operations list reversed",
              report.get("undo") == [f"keeper history undo {op}"
                                     for op in reversed(operations)],
              str(report.get("undo")))
        for operation in reversed(operations):
            run([REAL_KEEPER, "--root", project, "--json", "--actor",
                 "code-indexer", "history", "undo", operation], env,
                f"undo {operation}")
        check("11b undo in reverse restores the tree byte-for-byte",
              sha(os.path.join(project, "pair.py")) == original_pair
              and sha(os.path.join(project, "d_four.py")) == original_four,
              "pair.py and d_four.py are byte-identical to before the run")

        lone = doc_for(project, [("e_five.py", 0, "    return 55")])
        plans.write_plan(lone, plan_dir)
        code, body, err = run([CLI, "plan-apply", lone["plan_id"], "--project",
                               "gateapply", "--json"], shim_env, "apply again")
        lone_report = json.loads(body)
        lone_ops = lone_report.get("operations", [])
        write(project, "e_five.py", FIVE["e_five.py"] + "# touched afterwards\n")
        adapter = keeper_mod.Keeper(project, plan_id=lone["plan_id"],
                                    command=REAL_KEEPER)
        recovery = apply_mod.recover(lone_ops, adapter)
        out(f"# apply.recover({lone_ops}) -> stopped={recovery.stopped} "
            f"steps={[(s.operation_id, s.ok, s.error_code) for s in recovery.steps]}")
        check("11c a refused undo (path changed) is reported, never retried",
              recovery.stopped and len(recovery.steps) == 1
              and recovery.failure is not None and not recovery.failure.ok,
              f"stopped={recovery.stopped} steps={len(recovery.steps)} "
              f"code={recovery.failure.error_code if recovery.failure else None}")

        out("=" * 72)
        out("BAR 12 -- keeper absent, and a keeper missing the guard flag")
        out("=" * 72)
        absent = doc_for(project, [("b_two.py", 0, "    return 222")])
        plans.write_plan(absent, plan_dir)
        before = sha(os.path.join(project, "b_two.py"))
        bare = dict(env, PATH="/usr/bin:/bin")
        code, body, err = run([CLI, "plan-apply", absent["plan_id"],
                               "--project", "gateapply"], bare,
                              "PATH=/usr/bin:/bin: no keeper anywhere")
        check("12a exit 3 KeeperUnavailable naming 0.1.8",
              code == 3 and "KeeperUnavailable" in (err + body)
              and "0.1.8" in (err + body),
              f"exit {code}: {(err or body).splitlines()[0][:200]}")
        check("12b no fallback write: the file is unchanged",
              sha(os.path.join(project, "b_two.py")) == before,
              "byte-identical")
        bad_env = dict(env, PATH=bad_dir + os.pathsep + env["PATH"])
        code, body, err = run([CLI, "plan-apply", absent["plan_id"],
                               "--project", "gateapply"], bad_env,
                              "a keeper whose replace-lines --help lacks "
                              "--expected-hash")
        check("12c exit 3 KeeperCapabilityMissing naming the flag",
              code == 3 and "KeeperCapabilityMissing" in (err + body)
              and "--expected-hash" in (err + body),
              f"exit {code}: {(err or body).splitlines()[0][:200]}")
        check("12d the capability probe wrote nothing",
              sha(os.path.join(project, "b_two.py")) == before,
              "byte-identical")

        out("=" * 72)
        out("BAR 10 -- partial failure at file 3 of 5")
        out("=" * 72)
        five = doc_for(project, [(name, 0, f"    return {index}0")
                                 for index, name in enumerate(sorted(FIVE), 1)])
        plans.write_plan(five, plan_dir)
        five_before = {name: sha(os.path.join(project, name)) for name in FIVE}
        fail_env = dict(shim_env, KEEPER_FAIL_PATH="c_three.py")
        code, body, err = run([CLI, "plan-apply", five["plan_id"], "--project",
                               "gateapply", "--json"], fail_env,
                              "keeper shim injects one failure at the third file "
                              "(gate fault injection)")
        try:
            partial = json.loads(body)
        except json.JSONDecodeError:
            partial = {}
        statuses = [(item["path"], item["status"])
                    for item in partial.get("files", [])]
        check("10a exit 1 and ErrorType PartialApply",
              code == 1 and partial.get("ErrorType") == "PartialApply",
              f"exit {code} ErrorType={partial.get('ErrorType')}")
        check("10b the report is on stdout (JSON, not a bare error)",
              bool(partial) and partial.get("partial") is True,
              f"partial={partial.get('partial')}, keys {sorted(partial)[:8]}")
        check("10c per-file verdict: 1,2 applied; 3 failed; 4,5 not_attempted",
              statuses == [("a_one.py", "applied"), ("b_two.py", "applied"),
                           ("c_three.py", "failed"),
                           ("d_four.py", "not_attempted"),
                           ("e_five.py", "not_attempted")],
              str(statuses))
        check("10d the bytes agree: 1,2 written; 3,4,5 untouched",
              sha(os.path.join(project, "a_one.py")) != five_before["a_one.py"]
              and sha(os.path.join(project, "b_two.py")) != five_before["b_two.py"]
              and all(sha(os.path.join(project, name)) == five_before[name]
                      for name in ("c_three.py", "d_four.py", "e_five.py")),
              "a,b changed; c,d,e byte-identical")
        check("10e the failure names the keeper code and the failed edit",
              any(edit.get("error_code") == "IO_ERROR"
                  for item in partial.get("files", []) if item["path"] == "c_three.py"
                  for edit in item["edits"]),
              str([(e.get("kind"), e.get("error_code"))
                   for item in partial.get("files", []) if item["path"] == "c_three.py"
                   for e in item["edits"]]))

        out("=" * 72)
        out("BAR 14 -- the parse verdict is the parser's; --refresh is explicit")
        out("=" * 72)
        broken = doc_for(project, [("broken.py", 0, "    return (")])
        plans.write_plan(broken, plan_dir)
        code, body, err = run([CLI, "plan-apply", broken["plan_id"],
                               "--project", "gateapply"], env,
                              "a replacement that leaves the file unparsable")
        check("14a a broken result reports parse_error with the offending lines",
              "parse_error" in body and "line(s)" in body,
              [line for line in body.splitlines()
               if "broken.py" in line][:1])
        check("14b a good result reports parsed (the pair.py run above)",
              any(item.get("verdict") == "parsed"
                  for item in report.get("post_apply", [])),
              str([(i["path"], i["verdict"]) for i in report.get("post_apply", [])]))
        tree, verdict = ts_chunker.parse_tree("notes.txt",
                                              "def one():\n    return 1\n")
        check("14c a file with no grammar answers parse_unavailable (parser "
              "level)", tree is None and verdict == "parse_unavailable",
              f"tree={tree} verdict={verdict}")
        code, body, err = run([CLI, "plan-replace-body", "one", "--project",
                               "gateapply", "--file", "notes.txt", "--text",
                               "    return 2"], env,
                              "is parse_unavailable reachable through plan-apply?")
        check("14d no plan can be prepared for a grammarless file, so "
              "parse_unavailable is unreachable through apply", code != 0,
              f"exit {code}: {(err or body).splitlines()[0][:160]}")
        check("14e the reparse writes nothing by default (report says skipped)",
              report.get("refresh") == "skipped",
              f"refresh={report.get('refresh')}")
        refresh_doc = doc_for(project, [("e_five.py", 0, "    return 555")])
        plans.write_plan(refresh_doc, plan_dir)
        code, body, err = run([CLI, "plan-apply", refresh_doc["plan_id"],
                               "--project", "gateapply", "--refresh"], env,
                              "--refresh: one incremental pass, and it says so")
        check("14f a passed --refresh is reported as ran",
              code == 0 and "refresh: ran" in body,
              [line for line in body.splitlines() if line.startswith("refresh")])
    finally:
        out("=" * 72)
        out("SUMMARY")
        out("=" * 72)
        for name, verdict, detail in RESULTS:
            out(f"{verdict:4} {name}")
        out()
        failed = [name for name, verdict, _ in RESULTS if verdict == "FAIL"]
        out(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        subprocess.run([CLI, "remove-project", "--project", "gateapply"],
                       capture_output=True, text=True, env=env)
        shutil.rmtree(work, ignore_errors=True)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
