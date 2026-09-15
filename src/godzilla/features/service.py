"""Publish feature snapshots through the existing atomic, fail-closed event journal."""

from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import EventEnvelope, FeatureUpdate, NamedValue, Provenance
from godzilla.core.events.serialization import idempotency_key
from godzilla.features.models import FeatureSetVersion, FeatureSnapshot


def publish_snapshot(
    journal: EventJournal,
    snapshot: FeatureSnapshot,
    definition: FeatureSetVersion,
    correlation_id: UUID,
) -> bool:
    digest = snapshot.snapshot_hash()
    envelope = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:feature:" + digest),
        correlation_id=correlation_id,
        occurred_at=snapshot.timestamp,
        received_at=snapshot.timestamp,
        source="feature-engine",
        provenance=Provenance(
            code_commit=snapshot.code_commit,
            config_hash=snapshot.config_hash,
            data_snapshot=snapshot.data_snapshot,
            feature_version=snapshot.feature_set_hash,
        ),
        payload=FeatureUpdate(
            entity=snapshot.entity,
            feature_version=snapshot.feature_set_hash,
            snapshot=snapshot,
            definition=definition,
            values=tuple(
                NamedValue(name=item.name, value=Decimal(str(item.value)))
                for item in snapshot.values
                if item.value is not None
            ),
        ),
    )
    return journal.publish(envelope, idempotency_key("feature-snapshot", digest))
