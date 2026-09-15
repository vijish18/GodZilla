"""Research-only Alpha D settings, measured evidence and non-executable exit references."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from godzilla.features.models import FeatureModel, content_hash
from godzilla.features.vwap_reversion import ReversionFeatures, ReversionFeatureVersion
from godzilla.market_data.models import Quote, utc
from godzilla.market_state.models import MarketState

VWAP_ALPHA_ID = "vwap_mean_reversion"


class VwapSettings(FeatureModel):
    version: Literal["vwap-reversion-research-v1"] = "vwap-reversion-research-v1"
    enabled: bool = True
    live_enabled: Literal[False] = False
    research_regime_override: bool = False
    features: ReversionFeatureVersion = ReversionFeatureVersion()
    min_stretch: float = Field(default=2, gt=0)
    max_stretch: float = Field(default=6, gt=0)
    min_impulse: float = Field(default=0.5, gt=0)
    max_deceleration_ratio: float = Field(default=0.7, gt=0, lt=1)
    min_reversal: float = Field(default=0.1, gt=0)
    min_body: float = Field(default=0.05, gt=0)
    min_close_location: float = Field(default=0.65, ge=0.5, le=1)
    start_minutes: int = Field(default=75, ge=0)
    end_minutes: int = Field(default=300, ge=1)
    max_hold_minutes: int = Field(default=30, ge=1)
    convergence_fraction: float = Field(default=0.8, gt=0, le=1)
    adverse_scale: float = Field(default=1, gt=0)
    max_spread_bps: float = Field(default=12, gt=0)
    max_quote_age_seconds: float = Field(default=3, gt=0)
    max_bar_lag_seconds: float = Field(default=30, ge=0)
    liquidity_max_age_seconds: float = Field(default=86400, gt=0)
    min_adv: float = Field(default=100000, gt=0)
    min_turnover: float = Field(default=10000000, gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "VwapSettings":
        if self.min_stretch >= self.max_stretch or self.start_minutes >= self.end_minutes:
            raise ValueError("VWAP thresholds and entry window must be ordered")
        return self

    def version_hash(self) -> str:
        return content_hash(self)


class VwapLiquidity(FeatureModel):
    quote: Quote
    adv: float = Field(gt=0)
    daily_turnover: float = Field(gt=0)
    known_at: datetime
    snapshot_id: str = Field(min_length=1)

    _aware = field_validator("known_at")(utc)


class VwapExitMetadata(FeatureModel):
    convergence_price: float = Field(gt=0)
    adverse_price: float = Field(gt=0)
    time_stop: datetime
    entry_state: MarketState
    reasons: tuple[str, ...] = (
        "VWAP_CONVERGENCE",
        "TIME_STOP",
        "ADVERSE_CONTINUATION",
        "STATE_CHANGE",
    )
    reference_policy: Literal["FROZEN_ENTRY_VWAP_AND_SCALE"] = "FROZEN_ENTRY_VWAP_AND_SCALE"
    executable_order: Literal[False] = False

    _aware = field_validator("time_stop")(utc)


class VwapSignalEvidence(FeatureModel):
    timestamp: datetime
    instrument_id: str
    symbol: str
    source: str
    side: Literal["LONG", "SHORT"]
    score: float = Field(ge=0, le=1)
    features: ReversionFeatures
    exits: VwapExitMetadata
    settings: VwapSettings
    alpha_version_hash: str
    feature_set_hash: str
    universe_hash: str
    instrument_master_hash: str
    state_hash: str
    liquidity_hash: str
    code_commit: str
    config_hash: str
    data_snapshot: str

    _aware = field_validator("timestamp")(utc)

    @property
    def alpha_id(self) -> str:
        return VWAP_ALPHA_ID

    @model_validator(mode="after")
    def consistent(self) -> "VwapSignalEvidence":
        if (
            self.alpha_version_hash != self.settings.version_hash()
            or self.feature_set_hash != self.features.definition.version_hash()
            or self.features.definition != self.settings.features
            or self.instrument_id != self.features.instrument_id
            or self.source != self.features.source
            or self.features.timestamp > self.timestamp
            or self.exits.time_stop <= self.timestamp
        ):
            raise ValueError("VWAP evidence identity/version/timing mismatch")
        return self
