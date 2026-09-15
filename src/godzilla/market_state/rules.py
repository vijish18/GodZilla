"""Auditable pure classification and fixed version-1 permission mapping."""

from typing import Literal

from godzilla.features.models import FeatureSnapshot
from godzilla.market_state.models import AlphaFamily, MarketState, RouterSettings, ThresholdEvidence


class EvidenceBuilder:
    def __init__(self, snapshot: FeatureSnapshot) -> None:
        self.values = {item.name: item for item in snapshot.values}
        self.checks: list[ThresholdEvidence] = []

    def check(
        self,
        name: str,
        operator: Literal["ge", "le", "eq", "abs_ge"],
        threshold: float,
    ) -> bool:
        item = self.values.get(name)
        value = item.value if item else None
        return self.record(
            name,
            value,
            operator,
            threshold,
            str(item.reason)
            if item and item.reason
            else "MISSING_INPUT"
            if value is None
            else None,
        )

    def record(
        self,
        name: str,
        value: float | None,
        operator: Literal["ge", "le", "eq", "abs_ge"],
        threshold: float,
        missing_reason: str | None = None,
    ) -> bool:
        passed = (
            value is not None
            and {
                "ge": lambda: value >= threshold,
                "le": lambda: value <= threshold,
                "eq": lambda: value == threshold,
                "abs_ge": lambda: abs(value) >= threshold,
            }[operator]()
        )
        self.checks.append(
            ThresholdEvidence(
                name=name,
                value=value,
                operator=operator,
                threshold=threshold,
                passed=passed,
                missing_reason=missing_reason,
            )
        )
        return passed


def classify(e: EvidenceBuilder, cfg: RouterSettings, elapsed: float) -> MarketState:
    stabilizing = e.record("session.elapsed_seconds", elapsed, "le", cfg.stabilization_seconds)
    opening = e.record("session.shock_window", elapsed, "le", cfg.shock_window_seconds)
    gap = e.check("market.opening_gap", "abs_ge", cfg.opening_gap)
    high = (
        e.check("market.realized_volatility", "ge", cfg.high_realized_vol),
        e.check("market.range_volatility", "ge", cfg.high_range_vol),
        e.check("market.vix_level", "ge", cfg.high_vix),
    )
    up = (
        e.check("market.vwap_distance", "ge", cfg.vwap_distance),
        e.check("market.slope_15m", "ge", cfg.slope_15m),
        e.check("market.slope_30m", "ge", cfg.slope_30m),
        e.check("market.breadth_advancing", "ge", cfg.breadth_fraction),
    )
    down = (
        e.check("market.vwap_distance", "le", -cfg.vwap_distance),
        e.check("market.slope_15m", "le", -cfg.slope_15m),
        e.check("market.slope_30m", "le", -cfg.slope_30m),
        e.check("market.breadth_declining", "ge", cfg.breadth_fraction),
    )
    coverage = e.check("market.breadth_coverage", "eq", 1)
    if stabilizing or (opening and (gap or any(high))):
        return MarketState.OPEN_SHOCK
    required = (
        "market.realized_volatility",
        "market.range_volatility",
        "market.vwap_distance",
        "market.slope_15m",
        "market.slope_30m",
        "market.breadth_advancing",
        "market.breadth_declining",
    ) + (("market.opening_gap",) if opening else ())
    if not coverage or any(
        name not in e.values or e.values[name].value is None for name in required
    ):
        return MarketState.RISK_OFF
    if any(high):
        return MarketState.HIGH_VOL
    if all(up):
        return MarketState.TREND_UP
    if all(down):
        return MarketState.TREND_DOWN
    balanced = []
    for name, bound in (
        ("market.vwap_distance", cfg.vwap_distance),
        ("market.slope_15m", cfg.slope_15m),
        ("market.slope_30m", cfg.slope_30m),
    ):
        balanced.append(e.check(name, "le", bound))
        balanced.append(e.check(name, "ge", -bound))
    balanced.extend(
        [
            e.check("market.breadth_advancing", "le", cfg.breadth_fraction),
            e.check("market.breadth_declining", "le", cfg.breadth_fraction),
        ]
    )
    return MarketState.CHOP if all(balanced) else MarketState.RISK_OFF


def permissions(
    state: MarketState,
    cfg: RouterSettings,
) -> tuple[tuple[AlphaFamily, ...], tuple[Literal["LONG", "SHORT"], ...], float]:
    if state in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK):
        return (), (), 0
    if state is MarketState.CHOP:
        return (
            (
                AlphaFamily.PAIRS_STAT_ARB,
                AlphaFamily.VWAP_MEAN_REVERSION,
                AlphaFamily.SECTOR_RELATIVE_VALUE,
            ),
            (),
            cfg.chop_multiplier,
        )
    if state is MarketState.HIGH_VOL:
        return (AlphaFamily.SECTOR_RELATIVE_VALUE,), (), cfg.high_vol_multiplier
    return (
        (
            AlphaFamily.CROSS_SECTIONAL_MOMENTUM,
            AlphaFamily.MOMENTUM_BREAKOUT,
            AlphaFamily.SECTOR_RELATIVE_VALUE,
        ),
        ("LONG",) if state is MarketState.TREND_UP else ("SHORT",),
        cfg.trend_multiplier,
    )
