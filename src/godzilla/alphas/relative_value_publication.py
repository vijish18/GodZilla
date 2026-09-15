"""Atomic basket publication through the existing durable journal, never broker execution."""

from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.alphas.relative_value_models import RelativeValueIntent
from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import EventEnvelope, Provenance
from godzilla.core.events.serialization import idempotency_key


def publish_relative_value(
    journal: EventJournal,
    intent: RelativeValueIntent,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:rv-event:" + str(intent.signal_id)),
        correlation_id=correlation_id,
        occurred_at=intent.timestamp,
        received_at=received_at,
        source=intent.alpha_id,
        payload=intent,
        provenance=Provenance(
            code_commit=intent.code_commit,
            config_hash=intent.config_hash,
            data_snapshot=intent.data_snapshot,
            feature_version=intent.feature_set_hash,
        ),
    )
    return journal.publish(event, idempotency_key("rv-intent", str(intent.signal_id)), emit=emit)
