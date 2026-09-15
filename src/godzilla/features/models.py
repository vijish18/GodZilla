"""Immutable inputs, explicit missingness and content-addressed feature snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from godzilla.market_data.models import utc


def content_hash(value: BaseModel) -> str:
    return hashlib.sha256(
        json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FeatureModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class MissingReason(StrEnum):
    WARMUP = "WARMUP"
    MISSING_INPUT = "MISSING_INPUT"
    INVALID_INPUT = "INVALID_INPUT"
    STALE = "STALE"
    ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class FeatureValue(FeatureModel):
    name: str
    value: float | None
    reason: MissingReason | None = None

    @model_validator(mode="after")
    def explicit_missingness(self) -> FeatureValue:
        if (self.value is None) != (self.reason is not None):
            raise ValueError("missing values require exactly one missing reason")
        return self


class FeatureSetVersion(FeatureModel):
    name: str = "godzilla-causal-v1"
    formula_revision: Literal["1"] = "1"
    opening_range_bars: int = Field(default=3, ge=1)
    atr_bars: int = Field(default=14, ge=2)
    statistics_bars: int = Field(default=20, ge=2)
    baseline_sessions: int = Field(default=20, ge=1)
    swing_confirmation_bars: int = Field(default=2, ge=1)
    quote_stale_seconds: float = Field(default=3, gt=0)
    spread_window_seconds: float = Field(default=300, gt=0)

    def version_hash(self) -> str:
        return content_hash(self)


class InstrumentRef(FeatureModel):
    instrument_id: str = Field(min_length=1)
    source: str = Field(min_length=1)


class FeatureContext(FeatureModel):
    entity: InstrumentRef
    nifty: InstrumentRef | None = None
    sector: InstrumentRef | None = None
    vix: InstrumentRef | None = None
    pair: InstrumentRef | None = None
    breadth_members: tuple[InstrumentRef, ...] = ()
    sector_members: tuple[InstrumentRef, ...] = ()
    universe_version: str = Field(min_length=1)
    known_at: datetime
    valid_from: datetime
    valid_to: datetime
    proposed_quantity: float | None = Field(default=None, ge=0)

    _aware = field_validator("known_at", "valid_from", "valid_to")(utc)

    @model_validator(mode="after")
    def valid_window(self) -> FeatureContext:
        if self.valid_from >= self.valid_to:
            raise ValueError("context effective window must be ordered")
        for members in (self.breadth_members, self.sector_members):
            if len(set(members)) != len(members):
                raise ValueError("breadth membership must be unique")
        return self


class FeatureSnapshot(FeatureModel):
    entity: str
    timestamp: datetime
    feature_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_commit: str = Field(min_length=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_snapshot: str = Field(min_length=1)
    values: tuple[FeatureValue, ...]

    _aware = field_validator("timestamp")(utc)

    @model_validator(mode="after")
    def ordered_unique_features(self) -> FeatureSnapshot:
        names = [item.name for item in self.values]
        if names != sorted(set(names)):
            raise ValueError("feature names must be unique and sorted")
        return self

    def snapshot_hash(self) -> str:
        return content_hash(self)

    def get(self, name: str) -> FeatureValue:
        return next(item for item in self.values if item.name == name)
