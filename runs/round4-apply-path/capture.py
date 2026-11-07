"""The acceptance lines for the apply leg, run against the real keeper.

Every run happens in a throwaway project under a temporary directory, never in
this checkout. The output is masked as it is printed so the capture can be
published: the project root, the index root and the account home become
<project-root>, <index-root> and <home>.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MASK_PATHS: list[tuple[str, str]] = []
CLI = shutil.which("code-indexer") or "code-indexer"


def mask(text: str) -> str:
    for real, token in MASK_PATHS:
        text = text.replace(real, token)
    return text.replace(os.path.expanduser("~"), "<home>")


def run(argv, env, label):
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    print(f"$ {mask(' '.join(argv))}")
    if label:
        print(f"# {label}")
    out = mask((proc.stdout or "") + (proc.stderr or "")).rstrip()
    print(out if out else "(no output)")
    print(f"exit {proc.returncode}")
    print()
    return proc.returncode, proc.stdout


SAMPLE = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"


def main() -> int:
    work = tempfile.mkdtemp(prefix="apply-capture-")
    project = os.path.join(work, "project")
    home = os.path.join(work, "keeper-home")
    os.makedirs(project)
    MASK_PATHS.append((project, "<project-root>"))
    MASK_PATHS.append((os.path.join(work, "index"), "<index-root>"))
    MASK_PATHS.append((work, "<work-dir>"))
    MASK_PATHS.append((ROOT, "<repo-root>"))
    with open(os.path.join(project, "sample.py"), "w", encoding="utf-8") as fh:
        fh.write(SAMPLE)
    env = dict(os.environ)
    env["INDEX_ROOT"] = os.path.join(work, "index")
    env["FILEKEEPER_HOME"] = home
    try:
        run([CLI, "add-project", project, "--name", "captureproj"],
            env, "register the throwaway project")
        code, stdout = run([
            CLI, "plan-replace-body", "alpha",
            "--project", "captureproj", "--file", "sample.py",
            "--text", "    return 41"], env, "prepare a plan")
        plan_path = [line.split(" ", 1)[1] for line in stdout.splitlines()
                     if line.startswith("path ")][0]
        document = json.load(open(plan_path, encoding="utf-8"))
        plan_id = document["plan_id"]

        run([CLI, "plan-apply", plan_id, "--project",
             "captureproj"], env, "apply it")
        with open(os.path.join(project, "sample.py"), encoding="utf-8") as fh:
            body = fh.read()
        print("# sample.py after the apply")
        print(body, end="")
        print()

        run(["keeper", "--root", project, "--json", "--actor",
             "code-indexer", "history", "list", "--task-id", plan_id],
            env, "keeper's own journal finds the application by plan id")

        # A changed file: exit 5, and the file keeps the bytes it had.
        with open(os.path.join(project, "sample.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(SAMPLE.replace("return 1", "return 7"))
        before = open(os.path.join(project, "sample.py"), "rb").read()
        run([CLI, "plan-apply", plan_id, "--project",
             "captureproj"], env, "reapply against a changed file")
        after = open(os.path.join(project, "sample.py"), "rb").read()
        print(f"# the file is byte-identical after the refusal: "
              f"{before == after}")
        print()
        run(["keeper", "--root", project, "--json", "--actor",
             "code-indexer", "history", "list", "--task-id", plan_id],
            env, "still one operation: the refusal recorded none")

        # keeper absent from PATH. The console script is addressed by its own
        # absolute path, because /usr/bin does not hold it; what the run
        # proves is that no keeper on PATH is a refusal, never a fallback.
        bare = dict(env, PATH="/usr/bin:/bin")
        with open(os.path.join(project, "sample.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(SAMPLE)
        run([CLI, "plan-apply", plan_id, "--project", "captureproj"], bare,
            "PATH=/usr/bin:/bin: no keeper, no fallback path")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
