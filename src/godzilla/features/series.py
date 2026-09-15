"""Bar-based formulas shared by stock, index and sector features."""

import math
from itertools import pairwise
from statistics import fmean

from godzilla.features.math import ema, population_std, ratio_change, slope
from godzilla.features.models import FeatureSetVersion, FeatureValue, MissingReason
from godzilla.market_data.models import Bar

HORIZONS = (5, 15, 30, 60, 120)


class Values:
    def __init__(self) -> None:
        self.values: dict[str, FeatureValue] = {}

    def put(
        self, name: str, value: float | None, reason: MissingReason = MissingReason.WARMUP
    ) -> None:
        if value is not None and not math.isfinite(value):
            value, reason = None, MissingReason.INVALID_INPUT
        self.values[name] = FeatureValue(
            name=name, value=value, reason=reason if value is None else None
        )


def typical(bar: Bar) -> float:
    return (float(bar.high) + float(bar.low) + float(bar.close)) / 3


def series_features(
    result: Values,
    prefix: str,
    bars: tuple[Bar, ...],
    version: FeatureSetVersion,
    missing: MissingReason = MissingReason.WARMUP,
) -> None:
    closes = tuple(float(bar.close) for bar in bars)
    for horizon in HORIZONS:
        n = horizon // 5
        value = ratio_change(closes[-1], closes[-n - 1]) if len(closes) > n else None
        result.put(f"{prefix}.return_{horizon}m", value, missing)
        value = slope(closes[-n - 1 :]) / closes[-n - 1] if len(closes) > n else None
        result.put(f"{prefix}.slope_{horizon}m", value, missing)
    for period in (9, 20):
        curve = ema(closes, period)
        result.put(f"{prefix}.ema_{period}", curve[-1] if curve else None, missing)
        result.put(
            f"{prefix}.ema_{period}_slope",
            ratio_change(curve[-1], curve[-2]) if len(curve) > 1 else None,
            missing,
        )
    volume = sum(float(bar.volume) for bar in bars)
    vwap = sum(typical(bar) * float(bar.volume) for bar in bars) / volume if volume > 0 else None
    volume_reason = MissingReason.ZERO_DENOMINATOR if bars and volume == 0 else missing
    result.put(f"{prefix}.session_vwap", vwap, volume_reason)
    result.put(
        f"{prefix}.vwap_distance", closes[-1] / vwap - 1 if closes and vwap else None, volume_reason
    )
    n = version.statistics_bars
    log_returns = tuple(math.log(b / a) for a, b in pairwise(closes))
    result.put(
        f"{prefix}.realized_volatility",
        population_std(log_returns[-n:]) if len(log_returns) >= n else None,
        missing,
    )
    ranges = tuple(float(bar.high - bar.low) / float(bar.close) for bar in bars)
    result.put(
        f"{prefix}.range_volatility",
        math.sqrt(fmean(value * value for value in ranges[-n:])) if len(ranges) >= n else None,
        missing,
    )
    result.put(
        f"{prefix}.range_percentile",
        sum(value <= ranges[-1] for value in ranges[-n - 1 : -1]) / n if len(ranges) > n else None,
        missing,
    )
    tr = tuple(
        max(
            float(bar.high - bar.low),
            abs(float(bar.high) - previous),
            abs(float(bar.low) - previous),
        )
        for bar, previous in zip(bars[1:], closes[:-1], strict=True)
    )
    result.put(
        f"{prefix}.atr",
        fmean(tr[-version.atr_bars :]) if len(tr) >= version.atr_bars else None,
        missing,
    )
    ready = len(bars) >= version.opening_range_bars
    opening = bars[: version.opening_range_bars]
    result.put(
        f"{prefix}.opening_range_high",
        max(float(bar.high) for bar in opening) if ready else None,
        missing,
    )
    result.put(
        f"{prefix}.opening_range_low",
        min(float(bar.low) for bar in opening) if ready else None,
        missing,
    )
    k = version.swing_confirmation_bars
    highs, lows = [], []
    for confirmed in range(2 * k, len(bars)):
        # Evaluate a trailing pattern only at its confirmation bar; never backdate the feature.
        i = confirmed - k
        neighbors = bars[confirmed - 2 * k : i] + bars[i + 1 : confirmed + 1]
        if all(bars[i].high > other.high for other in neighbors):
            highs.append(float(bars[i].high))
        if all(bars[i].low < other.low for other in neighbors):
            lows.append(float(bars[i].low))
    result.put(f"{prefix}.swing_high", highs[-1] if highs else None, missing)
    result.put(f"{prefix}.swing_low", lows[-1] if lows else None, missing)
