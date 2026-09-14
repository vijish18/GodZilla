"""Raw and normalized storage ports; memory fakes only until Phase 4 persistence."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, field_validator

from godzilla.market_data.models import Bar, Quote, utc


class RawRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    record_id: str
    source: str
    received_at: datetime
    payload: bytes

    _timestamp_utc = field_validator("received_at")(utc)


class RawStorage(Protocol):
    def append_raw(self, record: RawRecord) -> None: ...


class NormalizedStorage(Protocol):
    def append_observation(self, observation: Bar | Quote, *, version: str) -> None: ...


class MemoryRawStorage:
    def __init__(self) -> None:
        self.records: dict[str, RawRecord] = {}

    def append_raw(self, record: RawRecord) -> None:
        previous = self.records.get(record.record_id)
        if previous is not None and previous != record:
            raise ValueError("immutable raw record ID conflict")
        self.records[record.record_id] = record


class MemoryNormalizedStorage:
    def __init__(self) -> None:
        self.records: list[tuple[str, Bar | Quote]] = []

    def append_observation(self, observation: Bar | Quote, *, version: str) -> None:
        item = (version, observation)
        if item not in self.records:
            self.records.append(item)
