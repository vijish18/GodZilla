"""Canonical wire format and collision-resistant, namespace-scoped idempotency keys."""

import hashlib
import json

from godzilla.core.events.models import EventEnvelope


def serialize_event(event: EventEnvelope) -> str:
    return json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def deserialize_event(payload: str) -> EventEnvelope:
    return EventEnvelope.model_validate_json(payload)


def event_hash(event: EventEnvelope) -> str:
    return hashlib.sha256(serialize_event(event).encode()).hexdigest()


def idempotency_key(namespace: str, *parts: str) -> str:
    if not namespace or not parts or any(not part for part in parts):
        raise ValueError("idempotency key requires a namespace and nonempty identity parts")
    encoded = json.dumps([namespace, *parts], separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
