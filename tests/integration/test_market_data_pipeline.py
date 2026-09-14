from datetime import timedelta
from pathlib import Path

import pytest

from godzilla.core.clock import FixedClock
from godzilla.core.health import HealthStatus
from godzilla.market_data.freshness import QuoteFreshnessTracker
from godzilla.market_data.models import FeedKind
from godzilla.market_data.replay import RecordedProvider
from godzilla.market_data.service import MarketDataService
from godzilla.market_data.storage import MemoryNormalizedStorage, MemoryRawStorage, RawRecord

pytestmark = pytest.mark.integration


def make_service(recording, data_clock, checker, metrics):
    provider = RecordedProvider(recording, data_clock)
    store = MemoryNormalizedStorage()
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    return MarketDataService(provider, data_clock, checker, store, tracker, metrics), store


def test_fake_history_and_stream_produce_same_canonical_data(
    recording, data_clock, checker, metrics
):
    history, store = make_service(recording, data_clock, checker, metrics)
    bars = history.load_history("DEMO", recording.bars[0].start, recording.bars[-1].end)
    history.refresh_quote("DEMO")
    assert len(store.records) == 6
    assert metrics.counters["missing_bars"] == 0
    assert len(metrics.observations["provider_latency_seconds"]) == 2
    stream_clock = FixedClock(recording.bars[0].received_at)
    stream, _ = make_service(recording, stream_clock, checker, metrics)
    disconnects = []
    provider = RecordedProvider(recording, stream_clock)
    subscription = provider.subscribe(
        ("DEMO",), stream.ingest_bar, stream.ingest_quote, disconnects.append
    )
    assert subscription.closed
    assert disconnects == ["recording exhausted"]
    assert stream.canonical_bars() == bars


def test_ingestion_rejects_future_and_retains_duplicate_diagnostics(
    recording, data_clock, checker, metrics
):
    service, _ = make_service(recording, data_clock, checker, metrics)
    future = recording.bars[0].model_copy(
        update={"received_at": data_clock.now() + timedelta(days=1)}
    )
    with pytest.raises(ValueError, match="unavailable"):
        service.ingest_bar(future)
    service.ingest_bar(recording.bars[0])
    service.ingest_bar(recording.bars[0])
    assert metrics.counters["quality_violations"] >= 2


def test_raw_storage_is_immutable_and_separate(recording):
    store = MemoryRawStorage()
    record = RawRecord(
        record_id="source-sequence-1",
        source="fixture",
        received_at=recording.bars[0].received_at,
        payload=b'{"vendor":"raw"}',
    )
    store.append_raw(record)
    store.append_raw(record)
    assert len(store.records) == 1
    with pytest.raises(ValueError, match="immutable"):
        store.append_raw(record.model_copy(update={"payload": b"changed"}))


def test_recorded_provider_respects_asof_and_feed_kinds(recording, data_clock):
    provider = RecordedProvider(recording, data_clock)
    assert provider.feed_instruments(FeedKind.EQUITY) == ("DEMO",)
    assert provider.feed_instruments(FeedKind.VIX) == ()
    bars = provider.historical_bars(
        "DEMO", recording.bars[0].start, recording.bars[-1].end, as_of=recording.bars[0].end
    )
    assert len(bars) == 1
    data_clock.set(recording.bars[0].start)
    with pytest.raises(ValueError, match="no quote"):
        provider.quote_snapshot("DEMO")
    path = Path(__file__).parents[2] / "data/replay/opening-five-minutes.yaml"
    assert RecordedProvider.from_yaml(path, data_clock).recording == recording


def test_silent_stream_blocks_health_and_counts_missing_once(
    recording, data_clock, checker, metrics
):
    service, _ = make_service(recording, data_clock, checker, metrics)
    start, end = recording.bars[0].start, recording.bars[-1].end
    for _ in range(2):
        assert service.window_health("DEMO", "fixture", start, end).status is HealthStatus.UNHEALTHY
    assert metrics.counters["missing_bars"] == 5
    for bar in recording.bars:
        service.ingest_bar(bar)
    assert service.window_health("DEMO", "fixture", start, end).status is HealthStatus.HEALTHY


def test_provider_cannot_leak_past_request_cutoff(recording, data_clock, checker, metrics):
    class LeakyProvider(RecordedProvider):
        def historical_bars(self, instrument_id, start, end, *, as_of):
            self.clock.advance(timedelta(seconds=10))
            return (self.recording.bars[0].model_copy(update={"received_at": self.clock.now()}),)

    provider = LeakyProvider(recording, data_clock)
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    service = MarketDataService(
        provider, data_clock, checker, MemoryNormalizedStorage(), tracker, metrics
    )
    with pytest.raises(ValueError, match="scope"):
        service.load_history("DEMO", recording.bars[0].start, recording.bars[-1].end)
    assert metrics.observations["provider_latency_seconds"] == [10.0]
