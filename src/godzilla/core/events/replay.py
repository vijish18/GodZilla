"""Replay the persisted order; never reorder delayed observations by market time."""

from collections.abc import Callable, Iterable
from pathlib import Path

from godzilla.core.clock import ReplayClock
from godzilla.core.events.models import EventEnvelope
from godzilla.core.events.serialization import deserialize_event, event_hash


def read_fixture(path: Path) -> tuple[EventEnvelope, ...]:
    return tuple(
        deserialize_event(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


class ReplayEngine:
    def __init__(self, clock: ReplayClock) -> None:
        self.clock = clock

    def run(self, events: Iterable[EventEnvelope], emit: Callable[[EventEnvelope], None]) -> int:
        seen: dict[str, str] = {}
        emitted = 0
        for event in events:
            identity, digest = str(event.event_id), event_hash(event)
            if identity in seen:
                if seen[identity] != digest:
                    raise ValueError("conflicting duplicate replay event")
                continue
            self.clock.advance_to(event.received_at)
            emit(event)
            seen[identity] = digest
            emitted += 1
        return emitted
