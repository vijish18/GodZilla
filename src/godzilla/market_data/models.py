"""Vendor-neutral observations. All ordering timestamps are normalized to UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class QualityFlag(StrEnum):
    INVALID_PRICE = "INVALID_PRICE"
    IMPOSSIBLE_OHLC = "IMPOSSIBLE_OHLC"
    INVALID_VOLUME = "INVALID_VOLUME"
    ZERO_VOLUME = "ZERO_VOLUME"
    ABNORMAL_GAP = "ABNORMAL_GAP"
    ABNORMAL_VOLUME = "ABNORMAL_VOLUME"
    DUPLICATE = "DUPLICATE"
    MISSING_INTERVAL = "MISSING_INTERVAL"
    OUT_OF_SESSION = "OUT_OF_SESSION"
    MISALIGNED = "MISALIGNED"
    INCOMPLETE = "INCOMPLETE"
    STALE = "STALE"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    CROSSED_QUOTE = "CROSSED_QUOTE"


class FeedKind(StrEnum):
    EQUITY = "EQUITY"
    INDEX = "INDEX"
    SECTOR = "SECTOR"
    VIX = "VIX"


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    instrument_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    received_at: datetime
    quality_flags: frozenset[QualityFlag] = frozenset()
    feed_kind: FeedKind = FeedKind.EQUITY
    schema_version: str = "1"

    _received_utc = field_validator("received_at")(utc)

    @field_serializer("quality_flags")
    def sorted_flags(self, flags: frozenset[QualityFlag]) -> list[str]:
        return sorted(flag.value for flag in flags)


class Bar(Observation):
    """OHLCV over [start, end); receipt time controls historical availability."""

    start: datetime
    end: datetime
    interval_minutes: int = Field(ge=1, le=5)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    complete: bool = True

    _times_utc = field_validator("start", "end")(utc)

    @model_validator(mode="after")
    def interval_is_consistent(self) -> Bar:
        if self.interval_minutes not in {1, 5}:
            raise ValueError("only 1m and 5m bars are supported")
        if self.end - self.start != timedelta(minutes=self.interval_minutes):
            raise ValueError("bar duration differs from interval")
        return self

    def require_strategy_ready(self, at: datetime) -> None:
        from godzilla.market_data.quality import price_flags

        if (
            self.interval_minutes != 5
            or not self.complete
            or self.quality_flags
            or price_flags(self)
            or max(self.end, self.received_at) > utc(at)
        ):
            raise ValueError("bar is not complete, causal, quality-cleared canonical data")


class Quote(Observation):
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal

    _quote_utc = field_validator("timestamp")(utc)
