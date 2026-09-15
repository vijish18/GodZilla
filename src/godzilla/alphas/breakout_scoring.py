"""Symmetric closed-bar breakout measurements; no price prediction or order sizing."""

from datetime import datetime, timedelta
from typing import Literal

from godzilla.alphas.breakout_models import (
    BreakoutComponent,
    BreakoutInvalidation,
    BreakoutObservation,
    BreakoutSettings,
)
from godzilla.market_state.rules import EvidenceBuilder


def numeric(item: BreakoutObservation, name: str, *, reference: bool = False) -> float:
    snapshot = item.reference.snapshot if reference else item.current.snapshot
    values = {v.name: v.value for v in snapshot.values}
    value = values.get(name)
    if value is None:
        raise ValueError("MISSING_FEATURE:" + name)
    return value


def measure_breakout(
    item: BreakoutObservation,
    side: Literal["LONG", "SHORT"],
    strength: float,
    cfg: BreakoutSettings,
    opening: datetime,
    closing: datetime,
    evidence: EvidenceBuilder,
) -> tuple[tuple[BreakoutComponent, ...], BreakoutInvalidation | None, tuple[str, ...]]:
    sign = 1 if side == "LONG" else -1
    bar, previous = item.bar, item.previous_bar
    atr = numeric(item, "stock.atr")
    if atr <= 0 or bar.high == bar.low:
        return (), None, ("INVALID_ATR_OR_FLAT_BAR",)
    high, low, close, opened = map(float, (bar.high, bar.low, bar.close, bar.open))
    levels: list[tuple[Literal["opening_range", "swing"], float]] = []
    suffix = "high" if side == "LONG" else "low"
    for kind in ("opening_range", "swing"):
        if cfg.range_source not in (kind, "either"):
            continue
        if kind == "swing" and item.reference.snapshot.timestamp < (
            opening
            + timedelta(minutes=5 * (2 * item.feature_definition.swing_confirmation_bars + 1))
        ):
            continue
        if kind == "opening_range" and item.reference.snapshot.timestamp < (
            opening + timedelta(minutes=5 * item.feature_definition.opening_range_bars)
        ):
            continue
        try:
            level = numeric(item, "stock." + kind + "_" + suffix, reference=True)
        except ValueError:
            continue
        if level > 0 and sign * (float(previous.close) - level) <= 0 < sign * (close - level):
            levels.append((kind, level))
    if not levels:
        return (), None, ("NO_NEW_BREAK_OF_KNOWN_LEVEL",)
    # Hardest crossed level; opening range wins an equal-price tie.
    kind, level = max(levels, key=lambda pair: sign * pair[1])
    extension = sign * (close - level) / atr
    location = (close - low) / (high - low) if side == "LONG" else (high - close) / (high - low)
    body = sign * (close - opened) / (high - low)
    vwap = numeric(item, "stock.session_vwap")
    sector = sign * numeric(item, "sector.vwap_distance")
    slope = sign * numeric(item, "stock.ema_20_slope")
    relative_volume = numeric(item, "stock.relative_volume")
    residual_sector = sign * numeric(item, "stock.relative_sector_30m")
    residual_market = sign * numeric(item, "stock.relative_market_30m")
    spread = numeric(item, "liquidity.spread_bps")
    age = numeric(item, "liquidity.quote_age")
    adv = numeric(item, "liquidity.adv")
    turnover = numeric(item, "liquidity.average_daily_turnover")
    invalidation_level = level - sign * cfg.invalidation_buffer_atr * atr
    proxy = cfg.reward_distance_atr / (extension + cfg.invalidation_buffer_atr)
    checks = (
        evidence.record("stock.vwap_positive", float(vwap > 0), "eq", 1),
        evidence.record("stock.above_below_vwap", float(sign * (close - vwap) > 0), "eq", 1),
        evidence.record("sector.direction_confirmed", float(sector > 0), "eq", 1),
        evidence.record("stock.slope_confirmed", float(slope > 0), "eq", 1),
        evidence.record("relative_strength", strength, "ge", cfg.minimum_strength_percentile),
        evidence.record("residual_sector", residual_sector, "ge", cfg.minimum_residual_return),
        evidence.record("residual_market", residual_market, "ge", cfg.minimum_residual_return),
        evidence.record(
            "stock.relative_volume", relative_volume, "ge", cfg.minimum_relative_volume
        ),
        evidence.record("breakout.extension_atr", extension, "ge", cfg.min_extension_atr),
        evidence.record("breakout.extension_atr", extension, "le", cfg.max_extension_atr),
        evidence.record("breakout.close_location", location, "ge", cfg.min_close_location),
        evidence.record("breakout.body_fraction", body, "ge", cfg.min_body_fraction),
        evidence.record("reward_risk_proxy", proxy, "ge", cfg.min_reward_risk_proxy),
        evidence.record("liquidity.spread_bps", spread, "ge", 0),
        evidence.record("liquidity.spread_bps", spread, "le", cfg.max_spread_bps),
        evidence.record("liquidity.quote_age", age, "ge", 0),
        evidence.record("liquidity.quote_age", age, "le", cfg.max_quote_age_seconds),
        evidence.record("liquidity.adv", adv, "ge", cfg.min_adv),
        evidence.record("liquidity.turnover", turnover, "ge", cfg.min_daily_turnover),
        evidence.record("invalidation.positive_price", float(invalidation_level > 0), "eq", 1),
    )
    if not all(checks):
        return (), None, tuple("REJECTED:" + c.name for c in evidence.checks if not c.passed)
    quality = (location + body + min(1, extension / cfg.full_extension_atr)) / 3
    liquidity = ((1 - spread / cfg.max_spread_bps) + (1 - age / cfg.max_quote_age_seconds)) / 2
    raw = {
        "relative_strength": strength,
        "breakout_quality": quality,
        "relative_volume": relative_volume,
        "sector_confirmation": sector,
        "slope_quality": slope,
        "reward_risk_proxy": proxy,
        "liquidity_quality": liquidity,
    }
    scales = {
        "relative_volume": cfg.full_relative_volume,
        "sector_confirmation": cfg.full_sector_distance,
        "slope_quality": cfg.full_slope,
        "reward_risk_proxy": cfg.full_reward_risk_proxy,
    }
    weights = cfg.weights.model_dump()
    components = tuple(
        BreakoutComponent(
            name=name,
            raw_value=value,
            normalized_value=min(1, max(0, value / scales.get(name, 1))),
            weight=weights[name],
            contribution=weights[name] * min(1, max(0, value / scales.get(name, 1))),
        )
        for name, value in raw.items()
    )
    metadata = BreakoutInvalidation(
        range_kind=kind,
        broken_level=level,
        invalidation_level=invalidation_level,
        level_known_at=item.reference.snapshot.timestamp,
        trigger_bar_end=bar.end,
        expires_at=min(closing, bar.end + timedelta(minutes=5 * cfg.observation_bars)),
    )
    return (
        components,
        metadata,
        (
            "CLOSED_BAR_BREAK",
            "KNOWN_" + kind.upper(),
            "RESIDUAL_CONFIRMED",
            "PARTICIPATION_CONFIRMED",
            "LIQUIDITY_CONFIRMED",
        ),
    )
