"""Canonical wire format and collision-resistant, namespace-scoped idempotency keys."""

import hashlib
import json

from godzilla.core.events.models import (
    Decision,
    EventEnvelope,
    FeatureUpdate,
    Health,
    MarketStateUpdate,
    SignalIntent,
)


def serialize_event(event: EventEnvelope) -> str:
    payload = event.model_dump(mode="json")
    if isinstance(event.payload, FeatureUpdate) and event.payload.snapshot is None:
        # Preserve the canonical schema-1 representation and existing audit hashes.
        payload["payload"].pop("snapshot", None)
        payload["payload"].pop("definition", None)
    if isinstance(event.payload, MarketStateUpdate) and event.payload.decision is None:
        payload["payload"].pop("decision", None)
    if isinstance(event.payload, SignalIntent) and event.payload.evidence is None:
        payload["payload"].pop("score", None)
        payload["payload"].pop("evidence", None)
    if isinstance(event.payload, Decision):
        for name in ("ensemble", "portfolio"):
            if getattr(event.payload, name) is None:
                payload["payload"].pop(name, None)
    if isinstance(event.payload, Health) and event.payload.alpha_snapshot is None:
        payload["payload"].pop("alpha_snapshot", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def deserialize_event(payload: str) -> EventEnvelope:
    return EventEnvelope.model_validate_json(payload)


def event_hash(event: EventEnvelope) -> str:
    return hashlib.sha256(serialize_event(event).encode()).hexdigest()


def idempotency_key(namespace: str, *parts: str) -> str:
    if not namespace or not parts or any(not part for part in parts):
        raise ValueError("idempotency key requires a namespace and nonempty identity parts")
    encoded = json.dumps([namespace, *parts], separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
