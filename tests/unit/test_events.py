import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from godzilla.core.clock import ReplayClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.core.events.models import EventEnvelope, Health
from godzilla.core.events.replay import ReplayEngine
from godzilla.core.events.serialization import (
    deserialize_event,
    event_hash,
    idempotency_key,
    serialize_event,
)
from godzilla.core.health import SystemHealth
from godzilla.storage.postgres import make_engine

pytestmark = pytest.mark.unit


def test_legacy_feature_event_keeps_its_original_hash(event):
    raw = event.model_dump(mode="json")
    raw["payload"] = {
        "kind": "FEATURE_UPDATE",
        "entity": "DEMO",
        "feature_version": "v1",
        "values": [{"name": "x", "value": "1.25"}],
    }
    original = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    restored = deserialize_event(original)
    assert serialize_event(restored) == original
    assert event_hash(restored) == hashlib.sha256(original.encode()).hexdigest()


def test_event_serialization_is_lossless_and_frozen(event):
    restored = deserialize_event(serialize_event(event))
    assert restored == event
    assert restored.payload.bar.open == Decimal("100")
    assert event_hash(restored) == event_hash(event)
    with pytest.raises(ValidationError, match="frozen"):
        event.source = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        event.payload.bar.close = Decimal("0")


@pytest.mark.parametrize("schema", [0, 2, "unknown"])
def test_unknown_schema_fails_closed(event, schema):
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(event.model_dump() | {"schema_version": schema})


def test_timestamps_require_awareness_and_causality(event):
    for update in (
        {"occurred_at": datetime(2026, 9, 15)},
        {"received_at": event.occurred_at - timedelta(seconds=1)},
        {"occurred_at": event.occurred_at - timedelta(seconds=1)},
    ):
        with pytest.raises(ValidationError):
            EventEnvelope.model_validate(event.model_dump() | update)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "FEATURE_UPDATE",
            "entity": "DEMO",
            "feature_version": "v1",
            "values": [{"name": "x", "value": "1.23456789"}],
        },
        {"kind": "MARKET_STATE_UPDATE", "state": "CHOP", "reasons": ["fixture"]},
        {
            "kind": "SIGNAL_INTENT",
            "signal_id": str(UUID(int=2)),
            "instrument_id": "DEMO",
            "alpha_id": "fixture",
            "side": "LONG",
            "reasons": [],
        },
        *[
            {
                "kind": kind,
                "decision_id": str(UUID(int=3)),
                "intent_id": str(UUID(int=2)),
                "accepted": False,
                "reasons": ["fixture"],
            }
            for kind in ("ENSEMBLE_DECISION", "PORTFOLIO_DECISION", "RISK_DECISION")
        ],
        *[
            {
                "kind": kind,
                "order_intent_id": str(UUID(int=4)),
                "broker_order_id": "external-123",
                "reason": "fixture",
            }
            for kind in (
                "ORDER_PLAN_CREATED",
                "ORDER_SUBMITTED",
                "ORDER_ACKNOWLEDGED",
                "ORDER_REJECTED",
                "ORDER_CANCELLED",
                "ORDER_UNKNOWN",
                "ORDER_PARTIAL_FILL",
                "ORDER_FILLED",
            )
        ],
        {
            "kind": "FILL",
            "fill_id": str(UUID(int=5)),
            "order_intent_id": str(UUID(int=4)),
            "broker_order_id": "external-123",
            "quantity": "1",
            "price": "100.05",
        },
        {"kind": "POSITION_UPDATE", "instrument_id": "DEMO", "quantity": "-1", "reason": "fixture"},
        {"kind": "RECONCILIATION", "scope": "fixture", "matched": False, "reasons": ["unknown"]},
        {
            "kind": "HEALTH",
            "component": "data",
            "status": "UNKNOWN",
            "blocking": True,
            "reason": "fixture",
        },
    ],
)
def test_every_event_contract_roundtrips(event, payload):
    changed = EventEnvelope.model_validate(event.model_dump() | {"payload": payload})
    assert deserialize_event(serialize_event(changed)) == changed


def test_idempotency_keys_have_unambiguous_boundaries():
    assert idempotency_key("bars", "ab", "c") != idempotency_key("bars", "a", "bc")
    assert idempotency_key("bars", "a") != idempotency_key("quotes", "a")
    assert idempotency_key("bars", "a") == idempotency_key("bars", "a")
    with pytest.raises(ValueError):
        idempotency_key("", "a")


def test_replay_clock_order_ties_late_market_time_and_duplicates(event):
    health = Health(component="fixture", status="HEALTHY", blocking=False, reason="fixture")
    second = event.model_copy(
        update={
            "event_id": UUID(int=2),
            "payload": health,
            "occurred_at": event.occurred_at - timedelta(minutes=1),
        }
    )
    clock = ReplayClock(event.received_at)
    seen = []
    count = ReplayEngine(clock).run(
        (event, event, second), lambda item: seen.append((item.event_id, clock.now()))
    )
    assert count == 2
    assert [identity for identity, _ in seen] == [event.event_id, second.event_id]
    with pytest.raises(ValueError, match="conflicting"):
        ReplayEngine(clock).run(
            (event, event.model_copy(update={"source": "different"})), lambda _: None
        )
    with pytest.raises(ValueError, match="backwards"):
        clock.advance_to(clock.now() - timedelta(seconds=1))


def test_write_failure_latches_without_disclosing_driver_error(event, data_clock):
    class BrokenRepository:
        calls = 0

        def append(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("private-driver-detail")

    repository = BrokenRepository()
    journal = EventJournal(repository, data_clock)
    emitted = []
    with pytest.raises(CriticalWriteFailure) as error:
        journal.publish(event, idempotency_key("test", "1"), emit=emitted.append)
    assert "private-driver-detail" not in str(error.value)
    assert not emitted
    assert SystemHealth.aggregate([journal.health()]).blocks_new_entries
    with pytest.raises(CriticalWriteFailure, match="latched"):
        journal.publish(event, idempotency_key("test", "1"))
    assert repository.calls == 1


def test_dispatch_failure_after_commit_requires_replay(event, data_clock):
    class Repository:
        def append(self, *args, **kwargs):
            return True

    def fail(_):
        raise RuntimeError("consumer failed")

    journal = EventJournal(Repository(), data_clock)
    with pytest.raises(CriticalWriteFailure, match="after commit"):
        journal.publish(event, idempotency_key("test", "1"), emit=fail)


def test_persistence_health_unknown_until_first_success(event, data_clock):
    class Repository:
        def append(self, *args, **kwargs):
            return True

    journal = EventJournal(Repository(), data_clock)
    assert SystemHealth.aggregate([journal.health()]).blocks_new_entries
    journal.publish(event, idempotency_key("test", "health"))
    assert not SystemHealth.aggregate([journal.health()]).blocks_new_entries


def test_non_postgres_engine_rejected():
    with pytest.raises(ValueError, match="PostgreSQL"):
        make_engine("sqlite://")


def test_instrument_write_failure_uses_same_latch(data_clock):
    from pathlib import Path

    from godzilla.market_data.instruments import LocalInstrumentProvider

    snapshot = LocalInstrumentProvider(
        Path(__file__).parents[2] / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()

    class Repository:
        def save_master(self, snapshot):
            raise RuntimeError("private-driver-detail")

    journal = EventJournal(Repository(), data_clock)
    with pytest.raises(CriticalWriteFailure, match="instrument write"):
        journal.save_instruments(Repository(), snapshot)
    with pytest.raises(CriticalWriteFailure, match="latched"):
        journal.save_instruments(Repository(), snapshot)
