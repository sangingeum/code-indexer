"""The filekeeper boundary: one subprocess per mutation, one envelope read.

Nothing here imports filekeeper. The tool is a separate distribution with its
own release cadence, and the proposal's rule that this project must not touch
its internals is the same rule that keeps this boundary an argv list and a
JSON envelope: no shell, no positional parsing of human text, no shared
objects.

What the envelope gives the caller: ``ok``, ``operation_id``,
``undo_available``, ``before_hash``, ``after_hash``, and on failure
``error.code``, ``error.message`` and ``error.details.path``. That is all this
module reads, and it reads it by name.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from .errors import KeeperCapabilityMissingError, KeeperUnavailableError

KEEPER_COMMAND = "keeper"
ACTOR = "code-indexer"
# The floor is recorded for the install line only; the test that runs is the
# flag probe, because a build that keeps the version and drops a guard has to
# be caught (ADR-0005 section 6).
MINIMUM_VERSION = "0.1.8"
INSTALL_LINE = (
    f"install filekeeper {MINIMUM_VERSION} or newer -- "
    "`uv tool install filekeeper-cli` (or `pipx install filekeeper-cli`) -- "
    "and put `keeper` on PATH")
REQUIRED_FLAGS = ("--expected-hash", "--expected-content", "--dry-run")


def unavailable_message() -> str:
    """One actionable line for an absent keeper: no fallback is offered."""
    return ("keeper is not on PATH, and this command applies a plan through "
            "filekeeper only: " + INSTALL_LINE)


def capability_message(flag: str) -> str:
    return (f"`keeper file replace-lines --help` does not advertise {flag}, so "
            "this keeper build cannot carry the guard the plan needs: "
            + INSTALL_LINE)


@dataclass(frozen=True)
class Envelope:
    """One keeper answer, read by name from its JSON object.

    ``exit_code`` is the process's own code, kept beside the envelope because
    the mapping to this tool's codes is decided by the caller that knows
    whether a mutation had already happened.
    """

    ok: bool
    operation_id: str | None = None
    undo_available: bool = False
    before_hash: str | None = None
    after_hash: str | None = None
    predicted_after_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_path: str | None = None
    exit_code: int = 0


def _parse_envelope(stdout: str, stderr: str, exit_code: int) -> Envelope:
    """The JSON object keeper printed, or an envelope-shaped refusal.

    A malformed or absent envelope is reported as a failure rather than
    guessed at: this function never falls back to reading human text, and a
    keeper that printed no JSON gets no credit for the exit code alone.
    """
    text = stdout.strip()
    if not text:
        return Envelope(ok=False, error_code="NO_JSON_ENVELOPE",
                        error_message=("keeper printed no JSON envelope; "
                                       f"stderr: {stderr.strip()[:400]}"),
                        exit_code=exit_code)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return Envelope(ok=False, error_code="UNREADABLE_JSON_ENVELOPE",
                        error_message=(f"keeper's stdout is not one JSON "
                                       f"object: {exc}"),
                        exit_code=exit_code)
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return Envelope(ok=False, error_code="NOT_A_KEEPER_ENVELOPE",
                        error_message=("keeper's stdout holds no object "
                                       "carrying a boolean 'ok'"),
                        exit_code=exit_code)
    error = payload.get("error") or {}
    details = error.get("details") or {}
    return Envelope(
        ok=payload["ok"],
        operation_id=payload.get("operation_id"),
        undo_available=bool(payload.get("undo_available")),
        before_hash=payload.get("before_hash"),
        after_hash=payload.get("after_hash"),
        predicted_after_hash=payload.get("predicted_after_hash"),
        error_code=error.get("code"),
        error_message=error.get("message"),
        error_path=details.get("path"),
        exit_code=exit_code)


class Keeper:
    """The adapter: one workspace root, one plan id, one argv list per call."""

    def __init__(self, root: str, *, plan_id: str,
                 command: str = KEEPER_COMMAND, timeout: float = 120.0) -> None:
        self.root = root
        self.plan_id = plan_id
        self.command = command
        self.timeout = timeout

    # -- capability -----------------------------------------------------

    def probe(self) -> None:
        """Require the flags this adapter passes, read from the help text.

        By flag and not by version: a build that answers the same version and
        dropped ``--expected-hash`` from its parser has to be caught, and the
        help text is the parser's own statement of what it accepts.
        """
        try:
            proc = subprocess.run(
                [self.command, "file", "replace-lines", "--help"],
                capture_output=True, text=True, check=False,
                timeout=self.timeout)
        except FileNotFoundError as exc:
            raise KeeperUnavailableError(unavailable_message()) from exc
        except OSError as exc:
            raise KeeperUnavailableError(
                f"keeper cannot be executed ({exc}): " + INSTALL_LINE) from exc
        except subprocess.TimeoutExpired as exc:
            raise KeeperUnavailableError(
                "`keeper file replace-lines --help` did not answer: "
                + INSTALL_LINE) from exc
        text = (proc.stdout or "") + (proc.stderr or "")
        for flag in REQUIRED_FLAGS:
            if flag not in text:
                raise KeeperCapabilityMissingError(capability_message(flag))

    # -- reads ----------------------------------------------------------

    def file_hash(self, rel_path: str) -> Envelope:
        """``keeper file hash``: the same number the plan records."""
        return self._invoke(["file", "hash", "--", rel_path])

    # -- mutations ------------------------------------------------------

    def replace_lines(self, rel_path: str, *, start: int, end: int,
                      replacement: str, expected_hash: str,
                      expected_content: str | None = None,
                      dry_run: bool = False) -> Envelope:
        args = ["file", "replace-lines", f"--start={start}",
                f"--end={end}", f"--replacement={replacement}"]
        return self._mutation(args, rel_path, expected_hash,
                              expected_content, dry_run)

    def insert_lines(self, rel_path: str, *, content: str,
                     line: int | None = None, position: str = "before",
                     expected_hash: str,
                     expected_content: str | None = None,
                     dry_run: bool = False) -> Envelope:
        args = ["file", "insert-lines"]
        if position == "end":
            args.append("--position=end")
        else:
            args.extend([f"--line={line}", f"--position={position}"])
        args.append(f"--content={content}")
        return self._mutation(args, rel_path, expected_hash,
                              expected_content, dry_run)

    def delete_lines(self, rel_path: str, *, start: int, end: int,
                     expected_hash: str,
                     expected_content: str | None = None,
                     dry_run: bool = False) -> Envelope:
        args = ["file", "delete-lines", f"--start={start}", f"--end={end}"]
        return self._mutation(args, rel_path, expected_hash,
                              expected_content, dry_run)

    def undo(self, operation_id: str) -> Envelope:
        """Undo one recorded operation. A refusal is reported, never retried."""
        return self._invoke(["history", "undo", operation_id])

    # -- plumbing -------------------------------------------------------

    def _mutation(self, args: list[str], rel_path: str, expected_hash: str,
                  expected_content: str | None,
                  dry_run: bool) -> Envelope:
        args = list(args)
        args.append(f"--expected-hash={expected_hash}")
        if expected_content is not None:
            args.append(f"--expected-content={expected_content}")
        if dry_run:
            args.append("--dry-run")
        args.extend(["--", rel_path])
        return self._invoke(args)

    def _invoke(self, args: list[str]) -> Envelope:
        argv = [self.command, "--root", self.root, "--json",
                "--actor", ACTOR, "--task-id", self.plan_id, *args]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  check=False, timeout=self.timeout)
        except FileNotFoundError as exc:
            raise KeeperUnavailableError(unavailable_message()) from exc
        except OSError as exc:
            raise KeeperUnavailableError(
                f"keeper cannot be executed ({exc}): " + INSTALL_LINE) from exc
        except subprocess.TimeoutExpired:
            return Envelope(ok=False, error_code="KEEPER_TIMEOUT",
                            error_message=(f"keeper did not answer within "
                                           f"{self.timeout:g}s"),
                            exit_code=1)
        return _parse_envelope(proc.stdout or "", proc.stderr or "",
                               proc.returncode)
