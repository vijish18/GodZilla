"""Point-in-time exchange calendar and intraday session state."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SessionState(StrEnum):
    CLOSED = "CLOSED"
    PREOPEN = "PREOPEN"
    OPEN = "OPEN"
    FINAL_ENTRY_BLOCKED = "FINAL_ENTRY_BLOCKED"
    FLATTENING = "FLATTENING"
    CLOSED_POST = "CLOSED_POST"


class SpecialSessionStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    TIMINGS_PENDING = "TIMINGS_PENDING"
    CANCELLED = "CANCELLED"


class SessionTimes(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    preopen_start: time
    open_time: time
    final_entry_time: time
    flatten_start_time: time
    hard_flatten_deadline: time
    market_close: time

    @model_validator(mode="after")
    def validate_order(self) -> SessionTimes:
        values = (
            self.preopen_start,
            self.open_time,
            self.final_entry_time,
            self.flatten_start_time,
            self.hard_flatten_deadline,
            self.market_close,
        )
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("session times must be strictly increasing")
        return self


class Holiday(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    date: date
    name: str = Field(min_length=1)


class SpecialSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    date: date
    name: str = Field(min_length=1)
    status: SpecialSessionStatus
    times: SessionTimes | None = None

    @model_validator(mode="after")
    def confirmed_requires_times(self) -> SpecialSession:
        if self.status is SpecialSessionStatus.CONFIRMED and self.times is None:
            raise ValueError("confirmed special session requires times")
        return self


class CalendarSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    calendar_id: str
    version: str
    exchange: str
    timezone: str
    effective_from: date
    effective_to: date
    regular_weekdays: frozenset[int]
    regular_times: SessionTimes
    holidays: tuple[Holiday, ...]
    special_sessions: tuple[SpecialSession, ...] = ()
    source_reference: str
    reviewed_at: datetime

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


class Session(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_date: date
    timezone: str
    times: SessionTimes
    special: bool = False


class CalendarCoverageError(RuntimeError):
    """Raised when a date is outside the versioned snapshot."""


class ExchangeCalendar(Protocol):
    def session_on(self, day: date) -> Session | None: ...

    def state_at(self, instant: datetime) -> SessionState: ...


class NseExchangeCalendar:
    def __init__(self, snapshot: CalendarSnapshot) -> None:
        if snapshot.exchange != "NSE":
            raise ValueError("NSE calendar requires exchange=NSE")
        self.snapshot = snapshot
        self._timezone = ZoneInfo(snapshot.timezone)

    def local_date(self, instant: datetime) -> date:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("calendar instant must be timezone-aware")
        return instant.astimezone(self._timezone).date()

    def session_on(self, day: date) -> Session | None:
        if not self.snapshot.effective_from <= day <= self.snapshot.effective_to:
            raise CalendarCoverageError(f"calendar snapshot does not cover {day.isoformat()}")
        specials = {item.date: item for item in self.snapshot.special_sessions}
        special = specials.get(day)
        if special is not None:
            if special.status is not SpecialSessionStatus.CONFIRMED or special.times is None:
                return None
            return Session(
                session_date=day,
                timezone=self.snapshot.timezone,
                times=special.times,
                special=True,
            )
        if day.weekday() not in self.snapshot.regular_weekdays:
            return None
        if day in {item.date for item in self.snapshot.holidays}:
            return None
        return Session(
            session_date=day,
            timezone=self.snapshot.timezone,
            times=self.snapshot.regular_times,
        )

    def state_at(self, instant: datetime) -> SessionState:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("session evaluation time must be timezone-aware")
        local = instant.astimezone(self._timezone)
        session = self.session_on(local.date())
        if session is None:
            return SessionState.CLOSED
        current = local.timetz().replace(tzinfo=None)
        times = session.times
        if current < times.preopen_start:
            return SessionState.CLOSED
        if current < times.open_time:
            return SessionState.PREOPEN
        if current < times.final_entry_time:
            return SessionState.OPEN
        if current < times.flatten_start_time:
            return SessionState.FINAL_ENTRY_BLOCKED
        if current < times.hard_flatten_deadline:
            return SessionState.FLATTENING
        return SessionState.CLOSED_POST
