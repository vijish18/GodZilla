from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.state_fixtures import healthy, state_snapshot

from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_state.models import RouterSettings
from godzilla.market_state.router import MarketStateRouter
from godzilla.market_state.service import publish_state
from godzilla.storage.models import AuditEventRow, MarketStateRow
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def decision(checker):
    snapshot = state_snapshot()
    engine = MarketStateRouter(
        RouterSettings(), checker.calendar, checker.timezone, MemoryMetrics()
    )
    return engine.route(snapshot, healthy(snapshot), snapshot.timestamp)


def test_state_atomic_roundtrip_idempotency_and_dispatch(pg_engine, checker):
    result = decision(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []
    assert publish_state(journal, result, UUID(int=6), result.timestamp, emitted.append)
    assert not publish_state(journal, result, UUID(int=6), result.timestamp, emitted.append)
    assert len(emitted) == 1
    assert repo.get_market_state(result.decision_hash()) == result
    assert repo.get_market_state("0" * 64) is None
    assert repo.read_events()[0].payload.decision == result
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketStateRow)) == 1
        row = session.get(MarketStateRow, result.decision_hash())
        row.payload = row.payload | {"data_snapshot": "tampered"}
        session.commit()
    with pytest.raises(ValueError, match="integrity"):
        repo.get_market_state(result.decision_hash())


def test_projection_failure_blocks_dispatch_and_rolls_back(pg_engine, checker, monkeypatch):
    result = decision(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []

    def fail(*args):
        raise RuntimeError("injected")

    monkeypatch.setattr(repo, "_project", fail)
    with pytest.raises(CriticalWriteFailure):
        publish_state(journal, result, UUID(int=6), result.timestamp, emitted.append)
    assert not emitted
    assert journal.health().blocking
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(MarketStateRow)) == 0
