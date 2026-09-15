"""Explicit durable publication. Dispatch only happens after the journal commits."""

from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import EventEnvelope, MarketStateUpdate, Provenance
from godzilla.core.events.serialization import idempotency_key
from godzilla.market_state.models import StateDecision


def publish_state(
    journal: EventJournal,
    decision: StateDecision,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    """Retry with the identical receipt timestamp/correlation; never reconstruct a retry."""
    digest = decision.decision_hash()
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:market-state:" + digest),
        correlation_id=correlation_id,
        occurred_at=decision.timestamp,
        received_at=received_at,
        source="market-state-router",
        provenance=Provenance(
            code_commit=decision.code_commit,
            config_hash=decision.config_hash,
            data_snapshot=decision.data_snapshot,
            feature_version=decision.evidence.feature_set_hash,
        ),
        payload=MarketStateUpdate(
            state=decision.state.value, reasons=decision.evidence.reasons, decision=decision
        ),
    )
    return journal.publish(event, idempotency_key("market-state", digest), emit=emit)
