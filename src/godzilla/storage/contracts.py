"""Domain-facing repository ports; no ORM types cross this boundary."""

from typing import Protocol
from uuid import UUID

from godzilla.alphas.pairs_models import PairSignalIntent
from godzilla.alphas.relative_value_models import RelativeValueIntent
from godzilla.core.events.models import EventEnvelope, SignalIntent, SystemStateSnapshot
from godzilla.ensemble.models import AlphaHealthSnapshot
from godzilla.features.models import FeatureSnapshot
from godzilla.market_data.instruments import InstrumentMasterSnapshot
from godzilla.market_state.models import StateDecision
from godzilla.portfolio.models import EnsembleDecision, PortfolioDecision


class EventRepository(Protocol):
    def append(
        self, event: EventEnvelope, key: str, *, state: SystemStateSnapshot | None = None
    ) -> bool:
        """Atomically append event and derived projections; False means exact redelivery."""
        ...

    def read_events(self, after_sequence: int = 0) -> tuple[EventEnvelope, ...]: ...


class InstrumentRepository(Protocol):
    def save_master(self, snapshot: InstrumentMasterSnapshot) -> None: ...

    def get_master(self, snapshot_id: str) -> InstrumentMasterSnapshot | None: ...


class SystemStateRepository(Protocol):
    def get_state(self, scope: str) -> SystemStateSnapshot | None: ...


class FeatureRepository(Protocol):
    def get_feature_snapshot(self, snapshot_hash: str) -> FeatureSnapshot | None: ...


class MarketStateRepository(Protocol):
    def get_market_state(self, decision_hash: str) -> StateDecision | None: ...


class AlphaSignalRepository(Protocol):
    def get_alpha_signal(
        self, signal_id: UUID
    ) -> SignalIntent | PairSignalIntent | RelativeValueIntent | None: ...


class AllocationRepository(Protocol):
    def get_alpha_health(self, snapshot_hash: str) -> AlphaHealthSnapshot | None: ...
    def get_ensemble_decision(self, decision_id: UUID) -> EnsembleDecision | None: ...
    def get_portfolio_decision(self, decision_id: UUID) -> PortfolioDecision | None: ...
