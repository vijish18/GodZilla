"""Versioned cross-sectional momentum configuration and reproducible score evidence."""

import math
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from godzilla.features.models import FeatureContext, FeatureModel, FeatureSnapshot, content_hash
from godzilla.market_data.models import utc

ALPHA_ID = "cross_sectional_momentum"


class MomentumWeights(FeatureModel):
    return_30m: float = Field(default=0.20, ge=0, le=1)
    return_60m: float = Field(default=0.20, ge=0, le=1)
    return_120m: float = Field(default=0.20, ge=0, le=1)
    relative_sector_30m: float = Field(default=0.15, ge=0, le=1)
    relative_market_30m: float = Field(default=0.15, ge=0, le=1)
    trend_quality: float = Field(default=0.05, ge=0, le=1)
    liquidity_quality: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def normalized(self) -> "MomentumWeights":
        if not math.isclose(sum(self.model_dump().values()), 1, abs_tol=1e-12):
            raise ValueError("momentum weights must sum to one")
        if self.liquidity_quality == 1:
            raise ValueError("directional ranking weight must be positive")
        return self


class MomentumSettings(FeatureModel):
    version: str = "momentum-research-v1"
    formula_revision: Literal["1"] = "1"
    weights: MomentumWeights = MomentumWeights()
    exclude_recent_5m: bool = False
    enabled: bool = True
    long_tail_fraction: float = Field(default=0.2, gt=0, lt=0.5)
    short_tail_fraction: float = Field(default=0.2, gt=0, lt=0.5)
    max_candidates_per_side: int = Field(default=5, ge=1)
    minimum_universe_size: int = Field(default=10, ge=2)
    max_spread_bps: float = Field(default=12, gt=0)
    max_quote_age_seconds: float = Field(default=3, gt=0)
    min_adv: float = Field(default=100000, gt=0)
    min_daily_turnover: float = Field(default=10000000, gt=0)

    def version_hash(self) -> str:
        return content_hash(self)


class MomentumObservation(FeatureModel):
    snapshot: FeatureSnapshot
    context: FeatureContext


class ScoreComponent(FeatureModel):
    name: str
    raw_value: float
    rank_value: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    long_contribution: float
    short_contribution: float


class MomentumScore(FeatureModel):
    instrument_id: str
    symbol: str
    sector: str
    strength: float = Field(ge=0, le=1)
    percentile: float = Field(ge=0, le=1)
    long_score: float = Field(ge=0, le=1)
    short_score: float = Field(ge=0, le=1)
    components: tuple[ScoreComponent, ...]
    long_confirmed: bool
    short_confirmed: bool
    liquidity_confirmed: bool
    reasons: tuple[str, ...]
    feature_hash: str


class MomentumSignalEvidence(FeatureModel):
    timestamp: datetime
    side: Literal["LONG", "SHORT"]
    score: float = Field(ge=0, le=1)
    ranked: MomentumScore
    settings: MomentumSettings
    alpha_version_hash: str
    universe_hash: str
    instrument_master_hash: str
    cross_section_hash: str
    state_hash: str
    code_commit: str
    config_hash: str
    data_snapshot: str
    feature_set_hash: str

    _aware = field_validator("timestamp")(utc)

    @property
    def alpha_id(self) -> str:
        return ALPHA_ID

    @property
    def instrument_id(self) -> str:
        return self.ranked.instrument_id

    @property
    def symbol(self) -> str:
        return self.ranked.symbol

    @model_validator(mode="after")
    def consistent(self) -> "MomentumSignalEvidence":
        expected = self.ranked.long_score if self.side == "LONG" else self.ranked.short_score
        if self.score != expected or self.alpha_version_hash != self.settings.version_hash():
            raise ValueError("signal score/configuration mismatch")
        return self
