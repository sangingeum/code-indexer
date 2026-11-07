"""Does a session recover after one request times out?

The session runs its provider calls on a one-worker executor. A first answer
that is slow enough to expire does not stop the worker: it keeps running the
call. This probe asks the same healthy session a second and third time and
prints what each request got, so the answer is measured instead of argued.
"""

import time

from code_indexer import refactoring_provider as provider
from code_indexer.errors import ProviderTimeoutError


class SlowOnce:
    """First semantic answer takes 0.30 s; every later answer is instant."""

    def __init__(self, project_root):
        self.project_root = project_root
        self.calls = 0

    def capabilities(self):
        return (provider.ProviderIdentity("slow-once", "1.0.0", "in-process"),
                {"resolve_symbol": "supported",
                 "find_references": "supported",
                 "prepare_rename": "unsupported"})

    def resolve_symbol(self, query):
        self.calls += 1
        if self.calls == 1:
            time.sleep(0.30)
        return []

    def cancel(self):
        pass

    def close(self):
        pass


session = provider.session_for("/nonexistent-project", factory=SlowOnce,
                              request_deadline=0.05)
query = provider.SymbolQuery(qualified_name="greet")

for attempt in (1, 2, 3):
    started = time.monotonic()
    try:
        answer = session.resolve_symbol(query)
        outcome = f"answered ({len(answer.candidates)} candidate(s))"
    except ProviderTimeoutError as exc:
        outcome = f"{exc.error_type}: {exc}"
    print(f"request {attempt}: {outcome}  [{time.monotonic() - started:.2f}s]")
    if attempt == 2:
        time.sleep(0.35)

print(f"provider calls made: {session._instance.calls}")
session.close()
