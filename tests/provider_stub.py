"""A refactoring provider that answers from fixture files -- no server.

The contract suite drives this stub instead of a language server, so the
interface is proved on a machine with none installed. It answers every
operation from ``tests/fixtures/provider/answers.json`` in provider-native
coordinates (0-based lines, UTF-16 code units); the adapter does every
conversion, which is the point of driving it through ``ProviderSession``.
"""

from __future__ import annotations

import json
import os
import time

from code_indexer.errors import ProviderRefusalError
from code_indexer.refactoring_provider import (
    CapabilityReport,
    ProviderCandidate,
    ProviderEdit,
    ProviderIdentity,
    ProviderLocation,
    ProviderPosition,
    ProviderRange,
    ProviderSymbolQuery,
)

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "provider")


def load_answers(fixture_dir: str = FIXTURE_DIR) -> dict:
    with open(os.path.join(fixture_dir, "answers.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _position(record: dict) -> ProviderPosition:
    return ProviderPosition(record["line"], record["character"])


def _range(record: dict) -> ProviderRange:
    return ProviderRange(_position(record["start"]), _position(record["end"]))


class FixtureProvider:
    """The stub: fixture declarations, references and rename edits."""

    def __init__(self, project_root: str, *, fixture_dir: str = FIXTURE_DIR,
                 capabilities: dict[str, str] | None = None,
                 answers: dict | None = None) -> None:
        self.project_root = project_root
        self.answers = answers if answers is not None else load_answers(fixture_dir)
        self._capabilities = (capabilities if capabilities is not None
                              else self.answers.get("capabilities", {}))
        self.starts = 0
        self.closed = False
        self.cancelled = False
        self.calls: list[str] = []

    # -- lifecycle hooks the session calls when it finds them ---------------

    def start(self) -> None:
        self.starts += 1

    def close(self) -> None:
        self.closed = True

    def cancel(self) -> None:
        self.cancelled = True

    # -- the contract -------------------------------------------------------

    def capabilities(self):
        self.calls.append("capabilities")
        identity = ProviderIdentity(**self.answers["provider"])
        return identity, dict(self._capabilities)

    def resolve_symbol(self, query: ProviderSymbolQuery):
        self.calls.append("resolve_symbol")
        records = self.answers.get("declarations", {})
        if query.qualified_name is not None:
            found = records.get(query.qualified_name, [])
        else:
            assert query.position is not None and query.path is not None
            found = []
            for name_records in records.values():
                for record in name_records:
                    if _contains(record, query.path, query.position):
                        found.append(record)
        return tuple(
            ProviderCandidate(path=record["path"], range=_range(record),
                              qualified_name=record["qualified_name"],
                              symbol_type=record["symbol_type"])
            for record in found)

    def find_references(self, declaration: ProviderCandidate):
        self.calls.append("find_references")
        refusals = self.answers.get("refusals", {})
        if declaration.qualified_name in refusals:
            raise ProviderRefusalError(refusals[declaration.qualified_name])
        records = self.answers.get("references", {}).get(
            declaration.qualified_name, [])
        return tuple(ProviderLocation(path=record["path"], range=_range(record))
                     for record in records)

    def prepare_rename(self, declaration: ProviderCandidate, new_name: str):
        self.calls.append("prepare_rename")
        records = self.answers.get("rename_edits", {}).get(
            declaration.qualified_name, [])
        return tuple(
            ProviderEdit(path=record["path"],
                         start=_position(record["start"]),
                         end=_position(record["end"]),
                         new_text=new_name)
            for record in records)


def _contains(record: dict, path: str, position: ProviderPosition) -> bool:
    if record["path"] != path:
        return False
    start = _position(record["start"])
    end = _position(record["end"])
    return ((start.line, start.character)
            <= (position.line, position.character)
            <= (end.line, end.character))


class SilentAnswerProvider(FixtureProvider):
    """A provider that is ready but never answers a semantic request in time."""

    def resolve_symbol(self, query):
        time.sleep(0.5)
        return super().resolve_symbol(query)


class SlowStartProvider(FixtureProvider):
    """A provider that never becomes ready inside its start budget."""

    def start(self) -> None:
        time.sleep(0.5)
        super().start()


class OnceSlowProvider(FixtureProvider):
    """Slow on the first semantic call, instant on every call after it.

    Models a wedged server that recovers: the call that overran a deadline is
    the last one waited on, and anything asked afterwards answers at once --
    on this object or on the one built to replace it. ``slow_left`` is shared
    across instances on purpose, so a rebuilt provider counts as recovered.
    """

    def __init__(self, *args, slow_left: list[int] | None = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slow_left = slow_left if slow_left is not None else [1]

    def resolve_symbol(self, query):
        if self.slow_left[0] > 0:
            self.slow_left[0] -= 1
            time.sleep(0.5)
        return super().resolve_symbol(query)


def capability_report(fixture_dir: str = FIXTURE_DIR) -> dict:
    """The capability report a caller reads -- rename is the accepted risk."""
    provider = FixtureProvider(".", fixture_dir=fixture_dir)
    identity, operations = provider.capabilities()
    return CapabilityReport(identity, operations).block()
