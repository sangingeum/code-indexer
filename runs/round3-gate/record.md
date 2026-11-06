# Gate record — structural query on a stable identity

Verification of the round that landed at `3d93972` (round base `84fd194`):
filters on `find-symbol`, the evidence label, the ambiguity envelope, and the
graph union over candidates.

Status: in progress — captures are being written under this directory.

## Bars

| bar | what it asks | command | verdict |
|---|---|---|---|
| B1 | suite green, warn-clean, ruff clean, hygiene zero | `uv run pytest tests/ -q -W error` | pending |
| B2 | each filter answers alone and composed | `..b2-*.txt` | pending |
| B3 | `--name-pattern` is a glob, index-backed | `..b3-query-plan.txt` | pending |
| B4 | evidence on every candidate; `unknown` never a negative | `..b4-evidence.txt` | pending |
| B5 | ambiguity envelope and the single-symbol refusal | `..b5-ambiguity.txt` | pending |
| B6 | graph commands union over candidates | `..b6-graph.txt` | pending |
| B7 | a capped answer says so | `..b7-truncated.txt` | pending |
| B8 | nothing else moved | `..b8-*.txt` | pending |
| B9 | docs match the shipped surface | `..b9-docs.txt` | pending |

## Verdict

pending
