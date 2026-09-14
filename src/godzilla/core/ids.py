"""Strongly separated UUID-backed internal identifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class UUIDBackedId:
    value: UUID

    @classmethod
    def new(cls) -> Self:
        return cls(uuid4())

    @classmethod
    def parse(cls, value: str) -> Self:
        return cls(UUID(value))

    def __str__(self) -> str:
        return str(self.value)


class SignalId(UUIDBackedId):
    pass


class TradeId(UUIDBackedId):
    pass


class EventId(UUIDBackedId):
    pass


class CorrelationId(UUIDBackedId):
    pass
