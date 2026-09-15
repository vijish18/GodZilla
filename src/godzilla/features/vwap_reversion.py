"""Versioned causal session features for exhaustion/reversal research."""

from datetime import datetime, timedelta
from statistics import fmean
from typing import Literal

from pydantic import Field, field_validator

from godzilla.features.models import FeatureModel, content_hash
from godzilla.features.series import typical
from godzilla.market_data.models import Bar, FeedKind, utc


class ReversionFeatureVersion(FeatureModel):
    revision: Literal["vwap-reversion-features-v1"] = "vwap-reversion-features-v1"
    scale_bars: int = Field(default=12, ge=3)

    def version_hash(self) -> str:
        return content_hash(self)


class ReversionFeatures(FeatureModel):
    timestamp: datetime
    instrument_id: str
    source: str
    session_vwap: float
    volatility_scale: float = Field(gt=0)
    stretch: float
    impulse: float
    decelerated_impulse: float
    reversal: float
    body: float
    close_location: float = Field(ge=0, le=1)
    price: float
    definition: ReversionFeatureVersion
    input_hash: str

    _aware = field_validator("timestamp")(utc)


class _Bars(FeatureModel):
    bars: tuple[Bar, ...]


def reversion_features(
    observations: tuple[Bar, ...],
    at: datetime,
    opening: datetime,
    definition: ReversionFeatureVersion,
) -> ReversionFeatures:
    at, opening = utc(at), utc(opening)
    bars = tuple(
        sorted(
            (b for b in observations if opening <= b.start and b.end <= at and b.received_at <= at),
            key=lambda b: b.start,
        )
    )
    if len(bars) < definition.scale_bars + 2:
        raise ValueError("FEATURE_WARMUP")
    for index, bar in enumerate(bars):
        bar.require_strategy_ready(at)
        if (
            bar.start != opening + timedelta(minutes=5 * index)
            or bar.instrument_id != bars[0].instrument_id
            or bar.source != bars[0].source
            or bar.feed_kind is not FeedKind.EQUITY
        ):
            raise ValueError("SESSION_HISTORY_AMBIGUOUS")
    closes = tuple(float(b.close) for b in bars)
    # Exclude the trigger bar from the scale, preventing its range diluting its own stretch.
    prior = bars[-definition.scale_bars - 1 : -1]
    previous = closes[-definition.scale_bars - 2 : -2]
    scale = fmean(
        max(float(b.high - b.low), abs(float(b.high) - p), abs(float(b.low) - p))
        for b, p in zip(prior, previous, strict=True)
    )
    if scale <= 0:
        raise ValueError("DEGENERATE_VOLATILITY")
    vwap = sum(typical(b) * float(b.volume) for b in bars) / sum(float(b.volume) for b in bars)
    current = bars[-1]
    bar_range = float(current.high - current.low)
    if bar_range <= 0:
        raise ValueError("NO_REVERSAL_RANGE")
    return ReversionFeatures(
        timestamp=current.end,
        instrument_id=current.instrument_id,
        source=current.source,
        session_vwap=vwap,
        volatility_scale=scale,
        stretch=(closes[-1] - vwap) / scale,
        impulse=(closes[-3] - closes[-4]) / scale,
        decelerated_impulse=(closes[-2] - closes[-3]) / scale,
        reversal=(closes[-1] - closes[-2]) / scale,
        body=float(current.close - current.open) / scale,
        close_location=float(current.close - current.low) / bar_range,
        price=closes[-1],
        definition=definition,
        input_hash=content_hash(_Bars(bars=bars)),
    )
