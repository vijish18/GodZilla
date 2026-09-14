"""Causal, configurable validation; invalid values are retained for diagnostics."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from godzilla.market_data.calendar import ExchangeCalendar
from godzilla.market_data.models import Bar, QualityFlag, utc


class DataQualitySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    abnormal_gap_fraction: Decimal = Field(default=Decimal("0.10"), gt=0)
    abnormal_volume_multiple: Decimal = Field(default=Decimal("10"), gt=1)
    normalization_version: str = "ohlcv-v1"


def price_flags(bar: Bar) -> frozenset[QualityFlag]:
    flags: set[QualityFlag] = set()
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        flags.add(QualityFlag.INVALID_PRICE)
    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        flags.add(QualityFlag.IMPOSSIBLE_OHLC)
    if bar.volume < 0:
        flags.add(QualityFlag.INVALID_VOLUME)
    if bar.volume == 0:
        flags.add(QualityFlag.ZERO_VOLUME)
    return frozenset(flags)


def session_bounds(
    calendar: ExchangeCalendar, instant: datetime, timezone: str
) -> tuple[datetime, datetime] | None:
    local = utc(instant).astimezone(ZoneInfo(timezone))
    session = calendar.session_on(local.date())
    if session is None:
        return None
    zone = ZoneInfo(session.timezone)
    return (
        utc(datetime.combine(session.session_date, session.times.open_time, zone)),
        utc(datetime.combine(session.session_date, session.times.market_close, zone)),
    )


class BarQualityChecker:
    def __init__(
        self, calendar: ExchangeCalendar, settings: DataQualitySettings, timezone: str
    ) -> None:
        self.calendar = calendar
        self.settings = settings
        self.timezone = timezone

    def check(self, bar: Bar, previous: Bar | None = None) -> Bar:
        flags = set(bar.quality_flags | price_flags(bar))
        bounds = session_bounds(self.calendar, bar.start, self.timezone)
        if bounds is None or bar.start < bounds[0] or bar.end > bounds[1]:
            flags.add(QualityFlag.OUT_OF_SESSION)
        elif (bar.start - bounds[0]).total_seconds() % (60 * bar.interval_minutes):
            flags.add(QualityFlag.MISALIGNED)
        if not bar.complete:
            flags.add(QualityFlag.INCOMPLETE)
        if bar.received_at < bar.end:
            flags.add(QualityFlag.FUTURE_TIMESTAMP)
        if (
            previous is not None
            and previous.instrument_id == bar.instrument_id
            and previous.source == bar.source
            and previous.end <= bar.start
            and previous.received_at <= bar.received_at
        ):
            if (
                previous.close > 0
                and abs(bar.open / previous.close - 1) > self.settings.abnormal_gap_fraction
            ):
                flags.add(QualityFlag.ABNORMAL_GAP)
            if (
                previous.volume > 0
                and bar.volume / previous.volume > self.settings.abnormal_volume_multiple
            ):
                flags.add(QualityFlag.ABNORMAL_VOLUME)
        return bar.model_copy(update={"quality_flags": frozenset(flags)})

    def missing_intervals(
        self, bars: tuple[Bar, ...], start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[datetime, ...]:
        """Enumerate missing completed 1m intervals, including completely absent windows."""
        cursor, stop = utc(start), min(utc(end), utc(as_of))
        if utc(start) >= utc(end) or cursor.second or cursor.microsecond:
            raise ValueError("missing interval window must be ordered and minute-aligned")
        if len({(bar.instrument_id, bar.source) for bar in bars}) > 1:
            raise ValueError("missing interval check requires a single instrument and source")
        present = {
            bar.start
            for bar in bars
            if bar.interval_minutes == 1
            and bar.complete
            and max(bar.end, bar.received_at) <= utc(as_of)
        }
        missing: list[datetime] = []
        while cursor + timedelta(minutes=1) <= stop:
            bounds = session_bounds(self.calendar, cursor, self.timezone)
            if (
                bounds
                and bounds[0] <= cursor
                and cursor + timedelta(minutes=1) <= bounds[1]
                and (cursor - bounds[0]).total_seconds() % 60 == 0
                and cursor not in present
            ):
                missing.append(cursor)
            cursor += timedelta(minutes=1)
        return tuple(missing)
