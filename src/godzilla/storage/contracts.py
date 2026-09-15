"""Domain-facing repository ports; no ORM types cross this boundary."""

from typing import Protocol

from godzilla.core.events.models import EventEnvelope, SystemStateSnapshot
from godzilla.features.models import FeatureSnapshot
from godzilla.market_data.instruments import InstrumentMasterSnapshot


class EventRepository(Protocol):
    def append(
        self, event: EventEnvelope, key: str, *, state: SystemStateSnapshot | None = None
    ) -> bool:
        """Atomically append event and derived projections; False means exact redelivery."""
        ...

    def read_events(self, after_sequence: int = 0) -> tuple[EventEnvelope, ...]: ...


class InstrumentRepository(Protocol):
    def save_master(self, snapshot: InstrumentMasterSnapshot) -> None: ...

    def get_master(self, snapshot_id: str) -> InstrumentMasterSnapshot | None: ...


class SystemStateRepository(Protocol):
    def get_state(self, scope: str) -> SystemStateSnapshot | None: ...


class FeatureRepository(Protocol):
    def get_feature_snapshot(self, snapshot_hash: str) -> FeatureSnapshot | None: ...
