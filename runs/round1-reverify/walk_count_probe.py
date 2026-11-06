# Re-verification probe (not part of the suite): how many staleness walks does a
# single-project `get-code-context` text answer perform?
import importlib.util
import pathlib

import pytest
from typer.testing import CliRunner

from code_indexer import cli

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "read_contract_module", _HERE.parents[1] / "tests" / "test_read_contract.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

rig = pytest.fixture(_module.rig.__wrapped__)


def test_count_walks(rig):
    core, entry, project, _, _ = rig
    seen = []
    real = core.staleness

    def counting(target):
        seen.append(target.path)
        return real(target)

    core.staleness = counting
    result = CliRunner().invoke(
        cli.app, ["get-code-context", "a.py", "--project", str(project),
                  "--start-line", "1", "--end-line", "5"])
    print("EXIT=" + str(result.exit_code))
    print("WALKS=" + str(len(seen)))
    print("STDERR=" + repr(result.stderr))
    assert result.exit_code == 0, result.output
