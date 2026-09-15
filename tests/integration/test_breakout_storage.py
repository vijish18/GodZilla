from uuid import UUID

import pytest
from tests.breakout_fixtures import breakout_result

from godzilla.alphas.breakout_models import BreakoutSignalEvidence
from godzilla.alphas.publication import publish_intent
from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import EventJournal
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_breakout_evidence_and_invalidation_persist_with_idempotency(pg_engine, checker):
    result = breakout_result(checker)
    intent = result.intents[-1]
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    assert publish_intent(journal, intent, UUID(int=8), result.timestamp)
    assert not publish_intent(journal, intent, UUID(int=8), result.timestamp)
    restored = repo.get_alpha_signal(intent.signal_id)
    assert restored == intent
    assert isinstance(restored.evidence, BreakoutSignalEvidence)
    assert restored.evidence.invalidation == intent.evidence.invalidation
