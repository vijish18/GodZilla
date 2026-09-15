from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from godzilla.core.clock import ReplayClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.core.events.models import BarClose, QuoteUpdate, SystemStateSnapshot
from godzilla.core.events.replay import ReplayEngine
from godzilla.core.events.serialization import idempotency_key
from godzilla.market_data.aggregation import FiveMinuteAggregator
from godzilla.market_data.instruments import LocalInstrumentProvider
from godzilla.storage.models import AuditEventRow, Bar1mRow, Bar5mRow, Base, InstrumentRow, QuoteRow
from godzilla.storage.postgres import IdempotencyConflict, PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_migration_upgrade_downgrade_and_metadata_match(pg_engine):
    with pg_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    assert set(Base.metadata.tables) <= set(inspect(pg_engine).get_table_names())
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    with pg_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_transaction_persists_event_projection_state_and_redelivery(pg_engine, event, data_clock):
    repository = PostgresRepository(pg_engine)
    state = SystemStateSnapshot(
        scope="paper",
        state="READY",
        mode="PAPER",
        kill_switch=False,
        broker_health="NOT_CONNECTED",
        data_health="HEALTHY",
        release_id="test",
        last_event_id=event.event_id,
        updated_at=event.received_at,
    )
    journal = EventJournal(repository, data_clock)
    emitted = []
    key = idempotency_key("event", str(event.event_id))
    assert journal.publish(event, key, state=state, emit=emitted.append)
    assert not journal.publish(event, key, state=state, emit=emitted.append)
    assert emitted == [event]
    assert repository.get_state("paper") == state
    assert repository.get_state("unknown") is None
    assert repository.read_events() == (event,)
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 1
        assert session.scalar(select(func.count()).select_from(Bar1mRow)) == 1
    with pytest.raises(IdempotencyConflict):
        repository.append(event.model_copy(update={"source": "conflict"}), key)
    with pytest.raises(IdempotencyConflict):
        repository.append(event, key)


def test_concurrent_duplicate_is_one_committed_event(pg_engine, event):
    repository = PostgresRepository(pg_engine)
    key = idempotency_key("event", str(event.event_id))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: repository.append(event, key), range(4)))
    assert results.count(True) == 1
    assert results.count(False) == 3
    assert repository.read_events() == (event,)


def test_projection_failure_rolls_back_audit_and_blocks_journal(
    pg_engine, event, data_clock, monkeypatch
):
    repository = PostgresRepository(pg_engine)

    def fail(session, event):
        session.execute(text("SELECT nonexistent_column FROM audit_events"))

    monkeypatch.setattr(repository, "_project", fail)
    journal = EventJournal(repository, data_clock)
    with pytest.raises(CriticalWriteFailure):
        journal.publish(event, idempotency_key("event", "1"))
    assert repository.read_events() == ()
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Bar1mRow)) == 0


def test_bar_and_quote_projection_replay_matches_live_order(pg_engine, event, recording, checker):
    repository = PostgresRepository(pg_engine)
    (canonical,) = FiveMinuteAggregator(checker).aggregate(
        recording.bars, as_of=recording.bars[-1].end
    )
    bar_event = event.model_copy(
        update={
            "event_id": UUID(int=2),
            "payload": BarClose(bar=canonical),
            "occurred_at": canonical.end,
            "received_at": canonical.received_at,
        }
    )
    quote = recording.quotes[0]
    quote_event = event.model_copy(
        update={
            "event_id": UUID(int=3),
            "payload": QuoteUpdate(quote=quote),
            "occurred_at": quote.timestamp,
            "received_at": quote.received_at,
        }
    )
    events = (event, bar_event, quote_event)
    for item in events:
        repository.append(item, idempotency_key("event", str(item.event_id)))
    replayed = []
    clock = ReplayClock(event.received_at)
    ReplayEngine(clock).run(repository.read_events(), replayed.append)
    assert replayed == list(events)
    assert repository.read_events(after_sequence=1) == events[1:]
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(Bar5mRow)) == 1
        assert session.scalar(select(func.count()).select_from(QuoteRow)) == 1


def test_instrument_versions_are_idempotent_and_conflicts_rejected(pg_engine):
    snapshot = LocalInstrumentProvider(
        Path(__file__).parents[2] / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()
    repository = PostgresRepository(pg_engine)
    repository.save_master(snapshot)
    repository.save_master(snapshot)
    assert repository.get_master(snapshot.snapshot_id) == snapshot
    assert repository.get_master("missing") is None
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(InstrumentRow)) == len(
            snapshot.instruments
        )
    with pytest.raises(IdempotencyConflict):
        repository.save_master(snapshot.model_copy(update={"version": "changed"}))


def test_commit_constraint_failure_rolls_back_second_event(pg_engine, event, data_clock):
    repository = PostgresRepository(pg_engine)
    repository.append(event, idempotency_key("event", "first"))
    conflicting_bar = event.model_copy(update={"event_id": UUID(int=33)})
    journal = EventJournal(repository, data_clock)
    with pytest.raises(CriticalWriteFailure):
        journal.publish(conflicting_bar, idempotency_key("event", "second"))
    assert repository.read_events() == (event,)


def test_state_redelivery_cannot_mutate_original_projection(pg_engine, event):
    repository = PostgresRepository(pg_engine)
    state = SystemStateSnapshot(
        scope="test",
        state="READY",
        mode="PAPER",
        kill_switch=False,
        broker_health="UNKNOWN",
        data_health="HEALTHY",
        release_id="test",
        last_event_id=event.event_id,
        updated_at=event.received_at,
    )
    key = idempotency_key("event", "state")
    repository.append(event, key, state=state)
    with pytest.raises(IdempotencyConflict):
        repository.append(event, key, state=state.model_copy(update={"kill_switch": True}))
    assert repository.get_state("test") == state


def test_repository_rejects_invalid_key_and_state_reference(pg_engine, event):
    repository = PostgresRepository(pg_engine)
    with pytest.raises(ValueError, match="SHA256"):
        repository.append(event, "bad")
    state = SystemStateSnapshot(
        scope="test",
        state="READY",
        mode="PAPER",
        kill_switch=False,
        broker_health="UNKNOWN",
        data_health="HEALTHY",
        release_id="test",
        last_event_id=UUID(int=999),
        updated_at=event.received_at,
    )
    with pytest.raises(ValueError, match="reference"):
        repository.append(event, idempotency_key("event", "bad-state"), state=state)
    assert repository.read_events() == ()


def test_audit_tampering_and_receipt_regression_fail_closed(pg_engine, event):
    repository = PostgresRepository(pg_engine)
    repository.append(event, idempotency_key("event", "1"))
    with pytest.raises(ValueError, match="regression"):
        repository.append(
            event.model_copy(
                update={
                    "event_id": UUID(int=2),
                    "received_at": event.received_at - timedelta(seconds=1),
                }
            ),
            idempotency_key("event", "2"),
        )
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE audit_events SET chain_hash = :hash"), {"hash": "f" * 64})
    with pytest.raises(ValueError, match="integrity"):
        repository.read_events()
