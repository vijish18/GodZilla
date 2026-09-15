"""Frozen schema-v1 events; payloads contain facts, never executable strategy logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from godzilla.config.modes import SystemState, TradingMode
from godzilla.market_data.models import Bar, Quote, utc


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Provenance(FrozenModel):
    code_commit: str = Field(min_length=1)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_snapshot: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)


class BarClose(FrozenModel):
    kind: Literal["BAR_CLOSE"] = "BAR_CLOSE"
    bar: Bar


class QuoteUpdate(FrozenModel):
    kind: Literal["QUOTE_UPDATE"] = "QUOTE_UPDATE"
    quote: Quote


class NamedValue(FrozenModel):
    name: str = Field(min_length=1)
    value: Decimal


class FeatureUpdate(FrozenModel):
    kind: Literal["FEATURE_UPDATE"] = "FEATURE_UPDATE"
    entity: str
    feature_version: str
    values: tuple[NamedValue, ...]


class MarketStateUpdate(FrozenModel):
    kind: Literal["MARKET_STATE_UPDATE"] = "MARKET_STATE_UPDATE"
    state: str
    reasons: tuple[str, ...]


class SignalIntent(FrozenModel):
    kind: Literal["SIGNAL_INTENT"] = "SIGNAL_INTENT"
    signal_id: UUID
    instrument_id: str
    alpha_id: str
    side: Literal["LONG", "SHORT"]
    reasons: tuple[str, ...]


class Decision(FrozenModel):
    kind: Literal["ENSEMBLE_DECISION", "PORTFOLIO_DECISION", "RISK_DECISION"]
    decision_id: UUID
    intent_id: UUID
    accepted: bool
    reasons: tuple[str, ...]


class OrderEvent(FrozenModel):
    kind: Literal[
        "ORDER_PLAN_CREATED",
        "ORDER_SUBMITTED",
        "ORDER_ACKNOWLEDGED",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "ORDER_UNKNOWN",
        "ORDER_PARTIAL_FILL",
        "ORDER_FILLED",
    ]
    order_intent_id: UUID
    broker_order_id: str | None = None
    reason: str


class Fill(FrozenModel):
    kind: Literal["FILL"] = "FILL"
    fill_id: UUID
    order_intent_id: UUID
    broker_order_id: str
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)


class PositionUpdate(FrozenModel):
    kind: Literal["POSITION_UPDATE"] = "POSITION_UPDATE"
    instrument_id: str
    quantity: Decimal
    reason: str


class Reconciliation(FrozenModel):
    kind: Literal["RECONCILIATION"] = "RECONCILIATION"
    scope: str
    matched: bool
    reasons: tuple[str, ...]


class Health(FrozenModel):
    kind: Literal["HEALTH"] = "HEALTH"
    component: str
    status: Literal["HEALTHY", "UNKNOWN", "DEGRADED", "UNHEALTHY"]
    blocking: bool
    reason: str


Payload = Annotated[
    BarClose
    | QuoteUpdate
    | FeatureUpdate
    | MarketStateUpdate
    | SignalIntent
    | Decision
    | OrderEvent
    | Fill
    | PositionUpdate
    | Reconciliation
    | Health,
    Field(discriminator="kind"),
]


class EventEnvelope(FrozenModel):
    event_id: UUID
    correlation_id: UUID
    occurred_at: datetime
    received_at: datetime
    source: str = Field(min_length=1)
    schema_version: Literal[1] = 1
    provenance: Provenance
    payload: Payload

    _aware_times = field_validator("occurred_at", "received_at")(utc)

    @model_validator(mode="after")
    def causal_times(self) -> EventEnvelope:
        if self.occurred_at > self.received_at:
            raise ValueError("event cannot occur after receipt")
        observation: Bar | Quote | None = None
        event_time = self.occurred_at
        if isinstance(self.payload, BarClose):
            observation = self.payload.bar
            event_time = observation.end
        elif isinstance(self.payload, QuoteUpdate):
            observation = self.payload.quote
            event_time = observation.timestamp
        if observation is not None and (
            event_time != self.occurred_at or observation.received_at > self.received_at
        ):
            raise ValueError("envelope time disagrees with market observation")
        return self


class SystemStateSnapshot(FrozenModel):
    scope: str = Field(min_length=1)
    state: SystemState
    mode: TradingMode
    kill_switch: bool
    broker_health: str
    data_health: str
    release_id: str
    last_event_id: UUID
    updated_at: datetime

    _aware_time = field_validator("updated_at")(utc)
