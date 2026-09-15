from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.pairs_fixtures import pairs_inputs

from godzilla.alphas.pairs_engine import PairsEngine
from godzilla.alphas.pairs_models import PairSignalIntent, PairsSettings
from godzilla.alphas.pairs_publication import publish_pair_intent
from godzilla.alphas.pairs_statistics import StatsmodelsDiagnostics
from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.storage.models import AlphaSignalRow, AuditEventRow
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_pair_legs_persist_as_one_event_and_duplicate_conflicts(pg_engine, checker):
    result = PairsEngine(PairsSettings(), StatsmodelsDiagnostics(), checker.calendar).evaluate(
        *pairs_inputs(checker)
    )
    intent = result.intent
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []
    assert publish_pair_intent(journal, intent, UUID(int=9), result.timestamp, emitted.append)
    assert not publish_pair_intent(journal, intent, UUID(int=9), result.timestamp, emitted.append)
    restored = repo.get_alpha_signal(intent.signal_id)
    assert isinstance(restored, PairSignalIntent)
    assert restored == intent and len(restored.legs) == 2
    assert len(emitted) == 1
    assert repo.read_events()[0].payload == intent
    with pytest.raises(CriticalWriteFailure):
        publish_pair_intent(
            journal,
            intent.model_copy(update={"reasons": ("conflict",)}),
            UUID(int=9),
            result.timestamp,
        )
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AlphaSignalRow)) == 1


def test_pair_projection_failure_cannot_dispatch_one_leg(pg_engine, checker, monkeypatch):
    result = PairsEngine(PairsSettings(), StatsmodelsDiagnostics(), checker.calendar).evaluate(
        *pairs_inputs(checker)
    )
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    emitted = []

    def broken(*args):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(repo, "_project", broken)
    with pytest.raises(CriticalWriteFailure):
        publish_pair_intent(journal, result.intent, UUID(int=9), result.timestamp, emitted.append)
    assert not emitted
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0
