from uuid import UUID

import pytest
from tests.relative_value_fixtures import relative_value_result

from godzilla.alphas.relative_value_models import RelativeValueIntent
from godzilla.alphas.relative_value_publication import publish_relative_value
from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import CriticalWriteFailure, EventJournal
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_basket_is_one_durable_idempotent_intent(pg_engine, checker):
    intent = relative_value_result(checker, construction="BASKET").intents[0]
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(intent.timestamp))
    emitted = []
    assert publish_relative_value(journal, intent, UUID(int=11), intent.timestamp, emitted.append)
    assert not publish_relative_value(
        journal, intent, UUID(int=11), intent.timestamp, emitted.append
    )
    restored = repo.get_alpha_signal(intent.signal_id)
    assert isinstance(restored, RelativeValueIntent) and restored == intent
    assert len(emitted) == 1 and len(restored.legs) == 4
    with pytest.raises(CriticalWriteFailure):
        publish_relative_value(
            journal,
            intent.model_copy(update={"reasons": ("conflict",)}),
            UUID(int=11),
            intent.timestamp,
        )


def test_projection_failure_dispatches_no_legs(pg_engine, checker, monkeypatch):
    intent = relative_value_result(checker).intents[0]
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(intent.timestamp))

    def fail(*args):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(repo, "_project", fail)
    emitted = []
    with pytest.raises(CriticalWriteFailure):
        publish_relative_value(journal, intent, UUID(int=11), intent.timestamp, emitted.append)
    assert not emitted and not repo.read_events()
