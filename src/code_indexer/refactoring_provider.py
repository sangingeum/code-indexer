"""The refactoring provider interface (ADR-0006 sections 1, 3 to 7).

Five operations. Three are semantic questions a provider answers, one asks
the provider what it offers, and one is the adapter's own normalization:

``capabilities``     per operation ``supported`` / ``unsupported`` /
                     ``unknown``, plus the provider's name, version and
                     transport
``resolve_symbol``   path + position, or a qualified name -> candidate
                     declarations with ranges, or a typed ambiguity
``find_references``  one declaration -> locations, or a typed refusal
``prepare_rename``   one declaration + a new name -> provider edits in
                     UTF-16 positions, before normalization
``materialize``      provider edits -> line-aligned candidate plan edits

The interface is proved before any server exists: a stub in the test tree
answers from fixture files and passes ``tests/test_provider_contract.py``,
which is the registration test. A provider written but not passed is not
registered and cannot be selected by configuration.

Capability honesty (section 3)
------------------------------
Every answer is per ``(provider, operation)``. ``unknown`` is not a denial:
the request is attempted and the provider's own answer decides.
``unsupported`` is refused with ``UnsupportedOperation`` (exit 2) before the
provider is asked, and is never answered by a weaker local approximation.
An absent provider, one that never becomes ready, or one that times out is
``ProviderUnavailable`` / ``ProviderTimeout`` (exit 3) naming the install
route. Nothing here infers availability from the language.

Lifecycle (section 4)
---------------------
Lazy start: constructing a session spawns nothing. One provider per project
per invocation, reused across that invocation's requests, terminated at
close (``atexit`` covers process exit; there is no daemon). Every request
carries a deadline; an expired request is cancelled and reported as a
timeout, never as an empty semantic answer. Every semantic answer carries
``provider: {name, version, transport}``. Nothing here writes the index: a
provider identifier never reaches a symbol row.

Positions (section 5)
---------------------
Providers speak 0-based lines and UTF-16 code units; the plan speaks 1-based
lines and bytes. ``provider_positions`` is the only conversion site and this
module uses it on the way in and on the way out, so nothing downstream of
this module sees a UTF-16 position. The single exception ADR-0006 names is
``prepare_rename``'s output, which is documented as provider edits *before*
normalization and is passed straight to ``materialize``.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from . import plans
from .errors import (
    AmbiguousSymbolError,
    ArgumentError,
    CarriageReturnInPlanError,
    InternalError,
    NonLineAlignedProviderEditError,
    OverlappingEditsError,
    PositionConversionFailedError,
    ProviderFailureError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedOperationError,
)
from .provider_positions import offset_to_position, position_to_offset

logger = logging.getLogger("code-indexer.provider")

# Capability states. ``unknown`` is a third value on purpose: a provider that
# did not answer a capability question has not denied the operation.
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNKNOWN = "unknown"
CAPABILITY_STATES = (SUPPORTED, UNSUPPORTED, UNKNOWN)

# The semantic operations the interface delegates to a provider.
SEMANTIC_OPERATIONS = ("resolve_symbol", "find_references", "prepare_rename")
MATERIALIZE = "materialize"

# The plan edit kind ``materialize`` writes. It is the operation ADR-0004
# section 1 reserves: the candidate body is produced and validated here, while
# the plan format still refuses to seal a document carrying it.
PROVIDER_EDIT_KIND = "rename_symbol"

DEFAULT_START_BUDGET = 10.0
DEFAULT_REQUEST_DEADLINE = 15.0

_INSTALL_ROUTES = {
    "python": "uv tool install python-lsp-server   (or: uv tool install pyright)",
    "csharp": "dotnet tool install -g roslyn-language-server",
}


# ---------------------------------------------------------------------------
# typed values
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderIdentity:
    """The provenance every semantic answer carries."""

    name: str
    version: str
    transport: str

    def block(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version,
                "transport": self.transport}


@dataclass(frozen=True)
class CapabilityReport:
    """What one provider offers, per operation, as it answered."""

    provider: ProviderIdentity
    operations: Mapping[str, str]

    def __post_init__(self) -> None:
        for operation, state in self.operations.items():
            if state not in CAPABILITY_STATES:
                raise InternalError(
                    f"provider {self.provider.name!r} answered capability "
                    f"{operation!r} with {state!r}, which is not one of "
                    + ", ".join(CAPABILITY_STATES))

    def state(self, operation: str) -> str:
        return self.operations.get(operation, UNKNOWN)

    def supports(self, operation: str) -> bool:
        return self.state(operation) == SUPPORTED

    def block(self) -> dict[str, Any]:
        return {"provider": self.provider.block(),
                "operations": {name: self.state(name)
                               for name in SEMANTIC_OPERATIONS}}


@dataclass(frozen=True)
class ProviderPosition:
    """A provider-native position: 0-based line, UTF-16 code units."""

    line: int
    character: int


@dataclass(frozen=True)
class ProviderRange:
    start: ProviderPosition
    end: ProviderPosition


@dataclass(frozen=True)
class ProviderSymbolQuery:
    """A resolve request in provider coordinates."""

    path: str | None = None
    position: ProviderPosition | None = None
    qualified_name: str | None = None


@dataclass(frozen=True)
class ProviderCandidate:
    """A declaration as a provider reports it, before conversion."""

    path: str
    range: ProviderRange
    qualified_name: str
    symbol_type: str


@dataclass(frozen=True)
class ProviderLocation:
    path: str
    range: ProviderRange


@dataclass(frozen=True)
class ProviderEdit:
    """One provider edit, in UTF-16 positions, before normalization."""

    path: str
    start: ProviderPosition
    end: ProviderPosition
    new_text: str


@dataclass(frozen=True)
class SourceRange:
    """A range in the plan's coordinates: 1-based lines, byte offsets."""

    start_line: int
    end_line: int
    start_byte: int
    end_byte: int

    def block(self) -> dict[str, int]:
        return {"start_line": self.start_line, "end_line": self.end_line,
                "start_byte": self.start_byte, "end_byte": self.end_byte}


@dataclass(frozen=True)
class DeclarationRef:
    """A declaration the core can use: no UTF-16 position anywhere."""

    path: str
    qualified_name: str
    symbol_type: str
    range: SourceRange

    def block(self) -> dict[str, Any]:
        return {"path": self.path, "qualified_name": self.qualified_name,
                "symbol_type": self.symbol_type, "range": self.range.block()}


@dataclass(frozen=True)
class Location:
    path: str
    range: SourceRange

    def block(self) -> dict[str, Any]:
        return {"path": self.path, "range": self.range.block()}


@dataclass(frozen=True)
class SymbolQuery:
    """A resolve request in the plan's coordinates.

    Either a position (``path`` + 1-based ``line`` + byte ``character``) or a
    ``qualified_name``; ``path`` may narrow a name query to one file.
    """

    path: str | None = None
    line: int | None = None
    character: int | None = None
    qualified_name: str | None = None

    def __post_init__(self) -> None:
        positioned = (self.path is not None and self.line is not None
                      and self.character is not None)
        named = self.qualified_name is not None
        if positioned and named:
            raise ArgumentError(
                "a symbol query is either a position or a qualified name, "
                "never both")
        if not positioned and not named:
            raise ArgumentError(
                "a symbol query needs a path with a 1-based line and a byte "
                "character, or a qualified name")
        if positioned:
            if self.line < 1:
                raise ArgumentError("a symbol query line is 1-based")
            if self.character < 0:
                raise ArgumentError("a symbol query character is a byte offset")

    def label(self) -> str:
        if self.qualified_name is not None:
            return (f"{self.path}:{self.qualified_name}" if self.path
                    else self.qualified_name)
        return f"{self.path}:{self.line}:{self.character}"


@dataclass(frozen=True)
class Resolution:
    provider: ProviderIdentity
    candidates: tuple[DeclarationRef, ...]

    def block(self) -> dict[str, Any]:
        return {"provider": self.provider.block(),
                "candidates": [item.block() for item in self.candidates]}


@dataclass(frozen=True)
class References:
    provider: ProviderIdentity
    declaration: DeclarationRef
    locations: tuple[Location, ...]

    def block(self) -> dict[str, Any]:
        return {"provider": self.provider.block(),
                "declaration": self.declaration.block(),
                "locations": [item.block() for item in self.locations]}


@dataclass(frozen=True)
class RenameEdits:
    provider: ProviderIdentity
    edits: tuple[ProviderEdit, ...]

    def block(self) -> dict[str, Any]:
        return {
            "provider": self.provider.block(),
            "edits": [{"path": edit.path,
                       "start": {"line": edit.start.line,
                                 "character": edit.start.character},
                       "end": {"line": edit.end.line,
                               "character": edit.end.character},
                       "new_text": edit.new_text}
                      for edit in self.edits],
        }


# ---------------------------------------------------------------------------
# the provider side of the contract
# ---------------------------------------------------------------------------

@runtime_checkable
class RefactoringProvider(Protocol):
    """What a provider implements. Optional hooks: start, cancel, close."""

    def capabilities(self) -> tuple[ProviderIdentity, Mapping[str, str]]: ...

    def resolve_symbol(
        self, query: ProviderSymbolQuery,
    ) -> Sequence[ProviderCandidate]: ...

    def find_references(
        self, declaration: ProviderCandidate,
    ) -> Sequence[ProviderLocation]: ...

    def prepare_rename(
        self, declaration: ProviderCandidate, new_name: str,
    ) -> Sequence[ProviderEdit]: ...


ProviderFactory = Callable[[str], RefactoringProvider | None]

# Providers earn a place here by passing the contract suite; the registry is
# empty until one does, so the default answer to a semantic request is the
# honest "nothing is installed" line rather than a guess.
_REGISTERED: dict[str, ProviderFactory] = {}


def register_provider(language: str, factory: ProviderFactory) -> None:
    """Register a provider for a language after its contract run passed."""
    _REGISTERED[language] = factory


def unregister_provider(language: str) -> None:
    _REGISTERED.pop(language, None)


def registered_languages() -> tuple[str, ...]:
    return tuple(sorted(_REGISTERED))


def unavailable_message(language: str) -> str:
    """One line naming the install route, for the exit-3 answer."""
    route = _INSTALL_ROUTES.get(
        language, "see the provider strategy record for the install route")
    return (f"no refactoring provider is installed or selected for {language}: "
            "a provider is registered only after the contract suite passes "
            f"against it. Install route: {route}")


class ProviderSession:
    """One project's provider, started lazily and reused until close."""

    def __init__(self, project_root: str, *, language: str = "python",
                 factory: ProviderFactory | None = None,
                 start_budget: float = DEFAULT_START_BUDGET,
                 request_deadline: float = DEFAULT_REQUEST_DEADLINE) -> None:
        self.project_root = os.path.abspath(project_root)
        self.language = language
        self.start_budget = start_budget
        self.request_deadline = request_deadline
        self.starts = 0
        self._factory = factory
        self._instance: RefactoringProvider | None = None
        self._capabilities: CapabilityReport | None = None
        self._files: dict[str, plans.TextFile] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> ProviderSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Terminate the provider; no process outlives the invocation."""
        if self._closed:
            return
        self._closed = True
        provider, self._instance = self._instance, None
        if provider is not None:
            closer = getattr(provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001 - a failed close is logged
                    logger.warning("provider %s failed to close",
                                   self._provider_name(), exc_info=True)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _provider(self):
        if self._closed:
            raise ProviderUnavailableError(
                f"the {self.language} provider session is closed; a new "
                "invocation needs a new session")
        if self._instance is not None:
            return self._instance
        factory = self._factory or _REGISTERED.get(self.language)
        if factory is None:
            raise ProviderUnavailableError(unavailable_message(self.language))
        try:
            instance = factory(self.project_root)
        except (ProviderUnavailableError, ProviderTimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001 - a broken factory is unavailable
            raise ProviderUnavailableError(
                f"the {self.language} provider could not be created: {exc}. "
                f"Install route: {_INSTALL_ROUTES.get(self.language, 'see the provider record')}"
            ) from exc
        if instance is None:
            raise ProviderUnavailableError(unavailable_message(self.language))
        starter = getattr(instance, "start", None)
        if callable(starter):
            self._run(instance, "start", starter, budget=self.start_budget,
                      what="become ready")
        self.starts += 1
        self._instance = instance
        return instance

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="refactoring-provider")
        return self._executor

    def _run(self, provider, operation: str, function, *args: Any,
             budget: float | None = None, what: str | None = None) -> Any:
        """Call the provider under a deadline; an expiry is a cancel + timeout."""
        deadline = self.request_deadline if budget is None else budget
        future = self._pool().submit(function, *args)
        try:
            return future.result(timeout=deadline)
        except FutureTimeout:
            self._cancel(provider)
            future.cancel()
            verb = what or f"answer {operation!r}"
            raise ProviderTimeoutError(
                f"the {self._provider_name()} provider did not {verb} within "
                f"{deadline:g}s: the request was cancelled ($/cancelRequest) "
                "and is reported as a timeout, never as an empty answer"
            ) from None

    def _cancel(self, provider) -> None:
        cancel = getattr(provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # noqa: BLE001 - the timeout is the answer
                logger.warning("provider cancel failed", exc_info=True)

    def _provider_name(self) -> str:
        if self._capabilities is not None:
            return self._capabilities.provider.name
        return f"{self.language} refactoring"

    # -- capabilities ------------------------------------------------------

    def capabilities(self) -> CapabilityReport:
        if self._capabilities is None:
            provider = self._provider()
            answer = self._run(provider, "capabilities", provider.capabilities)
            if answer is None:
                raise ProviderFailureError(
                    f"the {self._provider_name()} provider answered no "
                    "capabilities; a result with no provenance is a defect")
            identity, operations = answer
            if not isinstance(identity, ProviderIdentity):
                raise ProviderFailureError(
                    "a provider capability answer must carry a ProviderIdentity")
            self._capabilities = CapabilityReport(identity, dict(operations))
        return self._capabilities

    def _require(self, operation: str) -> CapabilityReport:
        """Refuse an unsupported operation; ``unknown`` is attempted."""
        report = self.capabilities()
        state = report.state(operation)
        if state == UNSUPPORTED:
            raise UnsupportedOperationError(
                f"the {report.provider.name} provider reports {operation!r} as "
                "unsupported: the operation is refused rather than answered by "
                "a weaker local approximation")
        if state == UNKNOWN:
            logger.info("provider %s did not answer capability %r; the request "
                        "is attempted rather than denied",
                        report.provider.name, operation)
        return report

    # -- coordinate conversion --------------------------------------------

    def _file(self, rel_path: str) -> plans.TextFile:
        if rel_path not in self._files:
            self._files[rel_path] = plans.read_text_file(self.project_root,
                                                         rel_path)
        return self._files[rel_path]

    def _native_query(self, query: SymbolQuery) -> ProviderSymbolQuery:
        if query.qualified_name is not None:
            return ProviderSymbolQuery(path=query.path, position=None,
                                       qualified_name=query.qualified_name)
        assert query.path is not None and query.line is not None
        assert query.character is not None
        tf = self._file(query.path)
        if query.line > len(tf.lines):
            raise PositionConversionFailedError(
                f"{query.path}: line {query.line} is outside the file's "
                f"{len(tf.lines)} line(s)")
        offset = tf.line_starts[query.line - 1] + query.character
        line, character = offset_to_position(tf, offset)
        return ProviderSymbolQuery(
            path=query.path, position=ProviderPosition(line, character),
            qualified_name=None)

    def _native_declaration(self, declaration: DeclarationRef) -> ProviderCandidate:
        tf = self._file(declaration.path)
        start_line, start_character = self._native_position(tf, declaration.range.start_byte)
        end_line, end_character = self._native_position(tf, declaration.range.end_byte)
        if start_line != declaration.range.start_line - 1 \
                or end_line != declaration.range.end_line - 1:
            raise PositionConversionFailedError(
                f"{declaration.path}: the declaration's byte offsets and its "
                "line numbers disagree, so the provider cannot be asked about it")
        return ProviderCandidate(
            path=declaration.path,
            range=ProviderRange(ProviderPosition(start_line, start_character),
                                ProviderPosition(end_line, end_character)),
            qualified_name=declaration.qualified_name,
            symbol_type=declaration.symbol_type)

    @staticmethod
    def _native_position(tf: plans.TextFile, offset: int) -> tuple[int, int]:
        return offset_to_position(tf, offset)

    def _declaration_of(self, candidate: ProviderCandidate) -> DeclarationRef:
        tf = self._file(candidate.path)
        start = position_to_offset(tf, candidate.range.start.line,
                                   candidate.range.start.character)
        end = position_to_offset(tf, candidate.range.end.line,
                                 candidate.range.end.character)
        if end < start:
            raise PositionConversionFailedError(
                f"{candidate.path}: the provider's declaration range ends "
                "before it starts")
        start_line = tf.line_of_byte(start)
        end_line = tf.line_of_byte(end - 1) if end > start else start_line
        return DeclarationRef(
            path=candidate.path, qualified_name=candidate.qualified_name,
            symbol_type=candidate.symbol_type,
            range=SourceRange(start_line, end_line, start, end))

    def _location_of(self, location: ProviderLocation) -> Location:
        tf = self._file(location.path)
        start = position_to_offset(tf, location.range.start.line,
                                   location.range.start.character)
        end = position_to_offset(tf, location.range.end.line,
                                 location.range.end.character)
        if end < start:
            raise PositionConversionFailedError(
                f"{location.path}: the provider's location range ends before "
                "it starts")
        start_line = tf.line_of_byte(start)
        end_line = tf.line_of_byte(end - 1) if end > start else start_line
        return Location(location.path, SourceRange(start_line, end_line,
                                                   start, end))

    # -- the semantic operations -------------------------------------------

    def resolve_symbol(self, query: SymbolQuery) -> Resolution:
        self._require("resolve_symbol")
        provider = self._provider()
        raw = self._run(provider, "resolve_symbol", provider.resolve_symbol,
                        self._native_query(query))
        if raw is None:
            raise ProviderFailureError(
                f"the {self._provider_name()} provider returned no resolution "
                f"for {query.label()!r}")
        candidates = tuple(self._declaration_of(item) for item in raw)
        if len(candidates) > 1:
            raise AmbiguousSymbolError(
                query.label(),
                [self._candidate_block(ref) for ref in candidates])
        return Resolution(provider=self.capabilities().provider,
                          candidates=candidates)

    @staticmethod
    def _candidate_block(ref: DeclarationRef) -> dict[str, Any]:
        return {"file": ref.path, "range": ref.range.block(),
                "qualified_name": ref.qualified_name,
                "kind": ref.symbol_type, "container": None,
                "decl_hash": None}

    def find_references(self, declaration: DeclarationRef) -> References:
        self._require("find_references")
        provider = self._provider()
        raw = self._run(provider, "find_references", provider.find_references,
                        self._native_declaration(declaration))
        if raw is None:
            raise ProviderFailureError(
                f"the {self._provider_name()} provider returned no references "
                f"answer for {declaration.qualified_name!r}")
        return References(provider=self.capabilities().provider,
                          declaration=declaration,
                          locations=tuple(self._location_of(item)
                                          for item in raw))

    def prepare_rename(self, declaration: DeclarationRef,
                       new_name: str) -> RenameEdits:
        if not isinstance(new_name, str) or not new_name.strip():
            raise ArgumentError("a rename needs a non-empty new name")
        self._require("prepare_rename")
        provider = self._provider()
        raw = self._run(provider, "prepare_rename", provider.prepare_rename,
                        self._native_declaration(declaration), new_name)
        if raw is None:
            raise ProviderFailureError(
                f"the {self._provider_name()} provider returned no edits for "
                f"the rename of {declaration.qualified_name!r}")
        edits = tuple(raw)
        for edit in edits:
            if not isinstance(edit, ProviderEdit):
                raise ProviderFailureError(
                    "a provider rename answer must be ProviderEdit values, "
                    f"not {type(edit).__name__}")
        return RenameEdits(provider=self.capabilities().provider, edits=edits)


# ---------------------------------------------------------------------------
# provider edits -> line-aligned plan edits (ADR-0006 section 6)
# ---------------------------------------------------------------------------

def materialize(tf: plans.TextFile,
                edits: Sequence[ProviderEdit]) -> list[dict[str, Any]]:
    """Expand provider edits to whole-line candidate plan edits.

    Each edit is applied to its line's old text, so bytes outside the
    provider's range are preserved exactly; two edits on one line merge into
    one line edit, applied in descending byte order so an earlier edit never
    shifts a later one; each record's ``expected_old_text_hash`` covers the
    whole old line; and an edit whose range crosses a line terminator is
    refused rather than approximated.
    """
    if not edits:
        raise InternalError("materialize needs at least one provider edit")
    per_line: dict[int, list[tuple[int, int, str]]] = {}
    for edit in edits:
        if not isinstance(edit, ProviderEdit):
            raise InternalError(
                f"materialize takes ProviderEdit values, not {type(edit).__name__}")
        if edit.path != tf.rel_path:
            raise ArgumentError(
                f"the provider edit names {edit.path!r} but this file is "
                f"{tf.rel_path!r}")
        if edit.start.line != edit.end.line:
            raise NonLineAlignedProviderEditError(
                f"{edit.path}: the provider edit spans lines "
                f"{edit.start.line + 1}-{edit.end.line + 1}; a cross-line edit "
                "is refused in phase 1 rather than approximated")
        if "\r" in edit.new_text:
            raise CarriageReturnInPlanError(
                "a provider edit carries a carriage return; a line edit speaks "
                "LF only and the file's convention is applied on write")
        start = position_to_offset(tf, edit.start.line, edit.start.character)
        end = position_to_offset(tf, edit.end.line, edit.end.character)
        if end < start:
            raise ArgumentError(
                f"{edit.path}: the provider edit ends before it starts")
        per_line.setdefault(edit.start.line, []).append(
            (start, end, edit.new_text))

    out: list[dict[str, Any]] = []
    for line_index in sorted(per_line):
        line_start = tf.line_starts[line_index]
        line_end = tf.line_end_bytes[line_index]
        old_bytes = tf.body[line_start:line_end]
        replacements = per_line[line_index]
        # Overlap is refused rather than ordered, exactly as plans refuse it.
        ordered = sorted(replacements)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                raise OverlappingEditsError(
                    f"{tf.rel_path}:{line_index + 1}: two provider edits "
                    "overlap on one line; an overlap is refused, never ordered")
        text = old_bytes.decode("utf-8")
        for start, end, new_text in sorted(replacements,
                                           key=lambda item: (item[0], item[1]),
                                           reverse=True):
            text = (text[:start - line_start] + new_text
                    + text[end - line_start:])
        edit: dict[str, Any] = {
            "kind": PROVIDER_EDIT_KIND,
            "path": tf.rel_path,
            "start_line": line_index + 1,
            "end_line": line_index + 1,
            "start_byte": line_start,
            "end_byte": line_end,
            "expected_old_text_hash": "sha256:" + hashlib.sha256(old_bytes).hexdigest(),
        }
        if len(old_bytes) <= plans.REGION_TEXT_CAP:
            edit["expected_old_text"] = old_bytes.decode("utf-8")
        edit["new_text"] = text
        out.append(edit)
    return out


# ---------------------------------------------------------------------------
# invocation-scoped reuse
# ---------------------------------------------------------------------------

_SESSIONS: dict[tuple[str, str], ProviderSession] = {}


def session_for(project_root: str, *, language: str = "python",
                **options: Any) -> ProviderSession:
    """The one session for this project in this invocation.

    A second call for the same project and language returns the same session,
    so one provider serves the whole invocation. Pass ``factory`` only from a
    test or a registration path: an explicitly supplied provider is the
    caller's to close.
    """
    if "factory" in options and options["factory"] is not None:
        return ProviderSession(project_root, language=language, **options)
    key = (os.path.abspath(project_root), language)
    session = _SESSIONS.get(key)
    if session is None:
        session = ProviderSession(project_root, language=language, **options)
        _SESSIONS[key] = session
    return session


def close_all() -> None:
    """Terminate every invocation-scoped provider; registered with atexit."""
    for session in list(_SESSIONS.values()):
        session.close()
    _SESSIONS.clear()


atexit.register(close_all)
