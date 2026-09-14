from datetime import date, datetime, time, timedelta

import pytest

from godzilla.market_data.aggregation import FiveMinuteAggregator
from godzilla.market_data.calendar import NseExchangeCalendar, SessionTimes, SpecialSession
from godzilla.market_data.models import FeedKind, QualityFlag
from godzilla.market_data.quality import BarQualityChecker
from godzilla.market_data.replay import RecordedProvider

pytestmark = pytest.mark.unit


def test_special_session_buckets_align_to_actual_open(recording, checker):
    original = checker.calendar.snapshot
    times = SessionTimes(
        preopen_start=time(17, 45),
        open_time=time(18, 2),
        final_entry_time=time(18, 30),
        flatten_start_time=time(18, 40),
        hard_flatten_deadline=time(18, 50),
        market_close=time(19),
    )
    special = SpecialSession(
        date=date(2026, 11, 8), name="synthetic", status="CONFIRMED", times=times
    )
    calendar = NseExchangeCalendar(original.model_copy(update={"special_sessions": (special,)}))
    special_checker = BarQualityChecker(calendar, checker.settings, checker.timezone)
    start = datetime.fromisoformat("2026-11-08T18:02:00+05:30")
    shift = start - recording.bars[0].start
    bars = tuple(
        bar.model_copy(
            update={
                "start": bar.start + shift,
                "end": bar.end + shift,
                "received_at": bar.received_at + shift,
            }
        )
        for bar in recording.bars
    )
    (canonical,) = FiveMinuteAggregator(special_checker).aggregate(bars, as_of=bars[-1].end)
    assert canonical.start == start
    canonical.require_strategy_ready(bars[-1].end)


def test_holiday_and_preopen_do_not_create_canonical_bars(recording, checker):
    for shift in (timedelta(days=-1), timedelta(minutes=-10)):
        bars = tuple(
            bar.model_copy(
                update={
                    "start": bar.start + shift,
                    "end": bar.end + shift,
                    "received_at": bar.received_at + shift,
                }
            )
            for bar in recording.bars
        )
        assert FiveMinuteAggregator(checker).aggregate(bars, as_of=bars[-1].end) == ()


def test_last_normal_session_bucket_is_complete(recording, checker):
    shift = timedelta(hours=6, minutes=10)
    bars = tuple(
        bar.model_copy(
            update={
                "start": bar.start + shift,
                "end": bar.end + shift,
                "received_at": bar.received_at + shift,
            }
        )
        for bar in recording.bars
    )
    (canonical,) = FiveMinuteAggregator(checker).aggregate(bars, as_of=bars[-1].end)
    canonical.require_strategy_ready(bars[-1].end)


def test_flag_serialization_is_sorted(recording):
    bar = recording.bars[0].model_copy(
        update={"quality_flags": frozenset({QualityFlag.ZERO_VOLUME, QualityFlag.INVALID_PRICE})}
    )
    assert bar.model_dump(mode="json")["quality_flags"] == ["INVALID_PRICE", "ZERO_VOLUME"]


@pytest.mark.parametrize("kind", [FeedKind.INDEX, FeedKind.SECTOR, FeedKind.VIX])
def test_context_feeds_share_causal_contract(recording, data_clock, kind):
    context = recording.model_copy(
        update={"bars": tuple(bar.model_copy(update={"feed_kind": kind}) for bar in recording.bars)}
    )
    provider = RecordedProvider(context, data_clock)
    assert provider.feed_instruments(kind) == ("DEMO",)
    assert (
        len(
            provider.historical_bars(
                "DEMO", context.bars[0].start, context.bars[-1].end, as_of=context.bars[0].end
            )
        )
        == 1
    )


def test_missing_interval_scope_and_alignment_checked(recording, checker):
    bar = recording.bars[0]
    with pytest.raises(ValueError, match="aligned"):
        checker.missing_intervals((), bar.start + timedelta(seconds=1), bar.end, as_of=bar.end)
    with pytest.raises(ValueError, match="single"):
        checker.missing_intervals(
            (bar, bar.model_copy(update={"instrument_id": "OTHER"})),
            bar.start,
            bar.end,
            as_of=bar.end,
        )
