"""Research-only sector baskets with explicit modeled exposure and stable security identity."""

import math
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, field_validator, model_validator

from godzilla.alphas.momentum_models import MomentumObservation
from godzilla.alphas.pairs_models import PairedExecutionRequirements
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.instruments import Instrument
from godzilla.market_data.models import Bar, utc

RV_ALPHA_ID = "sector_relative_value"


def security_id(instrument: Instrument) -> UUID:
    """Stable across vendor tokens/symbols; listing segment is deliberately part of identity."""
    return uuid5(
        NAMESPACE_URL,
        "godzilla:security:"
        + ":".join(
            (
                instrument.exchange.strip().upper(),
                instrument.segment.strip().upper(),
                instrument.isin.strip().upper(),
            )
        ),
    )


class RelativeValueSettings(FeatureModel):
    version: Literal["sector-rv-research-v1"] = "sector-rv-research-v1"
    enabled: bool = True
    live_enabled: Literal[False] = False
    construction: Literal["PAIR", "BASKET"] = "PAIR"
    members_per_side: int = Field(default=2, ge=1, le=5)
    minimum_sector_size: int = Field(default=4, ge=2)
    tail_fraction: float = Field(default=0.25, gt=0, lt=0.5)
    sector_residual_weight: float = Field(default=0.5, ge=0, le=1)
    min_residual_gap: float = Field(default=0.002, gt=0)
    training_returns: int = Field(default=24, ge=8)
    min_beta: float = Field(default=0.2, gt=0)
    max_beta: float = Field(default=3, gt=0)
    max_split_beta_drift: float = Field(default=0.5, gt=0)
    max_net_gross: float = Field(default=0.15, ge=0, lt=1)
    max_beta_gross: float = Field(default=0.02, ge=0, lt=1)
    max_inventory_age_seconds: float = Field(default=30, gt=0)
    max_spread_bps: float = Field(default=12, gt=0)
    max_quote_age_seconds: float = Field(default=3, gt=0)
    min_adv: float = Field(default=100000, gt=0)
    min_turnover: float = Field(default=10000000, gt=0)
    max_leg_lag_seconds: float = Field(default=3, gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "RelativeValueSettings":
        if self.min_beta >= self.max_beta:
            raise ValueError("beta limits must be ordered")
        return self

    def version_hash(self) -> str:
        return content_hash(self)


class BetaObservation(FeatureModel):
    stock: Bar
    market: Bar


class RelativeValueObservation(FeatureModel):
    current: MomentumObservation
    history: tuple[BetaObservation, ...]


class BetaEstimate(FeatureModel):
    method: Literal["OLS_SIMPLE_RETURNS_INTERCEPT_V1"] = "OLS_SIMPLE_RETURNS_INTERCEPT_V1"
    beta: float
    intercept: float
    split_drift: float = Field(ge=0)
    training_start: datetime
    training_end: datetime
    returns: int = Field(ge=8)
    input_hash: str
    market_hash: str

    _aware = field_validator("training_start", "training_end")(utc)


class ReservedSecurity(FeatureModel):
    entity_id: UUID
    owner_alpha: Literal["pairs_stat_arb", "sector_relative_value"]
    reference_id: str = Field(min_length=1)


class RelativeValueInventory(FeatureModel):
    snapshot_id: str = Field(min_length=1)
    observed_at: datetime
    complete: bool = False
    reservations: tuple[ReservedSecurity, ...] = ()

    _aware = field_validator("observed_at")(utc)


class RelativeValueLeg(FeatureModel):
    entity_id: UUID
    instrument_id: str
    symbol: str
    sector: str
    side: Literal["LONG", "SHORT"]
    gross_fraction: float = Field(gt=0, lt=1)
    residual_strength: float
    percentile: float = Field(ge=0, le=1)
    beta: BetaEstimate
    feature_hash: str


class RelativeValueRiskReference(FeatureModel):
    signed_net_gross: float
    signed_beta_gross: float
    long_beta: float = Field(gt=0)
    short_beta: float = Field(gt=0)
    flatten_by: datetime
    model: Literal["ESTIMATED_NIFTY_BETA_NOT_RISK_APPROVAL"] = (
        "ESTIMATED_NIFTY_BETA_NOT_RISK_APPROVAL"
    )

    _aware = field_validator("flatten_by")(utc)


class RelativeValueIntent(FeatureModel):
    kind: Literal["RELATIVE_VALUE_INTENT"] = "RELATIVE_VALUE_INTENT"
    alpha_id: Literal["sector_relative_value"] = "sector_relative_value"
    signal_id: UUID
    entity_id: UUID
    timestamp: datetime
    sector: str
    legs: tuple[RelativeValueLeg, ...] = Field(min_length=2)
    hedge_ratio: float = Field(gt=0)
    risk_reference: RelativeValueRiskReference
    execution: PairedExecutionRequirements = PairedExecutionRequirements(max_leg_lag_seconds=3)
    reasons: tuple[str, ...]
    settings: RelativeValueSettings
    universe_hash: str
    master_hash: str
    inventory_hash: str
    state_hash: str
    cross_section_hash: str
    code_commit: str
    config_hash: str
    data_snapshot: str
    feature_set_hash: str

    _aware = field_validator("timestamp")(utc)

    @property
    def score(self) -> float:
        """Descriptive rank separation, not confidence or capital allocation."""
        longs = tuple(x.percentile for x in self.legs if x.side == "LONG")
        shorts = tuple(x.percentile for x in self.legs if x.side == "SHORT")
        return sum(longs) / len(longs) - sum(shorts) / len(shorts)

    @model_validator(mode="after")
    def coherent_exposure(self) -> "RelativeValueIntent":
        longs = tuple(x for x in self.legs if x.side == "LONG")
        shorts = tuple(x for x in self.legs if x.side == "SHORT")
        gross = sum(x.gross_fraction for x in self.legs)
        net = sum(x.gross_fraction * (1 if x.side == "LONG" else -1) for x in self.legs)
        beta = sum(
            x.gross_fraction * x.beta.beta * (1 if x.side == "LONG" else -1) for x in self.legs
        )
        if not longs or not shorts:
            raise ValueError("relative value requires both sides")
        long_gross, short_gross = (
            sum(x.gross_fraction for x in longs),
            sum(x.gross_fraction for x in shorts),
        )
        n = 1 if self.settings.construction == "PAIR" else self.settings.members_per_side
        if (
            len({x.entity_id for x in self.legs}) != len(self.legs)
            or len(longs) != n
            or len(shorts) != n
            or not 0 <= self.score <= 1
            or not math.isclose(
                sum(x.gross_fraction * x.beta.beta for x in longs) / long_gross,
                self.risk_reference.long_beta,
            )
            or not math.isclose(
                sum(x.gross_fraction * x.beta.beta for x in shorts) / short_gross,
                self.risk_reference.short_beta,
            )
            or self.execution.max_leg_lag_seconds != self.settings.max_leg_lag_seconds
            or any(
                x.sector != self.sector or x.beta.training_end >= self.timestamp for x in self.legs
            )
            or not math.isclose(gross, 1, abs_tol=1e-12)
            or not math.isclose(short_gross / long_gross, self.hedge_ratio)
            or not math.isclose(net, self.risk_reference.signed_net_gross, abs_tol=1e-12)
            or not math.isclose(beta, self.risk_reference.signed_beta_gross, abs_tol=1e-12)
            or abs(net) > self.settings.max_net_gross + 1e-12
            or abs(beta) > self.settings.max_beta_gross + 1e-12
            or self.risk_reference.flatten_by <= self.timestamp
        ):
            raise ValueError("relative value identity/exposure/timing mismatch")
        return self
