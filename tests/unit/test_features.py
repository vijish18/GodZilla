import math
from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError
from tests.feature_fixtures import bar_events, feature_context, feature_provenance

from godzilla.core.events.models import BarClose, QuoteUpdate
from godzilla.features.engine import CausalFeatureEngine, batch_snapshot
from godzilla.features.math import covariance, ema, slope
from godzilla.features.models import (
    FeatureSetVersion,
    FeatureSnapshot,
    FeatureValue,
    InstrumentRef,
    MissingReason,
)
from godzilla.market_data.models import FeedKind, QualityFlag, Quote

pytestmark = pytest.mark.unit


def test_missing_previous_session_cannot_use_older_baseline(checker):
    events = bar_events(75, day="2026-09-10") + bar_events(3)
    snapshot = evaluate(checker, events, version=FeatureSetVersion(baseline_sessions=1))
    assert snapshot.get("stock.opening_gap").value is None
    assert snapshot.get("liquidity.adv").value is None


def test_price_only_context_feeds_with_zero_volume(checker):
    events = bar_events(3)
    for symbol, kind in (("NIFTY", FeedKind.INDEX), ("VIX", FeedKind.VIX)):
        for event in bar_events(3, symbol=symbol, volume=0):
            bar = event.payload.bar.model_copy(update={"feed_kind": kind})
            events += (event.model_copy(update={"payload": BarClose(bar=bar)}),)
    context = feature_context(
        nifty=InstrumentRef(instrument_id="NIFTY", source="fixture"),
        vix=InstrumentRef(instrument_id="VIX", source="fixture"),
    )
    snapshot = evaluate(checker, events, context=context)
    assert snapshot.get("market.return_5m").value == pytest.approx(102 / 101 - 1)
    assert snapshot.get("market.vix_level").value == 102
    assert snapshot.get("market.session_vwap").reason is MissingReason.ZERO_DENOMINATOR
    assert evaluate(checker, bar_events(3, volume=0)).get("stock.session_vwap").value is None


def evaluate(checker, events, context=None, at=None, version=None):
    return batch_snapshot(
        events,
        context or feature_context(),
        at or events[-1].received_at,
        calendar=checker.calendar,
        timezone=checker.timezone,
        version=version or FeatureSetVersion(statistics_bars=2, atr_bars=2),
        provenance=feature_provenance(),
    )


def test_hand_computed_stock_formulas(checker):
    snapshot = evaluate(checker, bar_events(25))

    def value(name):
        return snapshot.get("stock." + name).value

    assert value("session_vwap") == 112
    assert value("vwap_distance") == pytest.approx(124 / 112 - 1)
    for horizon in (5, 15, 30, 60, 120):
        assert value(f"return_{horizon}m") == pytest.approx(124 / (124 - horizon // 5) - 1)
        assert value(f"slope_{horizon}m") == pytest.approx(1 / (124 - horizon // 5))
    assert value("ema_9") == pytest.approx(120)
    assert value("ema_9_slope") == pytest.approx(120 / 119 - 1)
    assert value("ema_20") == pytest.approx(114.5)
    assert value("ema_20_slope") == pytest.approx(114.5 / 113.5 - 1)
    assert value("atr") == 2
    assert value("opening_range_high") == 103
    assert value("opening_range_low") == 99
    assert value("range_percentile") == 0
    assert value("realized_volatility") == pytest.approx(
        abs(math.log(124 / 123) - math.log(123 / 122)) / 2
    )
    assert value("range_volatility") == pytest.approx(
        math.sqrt(((2 / 124) ** 2 + (2 / 123) ** 2) / 2)
    )


def test_warmup_missing_and_invalid_data_are_explicit(checker):
    events = bar_events(2)
    snapshot = evaluate(checker, events)
    assert snapshot.get("stock.ema_9").reason is MissingReason.WARMUP
    assert snapshot.get("stock.opening_range_high").value is None
    assert snapshot.get("market.vix_level").reason is MissingReason.MISSING_INPUT
    assert snapshot.get("pairs.stationarity_pvalue").reason is MissingReason.NOT_IMPLEMENTED
    bad = events[-1].model_copy(
        update={
            "payload": BarClose(
                bar=events[-1].payload.bar.model_copy(
                    update={"quality_flags": frozenset({QualityFlag.INCOMPLETE})}
                )
            )
        }
    )
    snapshot = evaluate(checker, (events[0], bad))
    assert snapshot.get("stock.session_vwap").reason is MissingReason.INVALID_INPUT
    snapshot = evaluate(checker, (events[-1],))
    assert snapshot.get("stock.return_5m").reason is MissingReason.MISSING_INPUT
    with pytest.raises(ValidationError):
        FeatureValue(name="x", value=float("nan"))
    with pytest.raises(ValidationError):
        FeatureValue(name="x", value=None)


def test_opening_range_and_swings_are_only_published_after_confirmation(checker):
    events = list(bar_events(5))
    high = events[2].payload.bar.model_copy(update={"high": Decimal(150)})
    events[2] = events[2].model_copy(update={"payload": BarClose(bar=high)})
    before = evaluate(checker, tuple(events[:4]))
    after = evaluate(checker, tuple(events))
    assert before.get("stock.swing_high").value is None
    assert after.get("stock.swing_high").value == 150
    assert before.get("stock.opening_range_high").value == 150
    assert evaluate(checker, tuple(events[:2])).get("stock.opening_range_high").value is None


def test_market_sector_residuals_breadth_and_vix(checker):
    stock = bar_events(4)
    market = bar_events(4, symbol="NIFTY", offset=200)
    sector = bar_events(4, symbol="SECTOR", offset=150)
    vix = bar_events(4, symbol="VIX", offset=20)

    def ref(name):
        return InstrumentRef(instrument_id=name, source="fixture")

    context = feature_context(
        nifty=ref("NIFTY"),
        sector=ref("SECTOR"),
        vix=ref("VIX"),
        breadth_members=(ref("STOCK"), ref("SECTOR")),
        sector_members=(ref("STOCK"),),
    )
    snapshot = evaluate(checker, stock + market + sector + vix, context)
    assert snapshot.get("stock.relative_market_15m").value == pytest.approx(103 / 100 - 203 / 200)
    assert snapshot.get("sector.relative_market_15m").value == pytest.approx(153 / 150 - 203 / 200)
    assert snapshot.get("market.breadth_advancing").value == 1
    assert snapshot.get("sector.breadth_coverage").value == 1
    assert snapshot.get("market.vix_level").value == 23
    assert snapshot.get("market.vix_change").value == pytest.approx(23 / 22 - 1)
    incomplete = evaluate(checker, stock + market + vix, context)
    assert incomplete.get("market.breadth_coverage").value == 0.5
    assert incomplete.get("market.breadth_advancing").value is None


def test_liquidity_baselines_use_only_completed_previous_sessions(checker):
    previous = bar_events(75, day="2026-09-10", offset=100, volume=10) + bar_events(
        75, day="2026-09-11", offset=100, volume=20
    )
    current = bar_events(3, volume=30)
    version = FeatureSetVersion(baseline_sessions=2)
    context = feature_context(proposed_quantity=10)
    at = current[-1].received_at
    quote = Quote(
        instrument_id="STOCK",
        source="fixture",
        timestamp=at,
        received_at=at,
        bid=Decimal(99),
        ask=Decimal(101),
        last=Decimal(100),
    )
    event = current[-1].model_copy(
        update={"event_id": uuid5(NAMESPACE_URL, "quote"), "payload": QuoteUpdate(quote=quote)}
    )
    snapshot = evaluate(checker, previous + current + (event,), context, version=version)
    assert snapshot.get("stock.opening_gap").value == pytest.approx(100 / 174 - 1)
    assert snapshot.get("liquidity.adv").value == 1125
    assert snapshot.get("liquidity.average_daily_turnover").value == 154125
    assert snapshot.get("stock.relative_volume").value == 2
    assert snapshot.get("stock.relative_turnover").value == 2
    assert snapshot.get("liquidity.spread_bps").value == 200
    assert snapshot.get("liquidity.adv_participation").value == pytest.approx(10 / 1125)
    assert snapshot.get("liquidity.turnover_participation").value == pytest.approx(1000 / 154125)
    stale = evaluate(
        checker,
        previous + current + (event,),
        context,
        at=at + timedelta(seconds=4),
        version=version,
    )
    assert stale.get("liquidity.spread_bps").reason is MissingReason.STALE
    assert stale.get("liquidity.quote_age").value == 4


def test_pairs_use_prior_window_and_constant_variance_is_missing(checker):
    left = bar_events(6)
    right = bar_events(6, symbol="PAIR", offset=200)
    context = feature_context(pair=InstrumentRef(instrument_id="PAIR", source="fixture"))
    snapshot = evaluate(checker, left + right, context)
    a = tuple(math.log(float(e.payload.bar.close)) for e in left[-3:-1])
    b = tuple(math.log(float(e.payload.bar.close)) for e in right[-3:-1])
    assert snapshot.get("pairs.hedge_ratio").value == pytest.approx((a[1] - a[0]) / (b[1] - b[0]))
    assert snapshot.get("pairs.zscore").value is None  # Two training points fit exactly.
    assert snapshot.get("pairs.log_left").value == pytest.approx(math.log(105))


def test_model_hashes_context_dates_and_duplicate_inputs(checker):
    events = bar_events(3)
    engine = CausalFeatureEngine(
        checker.calendar, checker.timezone, FeatureSetVersion(), feature_provenance()
    )
    for event in events:
        engine.on_event(event)
        engine.on_event(event)
    snapshot = engine.snapshot(feature_context(), events[-1].received_at)
    assert (
        FeatureSnapshot.model_validate_json(snapshot.model_dump_json()).snapshot_hash()
        == snapshot.snapshot_hash()
    )
    with pytest.raises(ValueError, match="conflicting"):
        engine.on_event(events[0].model_copy(update={"source": "changed"}))
    with pytest.raises(ValueError, match="context"):
        engine.snapshot(
            feature_context().model_copy(
                update={"known_at": events[-1].received_at + timedelta(days=1)}
            ),
            events[-1].received_at,
        )
    assert FeatureSetVersion().version_hash() != FeatureSetVersion(atr_bars=2).version_hash()
    with pytest.raises(ValueError):
        covariance((1.0,), ())
    with pytest.raises(ValueError):
        slope((1.0,))
    with pytest.raises(ValueError):
        ema((1.0,), 0)
