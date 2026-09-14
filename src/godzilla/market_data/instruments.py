"""Versioned instrument master and broker-symbol mappings."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from godzilla.market_data.providers import load_yaml_model


class InstrumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    UNTRADABLE = "UNTRADABLE"


class TickRounding(StrEnum):
    NEAREST = "NEAREST"
    FLOOR = "FLOOR"
    CEILING = "CEILING"


class PriceBand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    lower: Decimal | None = None
    upper: Decimal | None = None
    category: str | None = None

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> PriceBand:
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError("price-band lower bound must be below upper bound")
        return self


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str = Field(min_length=1)
    token: str = Field(min_length=1)
    isin: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    segment: str = Field(min_length=1)
    tick_size: Decimal = Field(gt=0)
    status: InstrumentStatus
    fo_eligible: bool
    sector: str = Field(min_length=1)
    price_band: PriceBand
    surveillance: bool = False
    data_ambiguous: bool = False
    effective_from: date
    effective_to: date

    @model_validator(mode="after")
    def effective_dates_are_ordered(self) -> Instrument:
        if self.effective_from > self.effective_to:
            raise ValueError("instrument effective_from must not exceed effective_to")
        return self

    def active_on(self, day: date) -> bool:
        return self.effective_from <= day <= self.effective_to

    def round_price(
        self, price: Decimal, direction: TickRounding = TickRounding.NEAREST
    ) -> Decimal:
        rounding = {
            TickRounding.NEAREST: ROUND_HALF_UP,
            TickRounding.FLOOR: ROUND_FLOOR,
            TickRounding.CEILING: ROUND_CEILING,
        }[direction]
        ticks = (price / self.tick_size).quantize(Decimal("1"), rounding=rounding)
        return ticks * self.tick_size


class InstrumentMasterSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: str
    version: str
    exchange: str
    as_of: date
    generated_at: datetime
    source_reference: str
    instruments: tuple[Instrument, ...]


class BrokerSymbolMapping(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    broker: str
    exchange: str
    instrument_token: str
    broker_symbol: str
    effective_from: date
    effective_to: date


class BrokerSymbolMapper(Protocol):
    def resolve(self, broker: str, instrument_token: str, as_of: date) -> str | None: ...


class LocalBrokerSymbolMapper:
    def __init__(self, mappings: tuple[BrokerSymbolMapping, ...]) -> None:
        self._mappings = mappings

    @classmethod
    def from_yaml(cls, path: Path) -> LocalBrokerSymbolMapper:
        class MappingSnapshot(BaseModel):
            mappings: tuple[BrokerSymbolMapping, ...]

        return cls(load_yaml_model(path, MappingSnapshot).mappings)

    def resolve(self, broker: str, instrument_token: str, as_of: date) -> str | None:
        matches = [
            item.broker_symbol
            for item in self._mappings
            if item.broker == broker
            and item.instrument_token == instrument_token
            and item.effective_from <= as_of <= item.effective_to
        ]
        if len(matches) > 1:
            raise ValueError("ambiguous broker-symbol mapping")
        return matches[0] if matches else None


class InstrumentProvider(Protocol):
    def load(self) -> InstrumentMasterSnapshot: ...


class LocalInstrumentProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> InstrumentMasterSnapshot:
        return load_yaml_model(self.path, InstrumentMasterSnapshot)
