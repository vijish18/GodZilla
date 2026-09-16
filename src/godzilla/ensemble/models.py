"""Versioned alpha registry, bounded health policy and normalized candidate contracts."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.models import utc
from godzilla.market_state.models import MarketState


class AlphaId(StrEnum):
    MOMENTUM = "cross_sectional_momentum"
    BREAKOUT = "momentum_breakout"
    PAIRS = "pairs_stat_arb"
    VWAP = "vwap_mean_reversion"
    RELATIVE_VALUE = "sector_relative_value"


class AlphaRegistration(FeatureModel):
    alpha_id: AlphaId
    base_budget: Decimal = Field(default=Decimal("0.2"), ge=0, le=1)
    enabled: bool = True
    score_floor: float = Field(default=0, ge=0, le=1)
    score_ceiling: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "AlphaRegistration":
        if self.score_floor >= self.score_ceiling:
            raise ValueError("normalization range must be ordered")
        return self


class AlphaRegistry(FeatureModel):
    version: Literal["alpha-registry-v1"] = "alpha-registry-v1"
    entries: tuple[AlphaRegistration, ...] = tuple(AlphaRegistration(alpha_id=a) for a in AlphaId)

    @model_validator(mode="after")
    def unique_budget(self) -> "AlphaRegistry":
        if {x.alpha_id for x in self.entries} != set(AlphaId) or len(self.entries) != len(AlphaId):
            raise ValueError("registry must contain each alpha exactly once")
        if sum(x.base_budget for x in self.entries) > 1:
            raise ValueError("base budgets cannot exceed equity")
        return self

    def registration(self, alpha: AlphaId) -> AlphaRegistration:
        return next(x for x in self.entries if x.alpha_id is alpha)


class AlphaHealthSettings(FeatureModel):
    version: Literal["alpha-health-v1"] = "alpha-health-v1"
    window_trades: int = Field(default=100, ge=2)
    minimum_trades: int = Field(default=20, ge=1)
    minimum_multiplier: float = Field(default=0, ge=0, le=1)
    initial_multiplier: float = Field(default=0.5, ge=0, le=1)
    maximum_multiplier: float = Field(default=1, ge=0, le=1)
    max_increase: float = Field(default=0.05, gt=0, le=0.1)
    increase_interval_seconds: int = Field(default=86400, ge=3600)
    max_return_drawdown: float = Field(default=0.05, gt=0)
    health_max_age_seconds: int = Field(default=86400, gt=0)

    @model_validator(mode="after")
    def bounds(self) -> "AlphaHealthSettings":
        if (
            not self.minimum_multiplier <= self.initial_multiplier <= self.maximum_multiplier
            or self.minimum_trades > self.window_trades
        ):
            raise ValueError("invalid health bounds or warm-up")
        return self


class AlphaHealthMultiplier(FeatureModel):
    value: float
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def bounded(self) -> "AlphaHealthMultiplier":
        if not 0 <= self.minimum <= self.value <= self.maximum <= 1:
            raise ValueError("health multiplier outside configured bounds")
        return self


class TradeOutcome(FeatureModel):
    trade_id: UUID
    alpha_id: AlphaId
    closed_at: datetime
    received_at: datetime
    gross_pnl: Decimal
    costs: Decimal = Field(ge=0)
    slippage_cost: Decimal = Field(ge=0)
    gross_capital: Decimal = Field(gt=0)
    regime: MarketState

    _aware = field_validator("closed_at", "received_at")(utc)

    @model_validator(mode="after")
    def time_order(self) -> "TradeOutcome":
        if self.closed_at > self.received_at:
            raise ValueError("trade receipt precedes close")
        return self


class RegimeCount(FeatureModel):
    regime: MarketState
    trades: int = Field(ge=0)


class AlphaHealthSnapshot(FeatureModel):
    alpha_id: AlphaId
    timestamp: datetime
    trades: int
    net_expectancy: float | None
    net_pnl: Decimal
    drawdown: float
    costs: Decimal
    slippage_cost: Decimal
    regimes: tuple[RegimeCount, ...]
    multiplier: AlphaHealthMultiplier
    last_increase_at: datetime
    last_trade_at: datetime | None
    settings: AlphaHealthSettings
    outcomes_hash: str
    prior_hash: str | None
    code_commit: str
    config_hash: str
    data_snapshot: str

    _aware = field_validator("timestamp", "last_increase_at")(utc)

    def snapshot_hash(self) -> str:
        return content_hash(self)

    @model_validator(mode="after")
    def coherent(self) -> "AlphaHealthSnapshot":
        if (
            self.last_increase_at > self.timestamp
            or (self.last_trade_at is not None and utc(self.last_trade_at) > self.timestamp)
            or self.multiplier.minimum != self.settings.minimum_multiplier
            or self.multiplier.maximum != self.settings.maximum_multiplier
            or sum(r.trades for r in self.regimes) != self.trades
        ):
            raise ValueError("inconsistent alpha health snapshot")
        return self


class CandidateLeg(FeatureModel):
    entity_id: UUID
    instrument_id: str
    sector: str
    signed_weight: Decimal


class NormalizedCandidate(FeatureModel):
    signal_id: UUID
    alpha_id: AlphaId
    timestamp: datetime
    strength: Decimal = Field(ge=0, le=1)
    score_semantics: Literal["ORDINAL_STRENGTH_NOT_PROBABILITY"] = (
        "ORDINAL_STRENGTH_NOT_PROBABILITY"
    )
    legs: tuple[CandidateLeg, ...] = Field(min_length=1)
    raw_intent_hash: str
    registry_hash: str

    _aware = field_validator("timestamp")(utc)

    @model_validator(mode="after")
    def indivisible_weights(self) -> "NormalizedCandidate":
        if (
            len({x.entity_id for x in self.legs}) != len(self.legs)
            or any(x.signed_weight == 0 for x in self.legs)
            or abs(sum((abs(x.signed_weight) for x in self.legs), Decimal(0)) - 1)
            > Decimal("1e-12")
        ):
            raise ValueError("candidate weights must have unique legs and unit gross")
        return self
