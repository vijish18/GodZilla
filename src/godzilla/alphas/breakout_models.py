"""Immutable Alpha B definitions, closed-bar evidence and invalidation metadata."""

import math
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from godzilla.alphas.momentum_models import MomentumObservation
from godzilla.features.models import FeatureModel, FeatureSetVersion, content_hash
from godzilla.market_data.models import Bar, utc
from godzilla.market_state.models import ThresholdEvidence

BREAKOUT_ALPHA_ID = "momentum_breakout"


class BreakoutWeights(FeatureModel):
    relative_strength: float = Field(default=0.25, ge=0, le=1)
    breakout_quality: float = Field(default=0.20, ge=0, le=1)
    relative_volume: float = Field(default=0.15, ge=0, le=1)
    sector_confirmation: float = Field(default=0.15, ge=0, le=1)
    slope_quality: float = Field(default=0.10, ge=0, le=1)
    reward_risk_proxy: float = Field(default=0.10, ge=0, le=1)
    liquidity_quality: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def normalized(self) -> "BreakoutWeights":
        if not math.isclose(sum(self.model_dump().values()), 1, abs_tol=1e-12):
            raise ValueError("breakout weights must sum to one")
        return self


class BreakoutSettings(FeatureModel):
    version: str = "breakout-research-v1"
    formula_revision: Literal["1"] = "1"
    enabled: bool = True
    weights: BreakoutWeights = BreakoutWeights()
    range_source: Literal["opening_range", "swing", "either"] = "either"
    minimum_universe_size: int = Field(default=10, ge=2)
    minimum_strength_percentile: float = Field(default=0.8, gt=0.5, lt=1)
    minimum_residual_return: float = Field(default=0.002, gt=0)
    minimum_relative_volume: float = Field(default=1.5, gt=0)
    full_relative_volume: float = Field(default=3, gt=0)
    min_extension_atr: float = Field(default=0.05, gt=0)
    full_extension_atr: float = Field(default=0.25, gt=0)
    max_extension_atr: float = Field(default=0.75, gt=0)
    min_close_location: float = Field(default=0.7, ge=0.5, le=1)
    min_body_fraction: float = Field(default=0.2, ge=0, le=1)
    full_sector_distance: float = Field(default=0.005, gt=0)
    full_slope: float = Field(default=0.001, gt=0)
    invalidation_buffer_atr: float = Field(default=0.1, gt=0)
    observation_bars: int = Field(default=3, ge=1)
    reward_distance_atr: float = Field(default=2, gt=0)
    min_reward_risk_proxy: float = Field(default=1.5, gt=0)
    full_reward_risk_proxy: float = Field(default=3, gt=0)
    max_spread_bps: float = Field(default=12, gt=0)
    max_quote_age_seconds: float = Field(default=3, gt=0)
    min_adv: float = Field(default=100000, gt=0)
    min_daily_turnover: float = Field(default=10000000, gt=0)
    max_bar_lag_seconds: float = Field(default=30, ge=0)
    minimum_score: float = Field(default=0.6, ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "BreakoutSettings":
        if not self.min_extension_atr <= self.full_extension_atr <= self.max_extension_atr:
            raise ValueError("breakout extension thresholds must be ordered")
        if (
            self.minimum_relative_volume > self.full_relative_volume
            or self.min_reward_risk_proxy > self.full_reward_risk_proxy
        ):
            raise ValueError("full-score thresholds must cover minimum thresholds")
        return self

    def version_hash(self) -> str:
        return content_hash(self)


class BreakoutObservation(FeatureModel):
    current: MomentumObservation
    reference: MomentumObservation
    bar: Bar
    previous_bar: Bar
    feature_definition: FeatureSetVersion


class BreakoutComponent(FeatureModel):
    name: str
    raw_value: float
    normalized_value: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    contribution: float


class BreakoutInvalidation(FeatureModel):
    range_kind: Literal["opening_range", "swing"]
    broken_level: float = Field(gt=0)
    invalidation_level: float = Field(gt=0)
    level_known_at: datetime
    trigger_bar_end: datetime
    expires_at: datetime
    rule: Literal["SUBSEQUENT_CLOSE_BACK_THROUGH_LEVEL"] = "SUBSEQUENT_CLOSE_BACK_THROUGH_LEVEL"

    _aware = field_validator("level_known_at", "trigger_bar_end", "expires_at")(utc)


class BreakoutSignalEvidence(FeatureModel):
    timestamp: datetime
    instrument_id: str
    symbol: str
    source: str
    side: Literal["LONG", "SHORT"]
    score: float = Field(ge=0, le=1)
    components: tuple[BreakoutComponent, ...]
    checks: tuple[ThresholdEvidence, ...]
    invalidation: BreakoutInvalidation
    settings: BreakoutSettings
    alpha_version_hash: str
    observation_hash: str
    cross_section_hash: str
    universe_hash: str
    instrument_master_hash: str
    state_hash: str
    code_commit: str
    config_hash: str
    data_snapshot: str
    feature_set_hash: str

    _aware = field_validator("timestamp")(utc)

    @property
    def alpha_id(self) -> str:
        return BREAKOUT_ALPHA_ID

    @model_validator(mode="after")
    def consistent(self) -> "BreakoutSignalEvidence":
        if (
            self.alpha_version_hash != self.settings.version_hash()
            or not math.isclose(
                self.score, sum(c.contribution for c in self.components), abs_tol=1e-12
            )
            or not self.invalidation.level_known_at
            < self.invalidation.trigger_bar_end
            <= self.timestamp
            or self.invalidation.expires_at <= self.timestamp
        ):
            raise ValueError("breakout evidence score, version or timing mismatch")
        return self


class FailedBreakoutObservation(FeatureModel):
    status: Literal["UNAVAILABLE", "ACTIVE", "FAILED", "EXPIRED"]
    reason: str
    observed_at: datetime

    _aware = field_validator("observed_at")(utc)
