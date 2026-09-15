"""Journal boundary for scored intents; no broker or order capability."""

from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import EventEnvelope, Provenance, SignalIntent
from godzilla.core.events.serialization import idempotency_key


def publish_intent(
    journal: EventJournal,
    intent: SignalIntent,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    if intent.evidence is None:
        raise ValueError("alpha publication requires scored evidence")
    evidence = intent.evidence
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:signal-event:" + str(intent.signal_id)),
        correlation_id=correlation_id,
        occurred_at=evidence.timestamp,
        received_at=received_at,
        source=intent.alpha_id,
        payload=intent,
        provenance=Provenance(
            code_commit=evidence.code_commit,
            config_hash=evidence.config_hash,
            data_snapshot=evidence.data_snapshot,
            feature_version=evidence.feature_set_hash,
        ),
    )
    return journal.publish(
        event, idempotency_key("signal-intent", str(intent.signal_id)), emit=emit
    )
