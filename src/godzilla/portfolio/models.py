"""Equity-fraction allocation inputs and immutable proposed targets."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from godzilla.ensemble.models import AlphaId, NormalizedCandidate
from godzilla.features.models import FeatureModel
from godzilla.market_data.models import utc


class AllocatorSettings(FeatureModel):
    version: Literal["portfolio-research-v1"] = "portfolio-research-v1"
    max_alpha: Decimal = Field(default=Decimal("0.4"), gt=0, le=1)
    max_gross: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    max_net: Decimal = Field(default=Decimal("0.6"), ge=0, le=1)
    max_symbol: Decimal = Field(default=Decimal("0.15"), gt=0, le=1)
    max_sector: Decimal = Field(default=Decimal("0.4"), gt=0, le=1)
    max_open_risk: Decimal = Field(default=Decimal("0.01"), gt=0, le=1)
    max_candidate_risk: Decimal = Field(default=Decimal("0.0025"), gt=0, le=1)
    max_groups: int = Field(default=4, ge=1)
    max_sector_groups: int = Field(default=2, ge=1)
    max_adv_participation: Decimal = Field(default=Decimal("0.0025"), gt=0, le=1)
    max_turnover_participation: Decimal = Field(default=Decimal("0.0025"), gt=0, le=1)
    max_input_age_seconds: int = Field(default=30, gt=0)
    correlation_threshold: Decimal = Field(default=Decimal("0.7"), ge=0, lt=1)
    correlation_penalty: Decimal = Field(default=Decimal("1"), ge=0)
    sector_duplication_penalty: Decimal = Field(default=Decimal("0.5"), ge=0)

    @model_validator(mode="after")
    def limits(self) -> "AllocatorSettings":
        if self.max_net > self.max_gross or self.max_sector_groups > self.max_groups:
            raise ValueError("allocator limits inconsistent")
        return self


class TargetPosition(FeatureModel):
    entity_id: UUID
    instrument_id: str
    sector: str
    alpha_id: AlphaId
    group_id: UUID
    signed_notional_fraction: Decimal
    open_risk_fraction: Decimal = Field(ge=0)
    origin: Literal["EXISTING_OR_PENDING", "PROPOSED"]
    independent_risk_required: Literal[True] = True


class PortfolioSnapshot(FeatureModel):
    snapshot_id: str
    timestamp: datetime
    equity: Decimal = Field(gt=0)
    complete: bool = False
    blocks_new_entries: bool = True
    positions: tuple[TargetPosition, ...] = ()

    _aware = field_validator("timestamp")(utc)

    @model_validator(mode="after")
    def unique_commitments(self) -> "PortfolioSnapshot":
        if len({(p.group_id, p.entity_id) for p in self.positions}) != len(self.positions):
            raise ValueError("duplicate existing/pending commitment")
        return self


class LiquidityCapacity(FeatureModel):
    entity_id: UUID
    timestamp: datetime
    valid_until: datetime
    price: Decimal = Field(gt=0)
    adv_shares: Decimal = Field(gt=0)
    daily_turnover: Decimal = Field(gt=0)
    max_notional: Decimal = Field(gt=0)
    source_snapshot: str
    eligible: bool = False

    _aware = field_validator("timestamp", "valid_until")(utc)


class CandidateRiskEstimate(FeatureModel):
    signal_id: UUID
    timestamp: datetime
    loss_per_gross: Decimal = Field(gt=0, le=1)
    volatility_penalty: Decimal = Field(default=Decimal(1), ge=1)
    source_snapshot: str

    _aware = field_validator("timestamp")(utc)


class CorrelationEstimate(FeatureModel):
    left: UUID
    right: UUID
    value: Decimal = Field(ge=-1, le=1)


class CorrelationSnapshot(FeatureModel):
    timestamp: datetime
    training_end: datetime
    estimates: tuple[CorrelationEstimate, ...]
    source_snapshot: str

    _aware = field_validator("timestamp", "training_end")(utc)


class AllocationRecord(FeatureModel):
    signal_id: UUID
    allocated_gross: Decimal = Field(ge=0)
    health_multiplier: Decimal = Field(ge=0, le=1)
    penalty: Decimal = Field(ge=1)
    reasons: tuple[str, ...]


class PortfolioDecision(FeatureModel):
    decision_id: UUID
    timestamp: datetime
    targets: tuple[TargetPosition, ...]
    allocations: tuple[AllocationRecord, ...]
    reasons: tuple[str, ...]
    input_hash: str
    settings: AllocatorSettings
    independent_risk_required: Literal[True] = True
    execution_authorized: Literal[False] = False

    _aware = field_validator("timestamp")(utc)


class EnsembleRejection(FeatureModel):
    signal_id: UUID
    reasons: tuple[str, ...]


class EnsembleDecision(FeatureModel):
    decision_id: UUID
    timestamp: datetime
    candidates: tuple[NormalizedCandidate, ...]
    rejected: tuple[UUID, ...]
    reasons: tuple[str, ...]
    input_hash: str
    rejection_reasons: tuple[EnsembleRejection, ...] = ()

    _aware = field_validator("timestamp")(utc)
