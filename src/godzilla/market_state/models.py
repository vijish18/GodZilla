"""Versioned routing rules and immutable audit evidence."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from godzilla.core.health import HealthStatus, SystemHealth
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.models import utc


class MarketState(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    CHOP = "CHOP"
    HIGH_VOL = "HIGH_VOL"
    OPEN_SHOCK = "OPEN_SHOCK"
    RISK_OFF = "RISK_OFF"


class AlphaFamily(StrEnum):
    CROSS_SECTIONAL_MOMENTUM = "cross_sectional_momentum"
    MOMENTUM_BREAKOUT = "momentum_breakout"
    PAIRS_STAT_ARB = "pairs_stat_arb"
    VWAP_MEAN_REVERSION = "vwap_mean_reversion"
    SECTOR_RELATIVE_VALUE = "sector_relative_value"


class RouterSettings(FeatureModel):
    version: str = "market-state-research-v1"
    formula_revision: Literal["1"] = "1"
    vwap_distance: float = Field(default=0.001, gt=0, lt=1)
    slope_15m: float = Field(default=0.0002, gt=0, lt=1)
    slope_30m: float = Field(default=0.0001, gt=0, lt=1)
    breadth_fraction: float = Field(default=0.6, gt=0.5, le=1)
    high_realized_vol: float = Field(default=0.005, gt=0)
    high_range_vol: float = Field(default=0.01, gt=0)
    high_vix: float = Field(default=25, gt=0)
    opening_gap: float = Field(default=0.015, gt=0, lt=1)
    stabilization_seconds: int = Field(default=900, ge=0)
    shock_window_seconds: int = Field(default=1800, gt=0)
    confirmation_count: int = Field(default=2, ge=1)
    min_duration_seconds: int = Field(default=600, ge=0)
    max_confirmation_gap_seconds: int = Field(default=600, gt=0)
    feature_max_age_seconds: int = Field(default=300, ge=0)
    health_max_age_seconds: int = Field(default=30, ge=0)
    flicker_window_seconds: int = Field(default=900, ge=0)
    trend_multiplier: float = Field(default=1, ge=0, le=1)
    chop_multiplier: float = Field(default=0.5, ge=0, le=1)
    high_vol_multiplier: float = Field(default=0.25, ge=0, le=1)

    @model_validator(mode="after")
    def ordered_limits(self) -> "RouterSettings":
        if self.shock_window_seconds < self.stabilization_seconds:
            raise ValueError("shock window must cover stabilization")
        if not self.high_vol_multiplier <= self.chop_multiplier <= self.trend_multiplier:
            raise ValueError("risk multipliers must decrease for chop and high volatility")
        return self

    def version_hash(self) -> str:
        return content_hash(self)


class RoutingHealth(FeatureModel):
    observed_at: datetime
    system_status: HealthStatus = HealthStatus.UNKNOWN
    risk_status: HealthStatus = HealthStatus.UNKNOWN
    blocks_new_entries: bool = True
    reasons: tuple[str, ...] = ()

    _aware = field_validator("observed_at")(utc)

    @classmethod
    def from_system(cls, system: SystemHealth, risk_status: HealthStatus) -> "RoutingHealth":
        if not system.components:
            raise ValueError("health requires timestamped component observations")
        return cls(
            observed_at=min(item.observed_at for item in system.components),
            system_status=system.status,
            risk_status=risk_status,
            blocks_new_entries=system.blocks_new_entries,
            reasons=tuple(item.component + ":" + item.message for item in system.components),
        )


class ThresholdEvidence(FeatureModel):
    name: str
    value: float | None
    operator: Literal["ge", "le", "eq", "abs_ge"]
    threshold: float
    passed: bool
    missing_reason: str | None = None


class MarketStateEvidence(FeatureModel):
    feature_snapshot_hash: str
    feature_set_hash: str
    router_version_hash: str
    thresholds: tuple[ThresholdEvidence, ...]
    health: RoutingHealth
    session_open: datetime | None
    reasons: tuple[str, ...]


class StateDecision(FeatureModel):
    timestamp: datetime
    state: MarketState
    candidate: MarketState
    previous_state: MarketState
    state_since: datetime
    confirmation_count: int
    allowed_alpha_families: tuple[AlphaFamily, ...]
    directional_sides: tuple[Literal["LONG", "SHORT"], ...]
    gross_risk_multiplier: float = Field(ge=0, le=1)
    evidence: MarketStateEvidence
    settings: RouterSettings
    code_commit: str
    config_hash: str
    data_snapshot: str
    prior_decision_hash: str | None

    _aware = field_validator("timestamp", "state_since")(utc)

    def decision_hash(self) -> str:
        return content_hash(self)

    @model_validator(mode="after")
    def consistent_policy(self) -> "StateDecision":
        if self.state_since > self.timestamp:
            raise ValueError("state start cannot be in the future")
        if self.evidence.router_version_hash != self.settings.version_hash():
            raise ValueError("router configuration hash mismatch")
        if self.state in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK) and (
            self.allowed_alpha_families or self.directional_sides or self.gross_risk_multiplier != 0
        ):
            raise ValueError("blocked state cannot grant risk or alpha permissions")
        return self
