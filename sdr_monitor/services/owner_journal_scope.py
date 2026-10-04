"""Inert cached owner scope reads for the admitted RTBW application graph."""
from __future__ import annotations

from ..domain.analytical_journal import JournalState, OwnerJournalScope, OwnerJournalSnapshot


def cached_owner_journals(port: object) -> tuple[OwnerJournalSnapshot, ...]:
    many = getattr(port, "analytical_journal_snapshots", None)
    single = getattr(port, "analytical_journal_snapshot", None)
    result = many() if callable(many) else (single(),) if callable(single) else ()
    if (type(result) is not tuple or len(result) > 2
            or any(not isinstance(value, OwnerJournalSnapshot) for value in result)):
        raise ValueError("cached owner journal contract differs")
    return result


def capture_owner_scopes(live: object, bindings: tuple[tuple[str, str], ...]
                         ) -> tuple[tuple[str, OwnerJournalScope], ...]:
    snapshots = cached_owner_journals(live)
    if not snapshots or any(value.state is not JournalState.ACTIVE or value.scope is None
                            for value in snapshots):
        return ()  # Explicitly unqualified, never infer a producer from a UI name.
    scopes = tuple(value.scope for value in snapshots)
    by_source = {value.source_id: value for value in scopes if value is not None}
    if (len(scopes) != len(bindings) or len(by_source) != len(scopes)
            or set(by_source) != {source for _, source in bindings}):
        raise ValueError("admitted owner scope differs from exact endpoint producer bindings")
    return tuple((endpoint, by_source[source]) for endpoint, source in bindings)
