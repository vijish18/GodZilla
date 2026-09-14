"""Injectable, timezone-aware clocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware instant."""
        ...


class ProductionClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, instant: datetime) -> None:
        self._instant = _require_aware(instant)

    def now(self) -> datetime:
        return self._instant

    def set(self, instant: datetime) -> None:
        self._instant = _require_aware(instant)

    def advance(self, delta: timedelta) -> datetime:
        self._instant += delta
        return self._instant


def _require_aware(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("clock instants must be timezone-aware")
    return instant
