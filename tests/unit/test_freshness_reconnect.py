from datetime import timedelta
from decimal import Decimal

import pytest

from godzilla.core.health import HealthStatus, SystemHealth
from godzilla.market_data.freshness import QuoteFreshnessTracker
from godzilla.market_data.models import QualityFlag
from godzilla.market_data.reconnect import ExponentialBackoff, ReconnectController, StreamState

pytestmark = pytest.mark.unit


def test_stale_quotes_block_entries_and_clock_rollback_blocks(
    recording, data_clock, metrics
) -> None:
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    assert SystemHealth.aggregate([tracker.health("DEMO")]).blocks_new_entries
    tracker.update(recording.quotes[0])
    assert tracker.require_fresh("DEMO") == recording.quotes[0]
    data_clock.advance(timedelta(seconds=3))
    assert tracker.health("DEMO").status is HealthStatus.HEALTHY
    data_clock.advance(timedelta(microseconds=1))
    with pytest.raises(ValueError, match="stale"):
        tracker.require_fresh("DEMO")
    data_clock.set(recording.quotes[0].timestamp - timedelta(seconds=1))
    assert tracker.health("DEMO").status is HealthStatus.UNHEALTHY


@pytest.mark.parametrize(
    ("change", "flag"),
    [
        ({"bid": Decimal("106")}, QualityFlag.CROSSED_QUOTE),
        ({"last": Decimal("0")}, QualityFlag.INVALID_PRICE),
    ],
)
def test_invalid_quote_cannot_clear_health(recording, data_clock, metrics, change, flag) -> None:
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    quote = recording.quotes[0].model_copy(update=change)
    assert flag in tracker.update(quote).quality_flags
    assert tracker.health("DEMO").status is HealthStatus.UNHEALTHY


def test_duplicate_future_and_stale_quotes(recording, data_clock, metrics) -> None:
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    quote = recording.quotes[0]
    tracker.update(quote)
    assert QualityFlag.DUPLICATE in tracker.update(quote).quality_flags
    future = quote.model_copy(update={"timestamp": data_clock.now() + timedelta(seconds=1)})
    assert QualityFlag.FUTURE_TIMESTAMP in tracker.update(future).quality_flags
    data_clock.advance(timedelta(seconds=4))
    assert QualityFlag.STALE in tracker.update(quote).quality_flags


def test_reconnect_requires_post_connect_snapshot_and_gap_review(
    recording, data_clock, metrics
) -> None:
    tracker = QuoteFreshnessTracker(data_clock, 3, metrics)
    tracker.update(recording.quotes[0])
    controller = ReconnectController(data_clock, ExponentialBackoff(), metrics)
    assert controller.next_retry_at() == data_clock.now() + timedelta(seconds=1)
    data_clock.advance(timedelta(seconds=1))
    controller.connected()
    assert controller.health().status is HealthStatus.UNHEALTHY
    with pytest.raises(ValueError):
        controller.validate_recovery(tracker, ("DEMO",), gaps_reconciled=True)
    quote = recording.quotes[0].model_copy(
        update={"timestamp": data_clock.now(), "received_at": data_clock.now()}
    )
    tracker.update(quote)
    with pytest.raises(ValueError):
        controller.validate_recovery(tracker, ("DEMO",), gaps_reconciled=False)
    controller.validate_recovery(tracker, ("DEMO",), gaps_reconciled=True)
    assert controller.state is StreamState.RECOVERY_VALIDATED
    controller.disconnected("transport lost")
    assert controller.health().status is HealthStatus.UNHEALTHY


def test_backoff_is_bounded(data_clock, metrics) -> None:
    backoff = ExponentialBackoff(initial_seconds=1, maximum_seconds=2, max_attempts=3)
    assert [backoff.delay(i) for i in range(1, 5)] == [
        timedelta(seconds=1),
        timedelta(seconds=2),
        timedelta(seconds=2),
        None,
    ]
    controller = ReconnectController(data_clock, backoff, metrics)
    for _ in range(4):
        controller.next_retry_at()
    assert controller.state is StreamState.EXHAUSTED
    assert metrics.counters["reconnects"] == 3
    with pytest.raises(ValueError):
        ExponentialBackoff(initial_seconds=-1)
    with pytest.raises(ValueError):
        backoff.delay(0)
