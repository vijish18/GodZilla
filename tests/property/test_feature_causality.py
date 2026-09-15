from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.feature_fixtures import bar_events, feature_context, feature_provenance

from godzilla.core.events.models import BarClose
from godzilla.features.engine import CausalFeatureEngine, batch_snapshot
from godzilla.features.models import FeatureSetVersion

pytestmark = pytest.mark.property


@settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=10))
def test_appending_arbitrary_future_events_cannot_change_snapshot(checker, prices):
    events = bar_events(25 + len(prices))
    engine = CausalFeatureEngine(
        checker.calendar, checker.timezone, FeatureSetVersion(), feature_provenance()
    )
    for event in events[:25]:
        engine.on_event(event)
    at = events[24].received_at
    before = engine.snapshot(feature_context(), at)
    for event, price in zip(events[25:], prices, strict=True):
        bar = event.payload.bar.model_copy(
            update={
                "open": Decimal(price),
                "close": Decimal(price),
                "high": Decimal(price + 1),
                "low": Decimal(price) / 2,
            }
        )
        engine.on_event(event.model_copy(update={"payload": BarClose(bar=bar)}))
    assert engine.snapshot(feature_context(), at) == before
    assert engine.snapshot(feature_context(), at).snapshot_hash() == before.snapshot_hash()


def test_batch_and_incremental_api_equivalence_at_every_prefix(checker):
    events = bar_events(27)
    context, version, provenance = feature_context(), FeatureSetVersion(), feature_provenance()
    incremental = CausalFeatureEngine(checker.calendar, checker.timezone, version, provenance)
    for event in events:
        incremental.on_event(event)
        live = incremental.snapshot(context, event.received_at)
        batch = batch_snapshot(
            events,
            context,
            event.received_at,
            calendar=checker.calendar,
            timezone=checker.timezone,
            version=version,
            provenance=provenance,
        )
        assert live == batch


def test_late_receipt_cannot_repair_a_past_snapshot(checker):
    events = bar_events(3)
    engine = CausalFeatureEngine(
        checker.calendar, checker.timezone, FeatureSetVersion(), feature_provenance()
    )
    for event in events[1:]:
        engine.on_event(event)
    at = events[-1].received_at
    before = engine.snapshot(feature_context(), at)
    engine.on_event(events[0].model_copy(update={"received_at": at + timedelta(seconds=1)}))
    assert engine.snapshot(feature_context(), at) == before
    assert before.get("stock.session_vwap").value is None
    assert (
        engine.snapshot(feature_context(), at + timedelta(seconds=1))
        .get("stock.session_vwap")
        .value
        == 101
    )
