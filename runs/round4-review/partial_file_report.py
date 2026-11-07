"""Does the report say `partial` when a file's SECOND edit fails?

A plan with two edits on one file; the stub keeper fails mutation 2, so the
first edit has already been written when the second is refused. The rig
builds a throwaway registered project under a temp directory; this checkout
is located from the script's own position, so no host path is written down.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tests"))

from typer.testing import CliRunner  # noqa: E402

import apply_rig  # noqa: E402
from code_indexer.cli import app  # noqa: E402

TWO_FUNCTIONS = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"

tmp = tempfile.mkdtemp(prefix="partialflag-")


class MonkeyPatch:
    """The two fixture services this rig asks for, without pytest."""

    @staticmethod
    def setenv(key, value):
        os.environ[key] = value

    @staticmethod
    def setattr(target, name, value):
        setattr(target, name, value)


rig = apply_rig.build(__import__("pathlib").Path(tmp), MonkeyPatch, {})
rig.write("mod.py", TWO_FUNCTIONS)
plan_path = apply_rig.plan_replacing(
    rig, [("mod.py", "two", "    return 22"),
          ("mod.py", "one", "    return 11")])

os.environ["STUB_KEEPER_FAIL_AT"] = "2"
result = CliRunner().invoke(
    app, ["plan-apply", plan_path, "--project", apply_rig.PROJECT_NAME,
          "--json"])
payload = json.loads(result.stdout)
print("exit_code      :", result.exit_code)
print("ErrorType      :", payload.get("ErrorType"))
print("partial        :", payload["partial"])
print("operations     :", payload["operations"])
print("file status    :", payload["files"][0]["status"])
print("file bytes now :", repr(rig.read("mod.py")))
print("stderr first   :", result.stderr.strip().splitlines()[0][:160])
