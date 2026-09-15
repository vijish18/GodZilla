"""Research-only pair identities, statistical evidence and indivisible two-leg intents."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from godzilla.features.models import FeatureModel, InstrumentRef, content_hash
from godzilla.market_data.models import Bar, Quote, utc

PAIRS_ALPHA_ID = "pairs_stat_arb"


class PairsSettings(FeatureModel):
    version: str = "pairs-research-v1"
    formula_revision: Literal["1"] = "1"
    enabled: bool = True
    live_enabled: Literal[False] = False
    training_bars: int = Field(default=250, ge=60)
    test_lags: int = Field(default=1, ge=0, le=10)
    significance: float = Field(default=0.05, gt=0, le=0.1)
    min_beta: float = Field(default=0.5, gt=0)
    max_beta: float = Field(default=2, gt=0)
    max_beta_drift: float = Field(default=0.25, gt=0)
    min_spread_std: float = Field(default=0.00001, gt=0)
    max_spread_std_ratio: float = Field(default=3, ge=1)
    max_spread_mean_shift: float = Field(default=1.5, gt=0)
    min_half_life_bars: float = Field(default=0.5, gt=0)
    max_half_life_bars: float = Field(default=30, gt=0)
    entry_z: float = Field(default=2, gt=0)
    exit_z: float = Field(default=0.5, ge=0)
    stop_z: float = Field(default=4, gt=0)
    max_hold_minutes: int = Field(default=60, ge=1)
    max_net_gross_fraction: float = Field(default=0.2, ge=0, lt=1)
    max_spread_bps: float = Field(default=12, gt=0)
    quote_max_age_seconds: float = Field(default=3, gt=0)
    max_bar_lag_seconds: float = Field(default=30, ge=0)
    min_adv: float = Field(default=100000, gt=0)
    min_turnover: float = Field(default=10000000, gt=0)
    liquidity_metadata_max_age_seconds: int = Field(default=86400, gt=0)
    max_leg_lag_seconds: float = Field(default=3, gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "PairsSettings":
        if not self.exit_z < self.entry_z < self.stop_z:
            raise ValueError("require exit_z < entry_z < stop_z")
        if self.min_beta >= self.max_beta or self.min_half_life_bars >= self.max_half_life_bars:
            raise ValueError("pair diagnostic limits must be ordered")
        return self

    def version_hash(self) -> str:
        return content_hash(self)


class PairCandidate(FeatureModel):
    pair_id: str = Field(min_length=1)
    left: InstrumentRef
    right: InstrumentRef
    economic_tags: tuple[str, ...] = Field(min_length=1)
    left_sector: str = Field(min_length=1)
    right_sector: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    known_at: datetime
    effective_from: datetime
    effective_to: datetime
    reviewed_at: datetime
    corporate_actions_clear: bool = False
    adjustment_snapshot: str = Field(min_length=1)

    _aware = field_validator("known_at", "effective_from", "effective_to", "reviewed_at")(utc)

    @model_validator(mode="after")
    def valid_identity(self) -> "PairCandidate":
        if (
            self.left.instrument_id == self.right.instrument_id
            or self.effective_from >= self.effective_to
        ):
            raise ValueError("pair requires distinct legs and an ordered effective window")
        return self


class PairObservation(FeatureModel):
    left: Bar
    right: Bar


class PairLiquidity(FeatureModel):
    quote: Quote
    adv: float = Field(gt=0)
    daily_turnover: float = Field(gt=0)
    known_at: datetime
    snapshot_id: str = Field(min_length=1)

    _aware = field_validator("known_at")(utc)


class StatisticalTests(FeatureModel):
    method_version: str
    cointegration_p: float = Field(ge=0, le=1)
    spread_adf_p: float = Field(ge=0, le=1)
    left_level_adf_p: float = Field(ge=0, le=1)
    right_level_adf_p: float = Field(ge=0, le=1)


class PairFit(FeatureModel):
    beta: float
    intercept: float
    spread_mean: float
    spread_std: float = Field(gt=0)
    half_life_bars: float | None
    split_beta_drift: float
    spread_std_ratio: float
    spread_mean_shift: float
    tests: StatisticalTests
    training_start: datetime
    training_end: datetime
    training_hash: str
    observations: int

    _aware = field_validator("training_start", "training_end")(utc)


class PairPosition(FeatureModel):
    """Research position context only, supplied explicitly; not a broker position."""

    position_id: UUID
    pair_id: str
    direction: Literal["LONG_SPREAD", "SHORT_SPREAD"]
    opened_at: datetime
    entry_fit: PairFit
    entry_z: float

    _aware = field_validator("opened_at")(utc)


class PairLeg(FeatureModel):
    instrument_id: str
    side: Literal["LONG", "SHORT"]
    gross_fraction: float = Field(gt=0, lt=1)


class CombinedRiskReference(FeatureModel):
    spread_mean: float
    spread_std: float = Field(gt=0)
    current_spread: float
    current_z: float
    adverse_boundary_z: float
    maximum_hold_until: datetime
    net_gross_fraction: float = Field(ge=0, lt=1)
    model: Literal["OLS_LOG_SPREAD_HEDGE_NOT_A_RISK_APPROVAL"] = (
        "OLS_LOG_SPREAD_HEDGE_NOT_A_RISK_APPROVAL"
    )

    _aware = field_validator("maximum_hold_until")(utc)


class PairedExecutionRequirements(FeatureModel):
    both_legs_required: Literal[True] = True
    combined_portfolio_and_risk_validation: Literal[True] = True
    broker_atomicity_assumed: Literal[False] = False
    reconcile_before_ambiguous_retry: Literal[True] = True
    partial_fill_creates_exposure: Literal[True] = True
    hedge_failure_action: Literal["HEDGE_EMERGENCY_RECONCILE_AND_REDUCE"] = (
        "HEDGE_EMERGENCY_RECONCILE_AND_REDUCE"
    )
    max_leg_lag_seconds: float = Field(gt=0)


class PairSignalIntent(FeatureModel):
    kind: Literal["PAIR_SIGNAL_INTENT"] = "PAIR_SIGNAL_INTENT"
    signal_id: UUID
    alpha_id: Literal["pairs_stat_arb"] = "pairs_stat_arb"
    timestamp: datetime
    pair_id: str
    action: Literal["ENTER", "EXIT"]
    direction: Literal["LONG_SPREAD", "SHORT_SPREAD"]
    legs: tuple[PairLeg, PairLeg]
    hedge_ratio: float = Field(gt=0)
    risk_reference: CombinedRiskReference
    execution: PairedExecutionRequirements
    reasons: tuple[str, ...]
    fit: PairFit
    settings: PairsSettings
    candidate: PairCandidate
    input_hash: str
    universe_hash: str
    state_hash: str
    code_commit: str
    config_hash: str
    data_snapshot: str

    _aware = field_validator("timestamp")(utc)

    @model_validator(mode="after")
    def coupled(self) -> "PairSignalIntent":
        import math

        left, right = self.legs
        entry_left = "LONG" if self.direction == "LONG_SPREAD" else "SHORT"
        expected_left = (
            entry_left if self.action == "ENTER" else ("SHORT" if entry_left == "LONG" else "LONG")
        )
        if (
            left.instrument_id != self.candidate.left.instrument_id
            or right.instrument_id != self.candidate.right.instrument_id
            or left.side != expected_left
            or left.side == right.side
            or not math.isclose(left.gross_fraction + right.gross_fraction, 1)
            or not math.isclose(right.gross_fraction / left.gross_fraction, self.hedge_ratio)
            or self.fit.beta != self.hedge_ratio
            or self.pair_id != self.candidate.pair_id
        ):
            raise ValueError("pair legs must match the indivisible hedge intent")
        return self


class PairEvaluation(FeatureModel):
    timestamp: datetime
    pair_id: str
    intent: PairSignalIntent | None = None
    fit: PairFit | None = None
    zscore: float | None = None
    accepted_relationship: bool = False
    reasons: tuple[str, ...]
    open_risk_attention: bool = False
    input_hash: str
    settings: PairsSettings
    code_commit: str
    data_snapshot: str

    _aware = field_validator("timestamp")(utc)
