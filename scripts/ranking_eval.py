"""Ranking-mode evaluation harness (offline measurement, not shipped as a tool).

Runs the same intents through core.search_one in all three ranking modes and
reports the rank of the designated ground-truth chunk per intent.

Usage: uv run python scripts/ranking_eval.py
"""

from __future__ import annotations

import time

from code_indexer.core import Core

# (project, query, ground-truth matcher: file suffix + symbol substring)
# Ground truth verified against find-symbol/skeleton on the live indexes.
INTENTS = [
    ("vivarium-sim", "pathfinding movement of pawns on the map",
     "TravelerPolicy.cs", "Neighbor"),
    ("vivarium-sim", "pawn suspicion decay witness level",
     "WitnessSuspicion.cs", "Decay"),
    ("vivarium-sim", "species vocabulary mentions as token boundary scan",
     "SpeciesDefinition.cs", "MentionsAsToken"),
    ("vivarium-sim", "pawn cognition profile record",
     "SpeciesDefinition.cs", "CognitionProfile"),
    ("vivarium-sim", "culture policy enum values",
     "SpeciesDefinition.cs", "CulturePolicy"),
    ("calc-engine", "convert a value from one unit to another unit conversion",
     "unit_ops.py", "convert"),
    ("calc-engine", "statistical moments summary of samples",
     None, "stats"),
    ("calc-engine", "mortgage monthly payment amortization finance",
     "finance_ops.py", "finance"),
]


def find_rank(hits: list[dict], file_part: str, sym_part: str | None) -> int | None:
    for i, h in enumerate(hits, 1):
        fmatch = file_part is None or file_part in (h.get("file") or "")
        if fmatch and (sym_part is None or sym_part in (h.get("symbol") or "")):
            return i
    return None


def main() -> None:
    core = Core(skip_stale_check=True)
    for mode in ("vector", "metadata", "hybrid"):
        ranks: list[int | None] = []
        t0 = time.time()
        for proj, query, fpart, spart in INTENTS:
            hits = core.search(query, project=proj, limit=30,
                               ranking_mode=mode, skip_refresh=True)
            ranks.append(find_rank(hits, fpart, spart))
        dt = (time.time() - t0) / len(INTENTS)
        top5 = sum(1 for r in ranks if r is not None and r <= 5)
        top10 = sum(1 for r in ranks if r is not None and r <= 10)
        top1 = sum(1 for r in ranks if r == 1)
        print(f"mode={mode:8s} top1={top1}/{len(INTENTS)} "
              f"top5={top5}/{len(INTENTS)} top10={top10}/{len(INTENTS)} "
              f"miss={sum(1 for r in ranks if r is None)} "
              f"avg_ms={dt * 1000:.0f}")
        print(f"  ranks: {ranks}")


if __name__ == "__main__":
    main()
