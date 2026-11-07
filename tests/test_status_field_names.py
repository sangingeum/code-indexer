"""One field name, one meaning -- in one JSON document.

``index-status --json`` carried ``languages_without_ast`` twice: at the top
level it was the registry's parse-unavailable language list (14 entries on a
C# project), and inside ``coverage`` it was the in-scope file languages that
yield no symbol reader (``[json, markdown, shell]``). A reader could not tell
which list governed the answer.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.langmatrix import languages_without_ast  # noqa: E402
from pass_rig import index_project, stub_core  # noqa: E402

runner = CliRunner()


@pytest.fixture()
def payload(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("def greet(name):\n    return name\n")
    (project / "README.md").write_text("# Title\n\nsome prose\n")
    (project / "data.json").write_text('{"a": 1}\n')
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    result = runner.invoke(app, ["index-status", str(entry.path), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_the_shared_key_is_gone_from_both_levels(payload):
    assert "languages_without_ast" not in payload
    assert "languages_without_ast" not in payload["coverage"]


def test_the_registry_list_names_the_registry_meaning(payload):
    assert payload["registry_languages_without_ast"] == languages_without_ast()
    assert "markdown" in payload["registry_languages_without_ast"]


def test_the_answer_list_names_the_answer_meaning(payload):
    answer = payload["coverage"]["answer_languages_without_ast"]
    assert answer == sorted({"markdown", "json"})
    # the two lists are different questions, and the payload says so
    assert answer != payload["registry_languages_without_ast"]


def test_the_text_line_uses_the_registry_spelling(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "app.py").write_text("def greet(name):\n    return name\n")
    core = stub_core(tmp_path)
    reg, entry = index_project(core, project)
    monkeypatch.setattr(cli, "_get_core", lambda skip: core)
    out = runner.invoke(app, ["index-status", str(entry.path)]).output
    assert "registry_languages_without_ast=[" in out
