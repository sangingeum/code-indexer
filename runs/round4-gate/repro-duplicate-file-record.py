"""Reproducer: a plan with two `files[]` records for one path.

A plan names each touched file once, and every command this round ships builds
it that way. A document that names the same path twice is accepted by
``plan-validate`` and applied correctly, but the report lists the path twice
and marks one of the two records ``not_attempted`` -- a status the report's own
vocabulary reserves for a file no mutation was attempted on.

Throwaway project; nothing here touches the checkout.
"""

from __future__ import annotations

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
SAMPLE = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
MASKS: list[tuple[str, str]] = []


def mask(text: str) -> str:
    for real, token in MASKS:
        text = text.replace(real, token)
    return text.replace(os.path.expanduser("~"), "<home>")


def run(argv, env):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    print(mask(f"$ {' '.join(argv)}"))
    print(mask(((proc.stdout or "") + (proc.stderr or "")).rstrip()))
    print(f"exit {proc.returncode}\n")
    return proc


def main() -> int:
    work = tempfile.mkdtemp(prefix="r4gate-dupe-")
    project = os.path.join(work, "project")
    os.makedirs(project)
    MASKS.extend([(project, "<project-root>"),
                  (os.path.join(work, "index"), "<index-root>"),
                  (work, "<work-dir>"), (ROOT, "<repo-root>")])
    env = dict(os.environ)
    env["INDEX_ROOT"] = os.path.join(work, "index")
    env["FILEKEEPER_HOME"] = os.path.join(work, "keeper-home")
    with open(os.path.join(project, "sample.py"), "w", encoding="utf-8") as fh:
        fh.write(SAMPLE)
    try:
        run([CLI, "add-project", project, "--name", "dupeproj", "--vectors",
             "off"], env)
        tf = plans.read_text_file(project, "sample.py")
        derived, _verdict = plans.declarations("sample.py", tf.text)
        document = plans.build_document(
            operation="replace_body",
            repository={"project": "dupeproj", "index_revision": "gate"},
            files=[plans.file_record(tf, "python"),
                   plans.file_record(tf, "python")],
            target=plans.locator(tf, derived[0], "python"),
            edits=[plans.replace_body_edit(tf, derived[0], "    return 41")],
            created_at="2026-11-07T00:00:00Z")
        path = plans.write_plan(document, os.path.join(
            project, ".code-indexer", "plans"))
        print(f"# the plan names sample.py twice: "
              f"{[record['path'] for record in document['files']]}\n")
        run([CLI, "plan-validate", path, "--project", "dupeproj"], env)
        proc = run([CLI, "plan-apply", document["plan_id"], "--project",
                    "dupeproj", "--json"], env)
        report = json.loads(proc.stdout)
        print("# report files[]:")
        print(json.dumps(report["files"], indent=2))
        print("# partial:", report["partial"])
        silent = [item for item in report["files"]
                  if item["status"] == "not_attempted"]
        print(f"# records reported not_attempted while the file WAS written: "
              f"{len(silent)}")
        return 0 if silent else 1
    finally:
        subprocess.run([CLI, "remove-project", "--project", "dupeproj"],
                       capture_output=True, text=True, env=env)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
