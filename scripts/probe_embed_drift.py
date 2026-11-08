"""One reproducible measurement of the embedding service's repeat drift.

The property the cache exists to absorb: the service can return a DIFFERENT
vector for the SAME text on an immediate repeat. This script measures exactly
that on the host where it runs and prints only what it observed -- the model
name, the embed format and the numbers -- so the output carries no paths and
no hostnames and can be published as evidence.

Protocol (also stated in ``--help``):

* unload the model first (``keep_alive="0"``), so the first embedding of the
  first trial is a load-first call;
* never read or write the embedding cache (the embedder is built with
  ``cache=None``), because this measures the SERVICE, not a stored row;
* per trial and per form, in this order: embed the text once (load-first),
  embed it AGAIN IMMEDIATELY (the repeat), embed a DIFFERENT text, then embed
  the first text again (the repeat after a different request);
* the instructed form is built by ``Embedder.query_instruction_text``, the
  function the query path applies before ``embed_query_once``, and the embed
  format is the ``EMBED_FORMAT`` constant imported from the package -- neither
  is retyped here.

Usage: uv run python scripts/probe_embed_drift.py --form both --trials 6
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys

from code_indexer.config import load_config
from code_indexer.embed_text import EMBED_FORMAT
from code_indexer.embedder import Embedder

DEFAULT_TEXT = "refresh the token for the next request"
DEFAULT_OTHER = "summarise the build log of the previous run"
FORMS = ("bare", "instructed")


def vector_digest(vector: list[float]) -> str:
    """A short digest of the exact values: same bytes, same digest."""
    packed = struct.pack(f"<{len(vector)}f", *vector)
    return hashlib.sha256(packed).hexdigest()[:16]


def largest_delta(first: list[float], second: list[float]) -> float:
    """The largest per-component change between two vectors."""
    if len(first) != len(second):
        raise SystemExit(
            f"the service answered {len(first)} and {len(second)} dimensions "
            "for the same text")
    return max(abs(a - b) for a, b in zip(first, second))


def form_text(embedder: Embedder, form: str, text: str) -> str:
    """The text this form sends: bare as given, instructed as the query path."""
    if form == "instructed":
        return embedder.query_instruction_text(text)
    return text


def probe_form(embedder: Embedder, form: str, text: str, other: str,
               trials: int) -> list[float]:
    """Run the fixed request sequence once per trial; return the deltas."""
    payload = form_text(embedder, form, text)
    other_payload = form_text(embedder, form, other)
    deltas: list[float] = []
    differing = 0
    returned = 0
    for trial in range(1, trials + 1):
        first = embedder.embed_query_once(payload)
        repeat = embedder.embed_query_once(payload)
        embedder.embed_query_once(other_payload)
        again = embedder.embed_query_once(payload)
        delta = largest_delta(first, repeat)
        came_back = vector_digest(again) == vector_digest(first)
        deltas.append(delta)
        differing += 1 if delta != 0.0 else 0
        returned += 1 if came_back else 0
        print(f"trial {trial} form={form}")
        print(f"  immediate repeat: largest per-component delta = {delta:.3e}"
              f" (differs={'yes' if delta != 0.0 else 'no'})")
        print(f"  digests: first={vector_digest(first)} "
              f"repeat={vector_digest(repeat)}")
        print("  repeat after a different request: "
              f"first form returned = {'yes' if came_back else 'no'}")
    low = min(deltas) if deltas else 0.0
    high = max(deltas) if deltas else 0.0
    print(f"summary {form}: trials={trials} immediate repeats differing="
          f"{differing} first-form returns={returned} "
          f"delta range={low:.3e}..{high:.3e}")
    return deltas


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_embed_drift.py",
        description=(
            "Measure how the embedding service answers the same text twice. "
            "Protocol: (1) unload the model first with keep_alive='0', so the "
            "first call of trial 1 is load-first; (2) read and write NO "
            "embedding cache -- this measures the service, not the cache; "
            "(3) per trial and per form embed the text once, embed it again "
            "IMMEDIATELY, embed a DIFFERENT text, then embed the first text "
            "again; (4) the instructed form is built by "
            "Embedder.query_instruction_text and the embed format is the "
            "EMBED_FORMAT constant, both imported rather than retyped. "
            "Output: per trial the immediate repeat's largest per-component "
            "delta, the two digests and whether the repeat after a different "
            "request returned the first form; then a summary per form (how "
            "many immediate repeats differed, how many first-form returns, "
            "the delta range) and the model name and embed format used. "
            "Nothing host-specific is printed."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--form", choices=(*FORMS, "both"), default="both",
                        help="Which text form to measure (default: both).")
    parser.add_argument("--trials", type=int, default=6,
                        help="Trials per form (default: 6).")
    parser.add_argument("--text", default=DEFAULT_TEXT,
                        help="The text whose repeat is measured.")
    parser.add_argument("--other", default=DEFAULT_OTHER,
                        help="The different text sent between the two repeats.")
    parser.add_argument("--model", default=None,
                        help="Embedding model (default: the configured one).")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="Seconds per embedding request (default: 60).")
    args = parser.parse_args(argv)
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config([])
    model = args.model or cfg.embed_model
    embedder = Embedder(host=cfg.ollama_url, model=model, timeout=args.timeout,
                        cache=None)
    # Unload first: an embed request carrying keep_alive="0" releases the
    # model when it completes, so the next request is a load-first call.
    embedder.client.embed(model=model, input=["unload"], keep_alive="0")
    print(f"model: {model}")
    print(f"embed format: {EMBED_FORMAT}")
    print("protocol: unload, then per trial embed, immediate repeat, different "
          "text, first text again; no cache")
    forms = FORMS if args.form == "both" else (args.form,)
    for form in forms:
        print()
        probe_form(embedder, form, args.text, args.other, args.trials)
    return 0


if __name__ == "__main__":
    sys.exit(main())
