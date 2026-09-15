from uuid import UUID

import pytest
from tests.vwap_fixtures import vwap_result

from godzilla.alphas.publication import publish_intent
from godzilla.alphas.vwap_models import VwapSignalEvidence
from godzilla.core.clock import FixedClock
from godzilla.core.events.journal import EventJournal
from godzilla.storage.postgres import PostgresRepository

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def test_vwap_features_exit_metadata_and_duplicate_persist(pg_engine, checker):
    result = vwap_result(checker)
    repo = PostgresRepository(pg_engine)
    journal = EventJournal(repo, FixedClock(result.timestamp))
    assert publish_intent(journal, result.intent, UUID(int=10), result.timestamp)
    assert not publish_intent(journal, result.intent, UUID(int=10), result.timestamp)
    restored = repo.get_alpha_signal(result.intent.signal_id)
    assert restored == result.intent
    assert isinstance(restored.evidence, VwapSignalEvidence)
    assert repo.read_events()[0].payload == restored
