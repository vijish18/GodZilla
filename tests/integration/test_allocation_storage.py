from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from tests.allocation_fixtures import allocation_inputs

from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.core.events.models import Provenance
from godzilla.portfolio.allocator import allocate
from godzilla.portfolio.publication import publish_decision, publish_health
from godzilla.storage.models import AlphaHealthRow, PortfolioDecisionRow
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_health_and_decisions_are_separate_audited_idempotent_projections(pg_engine, checker):
    inputs = allocation_inputs(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(inputs.at))
    snapshot = inputs.health[0]
    assert publish_health(journal, snapshot, UUID(int=12), inputs.at)
    assert not publish_health(journal, snapshot, UUID(int=12), inputs.at)
    assert repo.get_alpha_health(snapshot.snapshot_hash()) == snapshot
    provenance = Provenance(
        code_commit=inputs.state.code_commit,
        config_hash=inputs.state.config_hash,
        data_snapshot=inputs.state.data_snapshot,
        feature_version="allocation-v1",
    )
    assert publish_decision(journal, inputs.ensemble, provenance, UUID(int=12), inputs.at)
    decision = allocate(inputs, checker.calendar)
    assert publish_decision(journal, decision, provenance, UUID(int=12), inputs.at)
    assert not publish_decision(journal, decision, provenance, UUID(int=12), inputs.at)
    assert repo.get_ensemble_decision(inputs.ensemble.decision_id) == inputs.ensemble
    assert repo.get_portfolio_decision(decision.decision_id) == decision
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AlphaHealthRow)) == 1
        assert session.scalar(select(func.count()).select_from(PortfolioDecisionRow)) == 1
    assert repo.get_alpha_health("f" * 64) is None
    assert repo.get_portfolio_decision(UUID(int=42)) is None
    assert repo.get_ensemble_decision(UUID(int=42)) is None


def test_allocation_write_failure_prevents_dispatch_and_corruption_detected(
    pg_engine, checker, monkeypatch
):
    inputs = allocation_inputs(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(inputs.at))
    health = inputs.health[0]
    assert publish_health(journal, health, UUID(int=12), inputs.at)
    payload = health.model_dump(mode="json")
    payload["net_pnl"] = "999"
    with Session(pg_engine) as session, session.begin():
        session.execute(update(AlphaHealthRow).values(payload=payload))
    with pytest.raises(ValueError, match="integrity"):
        repo.get_alpha_health(health.snapshot_hash())

    def fail(*args):
        raise RuntimeError("critical write failed")

    monkeypatch.setattr(repo, "_project", fail)
    emitted = []
    with pytest.raises(CriticalWriteFailure):
        publish_health(journal, inputs.health[1], UUID(int=12), inputs.at, emitted.append)
    assert not emitted and len(repo.read_events()) == 1
