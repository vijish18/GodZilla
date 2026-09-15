from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tests.feature_fixtures import bar_events, feature_context, feature_provenance

from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.features.engine import batch_snapshot
from godzilla.features.models import FeatureSetVersion
from godzilla.features.service import publish_snapshot
from godzilla.storage.models import AuditEventRow, FeatureSnapshotRow, FeatureVersionRow
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_feature_snapshot_version_hash_roundtrip_and_idempotency(pg_engine, checker):
    events, version = bar_events(25), FeatureSetVersion()
    snapshot = batch_snapshot(
        events,
        feature_context(),
        events[-1].received_at,
        calendar=checker.calendar,
        timezone=checker.timezone,
        version=version,
        provenance=feature_provenance(),
    )
    repository = PostgresRepository(pg_engine)
    journal = EventJournal(repository, FixedClock(snapshot.timestamp))
    assert publish_snapshot(journal, snapshot, version, UUID(int=123))
    assert not publish_snapshot(journal, snapshot, version, UUID(int=123))
    assert repository.get_feature_snapshot(snapshot.snapshot_hash()) == snapshot
    assert repository.get_feature_snapshot("0" * 64) is None
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(FeatureVersionRow)) == 1
        assert session.scalar(select(func.count()).select_from(FeatureSnapshotRow)) == 1
    assert repository.read_events()[0].payload.snapshot == snapshot


def test_feature_projection_failure_rolls_back_and_latches(pg_engine, checker, monkeypatch):
    events, version = bar_events(3), FeatureSetVersion()
    snapshot = batch_snapshot(
        events,
        feature_context(),
        events[-1].received_at,
        calendar=checker.calendar,
        timezone=checker.timezone,
        version=version,
        provenance=feature_provenance(),
    )
    repository = PostgresRepository(pg_engine)

    def broken(session, event):
        raise RuntimeError("projection failure")

    monkeypatch.setattr(repository, "_project", broken)
    journal = EventJournal(repository, FixedClock(snapshot.timestamp))
    with pytest.raises(CriticalWriteFailure):
        publish_snapshot(journal, snapshot, version, UUID(int=123))
    with Session(pg_engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditEventRow)) == 0
        assert session.scalar(select(func.count()).select_from(FeatureSnapshotRow)) == 0
