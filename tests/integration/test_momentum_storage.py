from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.momentum_fixtures import momentum_result

from godzilla.alphas.publication import publish_intent
from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.storage.models import AlphaSignalRow, AuditEventRow
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_scored_signal_roundtrip_and_exact_duplicate(pg_engine, checker):
    result = momentum_result(checker)
    intent = result.intents[0]
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []
    assert publish_intent(journal, intent, UUID(int=7), result.timestamp, emitted.append)
    assert not publish_intent(journal, intent, UUID(int=7), result.timestamp, emitted.append)
    assert len(emitted) == 1
    assert repo.get_alpha_signal(intent.signal_id) == intent
    assert repo.get_alpha_signal(uuid4()) is None
    assert (
        repo.read_events()[0].payload.evidence.ranked.components
        == intent.evidence.ranked.components
    )
    with Session(pg_engine) as session:
        row = session.get(AlphaSignalRow, intent.signal_id)
        row.payload = row.payload | {"reasons": ["tampered"]}
        session.commit()
    with pytest.raises(ValueError, match="integrity"):
        repo.get_alpha_signal(intent.signal_id)


def test_conflicting_signal_same_symbol_time_cannot_be_published(pg_engine, checker):
    result = momentum_result(checker)
    intent = result.intents[0]
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    publish_intent(journal, intent, UUID(int=7), result.timestamp)
    different_id = intent.model_copy(update={"signal_id": uuid4()})
    with pytest.raises(CriticalWriteFailure):
        publish_intent(journal, different_id, UUID(int=7), result.timestamp)
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AlphaSignalRow)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 1


def test_signal_failure_rolls_back_and_blocks_dispatch(pg_engine, checker, monkeypatch):
    result = momentum_result(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []

    def fail(*args):
        raise RuntimeError("injected")

    monkeypatch.setattr(repo, "_project", fail)
    with pytest.raises(CriticalWriteFailure):
        publish_intent(journal, result.intents[0], UUID(int=7), result.timestamp, emitted.append)
    assert not emitted
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AlphaSignalRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0
