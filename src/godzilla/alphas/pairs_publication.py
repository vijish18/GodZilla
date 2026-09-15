"""One durable event for both legs; never two independently dispatchable signals."""

from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.alphas.pairs_models import PairSignalIntent
from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import EventEnvelope, Provenance
from godzilla.core.events.serialization import idempotency_key


def publish_pair_intent(
    journal: EventJournal,
    intent: PairSignalIntent,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:pair-event:" + str(intent.signal_id)),
        correlation_id=correlation_id,
        occurred_at=intent.timestamp,
        received_at=received_at,
        source=intent.alpha_id,
        payload=intent,
        provenance=Provenance(
            code_commit=intent.code_commit,
            config_hash=intent.config_hash,
            data_snapshot=intent.data_snapshot,
            feature_version="pairs-fit-v1",
        ),
    )
    return journal.publish(event, idempotency_key("pair-intent", str(intent.signal_id)), emit=emit)
