"""eval-compare names the arm on each side and refuses a cross-arm delta.

The round that taught ``eval`` to report both shapings left the comparison
surface behind: its delta table printed ``a``/``b`` with no arm anywhere, so
the table could still be published without the shaping it measured, and it
compared a merged report against an unmerged one -- a rule change reading as
a metric delta -- with a clean exit 0. It refused a two-arm document in the
right direction but the wrong class: ``InternalError``, exit 1, the
tool-fault class, where a valid document the command cannot accept is a
usage error (ADR-0003 section 5).

These tests pin the property, not the wording: the header names both arms, a
same-arm pair keeps the metric table it always printed, and both refusals are
ArgumentError / exit 2 at the CLI.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from typer.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from code_indexer import evalharness  # noqa: E402
from code_indexer.cli import app  # noqa: E402


def _report(arm: str | None, ranks: dict[str, int | None]) -> dict:
    report = {
        "project": "/proj",
        "mode": "dense",
        "queries": len(ranks),
        "metrics": {"recall@1": sum(r == 1 for r in ranks.values())
                     / len(ranks),
                     "mrr": 0.5},
        "results": [{"id": qid, "rank": r, "latency_s": 0.1,
                     "output_chars": 100, "approx_tokens": 25}
                    for qid, r in sorted(ranks.items())],
    }
    if arm is not None:
        report["arm"] = arm
    return report


# ---------------------------------------------------------------------------
# the delta table carries its arm
# ---------------------------------------------------------------------------

def test_the_delta_table_header_names_each_sides_arm():
    """A published delta names the shaping on both sides of it."""
    a = _report("merged", {"q1": 2})
    b = _report("merged", {"q1": 1})
    text = evalharness.compare_reports(a, b)
    assert text.splitlines()[0] == "a (merged) vs b (merged)"


def test_a_same_arm_comparison_keeps_the_metric_table_text():
    """The metric table's geometry is unchanged; only a header line is added.

    The review round's bar: a same-arm delta must read exactly as it always
    did below the arm line, so this pins the old two header lines and a data
    row's column widths rather than trusting the diff read.
    """
    a = _report("unmerged", {"q1": 3})
    b = _report("unmerged", {"q1": 1})
    lines = evalharness.compare_reports(a, b).splitlines()
    assert lines[0] == "a (unmerged) vs b (unmerged)"
    assert lines[1] == "metric                    a          b         delta"
    assert lines[2] == "-" * 52
    assert lines[3] == f"{'recall@1':<24} {0.0:>9.4f} {1.0:>9.4f} {1.0:>+10.4f}"
    assert lines[4] == f"{'mrr':<24} {0.5:>9.4f} {0.5:>9.4f} {0.0:>+10.4f}"
    assert lines[-3] == "improved (1): q1"


def test_a_report_without_an_arm_is_labelled_unrecorded_not_guessed():
    """A pre-arm report cannot be claimed as either arm; the header says so."""
    legacy = _report(None, {"q1": 1})
    text = evalharness.compare_reports(legacy, legacy)
    assert text.splitlines()[0] == \
        "a (arm unrecorded) vs b (arm unrecorded)"


# ---------------------------------------------------------------------------
# the refusals (unit)
# ---------------------------------------------------------------------------

def test_a_cross_arm_comparison_is_refused():
    """merged vs unmerged differs by a whole pass: not a metric delta."""
    a = _report("merged", {"q1": 1})
    b = _report("unmerged", {"q1": 2})
    with pytest.raises(ValueError) as excinfo:
        evalharness.compare_reports(a, b)
    message = str(excinfo.value)
    assert "merged" in message and "unmerged" in message
    assert "same arm" in message


def test_a_two_arm_report_is_refused():
    """A document that already holds both arms is not a comparison input."""
    both = {"project": "/proj", "mode": "dense", "queries": 1,
            "arm": "both",
            "arms": {"merged": _report("merged", {"q1": 1}),
                     "unmerged": _report("unmerged", {"q1": 2})}}
    with pytest.raises(ValueError, match="single-arm reports"):
        evalharness.compare_reports(both, _report("merged", {"q1": 1}))


# ---------------------------------------------------------------------------
# the refusals at the CLI boundary: usage class, exit 2
# ---------------------------------------------------------------------------

def _write(tmp_path, name: str, report: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return str(path)


def test_the_cli_refuses_a_cross_arm_pair_as_a_usage_error(tmp_path):
    """Exit 2 (argument error), not exit 1 (the tool-fault class)."""
    a = _write(tmp_path, "merged.json", _report("merged", {"q1": 1}))
    b = _write(tmp_path, "unmerged.json", _report("unmerged", {"q1": 1}))
    result = CliRunner().invoke(app, ["eval-compare", a, b])
    assert result.exit_code == 2, result.output
    assert "ArgumentError" in result.output
    assert "merged" in result.output and "unmerged" in result.output


def test_the_cli_refuses_a_two_arm_report_as_a_usage_error(tmp_path):
    """The pre-existing refusal keeps its aim and gains the usage class."""
    both = {"project": "/proj", "mode": "dense", "queries": 1,
            "arm": "both",
            "arms": {"merged": _report("merged", {"q1": 1}),
                     "unmerged": _report("unmerged", {"q1": 1})}}
    a = _write(tmp_path, "both.json", both)
    b = _write(tmp_path, "merged.json", _report("merged", {"q1": 1}))
    result = CliRunner().invoke(app, ["eval-compare", a, b])
    assert result.exit_code == 2, result.output
    assert "ArgumentError" in result.output
    assert "single-arm" in result.output


def test_the_cli_still_compares_two_reports_of_one_arm(tmp_path):
    """The happy path survives: exit 0, table printed, arm in the header."""
    a = _write(tmp_path, "a.json", _report("unmerged", {"q1": 3, "q2": None}))
    b = _write(tmp_path, "b.json", _report("unmerged", {"q1": 1, "q2": 1}))
    result = CliRunner().invoke(app, ["eval-compare", a, b])
    assert result.exit_code == 0, result.output
    assert "a (unmerged) vs b (unmerged)" in result.output
    assert "improved (2): q1, q2" in result.output
