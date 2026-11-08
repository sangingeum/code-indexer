"""One reproducible measurement of the embedding service's load-boundary drift.

The property the cache exists to absorb: when a model is LOADED, the first
embedding of a text differs from an immediate repeat of that same text. Once
the model is warm the repeat returns the same bytes. This script measures
exactly that on the host where it runs and prints only what it observed -- the
model name, the embed format and the numbers -- so the output carries no paths
and no hostnames and can be published as evidence.

Protocol (also stated in ``--help``):

* the model is unloaded before every trial by default (``--reload
  each-trial``), so every trial is a load-boundary trial; ``--reload once``
  unloads only at the start of a form's run, so trials after the first
  measure a WARM model;
* never read or write the embedding cache (the embedder is built with
  ``cache=None``), because this measures the SERVICE, not a stored row;
* per trial and per form, in this order: embed the text once (load-first when
  the trial crosses a boundary), embed it AGAIN IMMEDIATELY (the repeat),
  embed a DIFFERENT text, then embed the first text again (the repeat after a
  different request); the third call is counted against the RUN's load-first
  digest, so a warm trial is measured against the load-first form and not
  against its own first call;
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
RELOAD_MODES = ("each-trial", "once")


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


def unload(embedder: Embedder) -> None:
    """Release the model: an embed carrying keep_alive="0" ends the load."""
    embedder.client.embed(model=embedder.model, input=["unload"],
                          keep_alive="0")


def probe_form(embedder: Embedder, form: str, text: str, other: str,
               trials: int, reload: str) -> dict[str, float | int]:
    """Run the fixed request sequence once per trial; return its summary."""
    payload = form_text(embedder, form, text)
    other_payload = form_text(embedder, form, other)
    deltas: list[float] = []
    warm_deltas: list[float] = []
    differing = 0
    warm_differing = 0
    returned = 0
    kept = 0
    boundaries = 0
    # The digest of the first call after a load in THIS RUN. Every trial
    # compares its third call against it, so a warm trial's third call is
    # measured against the run's load-first form, not against the trial's
    # own (warm) first call.
    load_first_digest: str | None = None
    if reload == "once":
        # One unload for this form's run: trial 1 crosses the load boundary
        # and every later trial measures the warm model.
        unload(embedder)
    for trial in range(1, trials + 1):
        boundary = reload == "each-trial" or trial == 1
        if reload == "each-trial":
            unload(embedder)
        boundaries += 1 if boundary else 0
        first = embedder.embed_query_once(payload)
        repeat = embedder.embed_query_once(payload)
        embedder.embed_query_once(other_payload)
        again = embedder.embed_query_once(payload)
        first_digest = vector_digest(first)
        repeat_digest = vector_digest(repeat)
        again_digest = vector_digest(again)
        if boundary and load_first_digest is None:
            load_first_digest = first_digest
        delta = largest_delta(first, repeat)
        came_back = (load_first_digest is not None
                     and again_digest == load_first_digest)
        kept_repeat = again_digest == repeat_digest
        deltas.append(delta)
        differing += 1 if delta != 0.0 else 0
        returned += 1 if came_back else 0
        kept += 1 if kept_repeat else 0
        if not boundary:
            # The model was already loaded when this trial's first call ran:
            # an immediate repeat here is a WARM repeat.
            warm_deltas.append(delta)
            warm_differing += 1 if delta != 0.0 else 0
        print(f"trial {trial} form={form} reload={reload} "
              f"load-boundary={'yes' if boundary else 'no'}")
        print(f"  immediate repeat: largest per-component delta = {delta:.3e}"
              f" (differs={'yes' if delta != 0.0 else 'no'})")
        print(f"  digests: first={first_digest} repeat={repeat_digest} "
              f"again={again_digest}")
        print("  repeat after a different request: "
              f"load-first form returned = {'yes' if came_back else 'no'}; "
              f"repeat form kept = {'yes' if kept_repeat else 'no'}")
    low = min(deltas) if deltas else 0.0
    high = max(deltas) if deltas else 0.0
    warm = len(warm_deltas)
    summary: dict[str, float | int] = {
        "trials": trials,
        "load boundaries": boundaries,
        "warm trials": warm,
        "immediate repeats differing": differing,
        "warm repeats differing": warm_differing,
        "load-first-form returns": returned,
        "repeat-form kept": kept,
        "low": low,
        "high": high,
    }
    print(f"summary reload={reload} form={form} trials={trials} "
          f"load-boundaries={boundaries} warm-trials={warm} "
          f"immediate-repeats-differing={differing} "
          f"warm-repeats-differing={warm_differing} "
          f"load-first-form-returns={returned} repeat-form-kept={kept} "
          f"delta-range={low:.3e}..{high:.3e}")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_embed_drift.py",
        description=(
            "Measure how the embedding service answers the same text twice, "
            "on either side of a model load. Protocol: (1) the model is "
            "UNLOADED before every trial by default (--reload each-trial), "
            "so every trial's first call is a load-first call; --reload once "
            "unloads only at the start of a form's run, so trials after the "
            "first measure a WARM model; (2) read and write NO embedding "
            "cache -- this measures the service, not the cache; (3) per trial "
            "and per form embed the text once, embed it again IMMEDIATELY, "
            "embed a DIFFERENT text, then embed the first text again; (4) the "
            "instructed form is built by Embedder.query_instruction_text and "
            "the embed format is the EMBED_FORMAT constant, both imported "
            "rather than retyped. Output: per trial the reload mode, whether "
            "the trial crossed a load boundary, the immediate repeat's "
            "largest per-component delta, the digests and whether the repeat "
            "after a different request returned the form of the first call "
            "after the load (the run's load-first digest) or kept the "
            "repeat form; then one summary per mode and per form (how many "
            "trials crossed a load boundary, how many were warm, how many "
            "immediate repeats differed, how many of those were warm repeats, "
            "how many load-first-form returns, the delta range) and the model "
            "name and embed format used. Nothing host-specific is printed."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Note: --reload once measures a WARM model after the first "
                "trial of each form's run; the default --reload each-trial "
                "measures the load boundary on every trial."),
    )
    parser.add_argument("--form", choices=(*FORMS, "both"), default="both",
                        help="Which text form to measure (default: both).")
    parser.add_argument("--trials", type=int, default=6,
                        help="Trials per form and mode (default: 6).")
    parser.add_argument(
        "--reload", choices=(*RELOAD_MODES, "both"), default="each-trial",
        help=("When to unload the model: 'each-trial' unloads before every "
              "trial, so every trial is a load-boundary trial (default); "
              "'once' unloads only at the start of a form's run, so the "
              "later trials measure a warm model; 'both' runs both modes and "
              "reports each separately."))
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
    print(f"model: {model}")
    print(f"embed format: {EMBED_FORMAT}")
    print("protocol: reload per --reload; per trial embed, immediate repeat, "
          "different text, first text again; no cache")
    modes = RELOAD_MODES if args.reload == "both" else (args.reload,)
    forms = FORMS if args.form == "both" else (args.form,)
    for reload in modes:
        for form in forms:
            print()
            probe_form(embedder, form, args.text, args.other, args.trials,
                       reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
