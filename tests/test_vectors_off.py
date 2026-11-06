"""A pass that needs no service: vectors off, both services unreachable.

The extension is installed here, but no service is reachable: the addresses
point at a closed local port. The pass must therefore construct neither
client, write a complete manifest, record ``vectors=off`` and exit 0.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from typer.testing import CliRunner  # noqa: E402

import code_indexer.cli as cli  # noqa: E402
from code_indexer.cli import app  # noqa: E402
from code_indexer.manifest import Manifest  # noqa: E402

runner = CliRunner()

UNROUTABLE = "http://127.0.0.1:9"


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "a.py").write_text(
        "def alpha():\n    return 1\n\n\nclass Beta:\n"
        "    def go(self):\n        return alpha()\n", encoding="utf-8")
    (project / "pkg" / "b.py").write_text(
        "import alpha\n\ndef other():\n    return alpha()\n", encoding="utf-8")
    return project


def test_add_project_writes_a_complete_manifest_with_no_service(
        tmp_path, monkeypatch):
    project = _project(tmp_path)
    root = tmp_path / "state"
    monkeypatch.setenv("INDEX_ROOT", str(root))
    monkeypatch.setenv("OLLAMA_URL", UNROUTABLE)
    monkeypatch.setenv("QDRANT_URL", UNROUTABLE)
    monkeypatch.setattr(cli, "_core", None)

    result = runner.invoke(app, ["add-project", str(project)])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert "vectors=off" in result.output

    (manifest_path,) = root.glob("*/manifest.db")
    manifest = Manifest(str(manifest_path))
    try:
        files = manifest.all_files()
        assert len(files) == 2
        assert manifest.count_chunks() > 0
        assert manifest.fts().rowcount() == manifest.count_chunks()
        assert manifest.get_meta("vectors") == "off"
        assert manifest.get_meta("vectors_state") == "absent"
        assert manifest.get_meta("rows") == "current"
        assert manifest.get_meta("extraction_version") is not None
        assert manifest.get_meta("index_schema_version") == "7"
        assert manifest.get_meta("last_indexed") is not None
        assert manifest.get_meta("embedding_version") is None
    finally:
        manifest.close()


def test_index_status_reports_rows_current_vectors_off(tmp_path, monkeypatch):
    project = _project(tmp_path)
    root = tmp_path / "state"
    monkeypatch.setenv("INDEX_ROOT", str(root))
    monkeypatch.setenv("OLLAMA_URL", UNROUTABLE)
    monkeypatch.setenv("QDRANT_URL", UNROUTABLE)
    monkeypatch.setattr(cli, "_core", None)
    assert runner.invoke(app, ["add-project", str(project)]).exit_code == 0

    result = runner.invoke(app, ["index-status", str(project), "--json"])
    assert result.exit_code == 0, (result.output, result.stderr)
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["schema"] == 1
    assert payload["rows"] == "current"
    assert payload["vectors"] == "off"
    assert payload["vectors_state"] == "absent"
    assert payload["parsed"] == 2
