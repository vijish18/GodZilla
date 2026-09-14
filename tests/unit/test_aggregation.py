from datetime import timedelta
from decimal import Decimal

import pytest

from godzilla.market_data.aggregation import FiveMinuteAggregator
from godzilla.market_data.models import QualityFlag

pytestmark = pytest.mark.unit


def test_correct_ohlcv_and_canonical_readiness(recording, checker, data_clock) -> None:
    (result,) = FiveMinuteAggregator(checker).aggregate(recording.bars, as_of=data_clock.now())
    assert (result.open, result.high, result.low, result.close, result.volume) == tuple(
        map(Decimal, [100, 106, 99, 105, 600])
    )
    assert result.start == recording.bars[0].start
    result.require_strategy_ready(data_clock.now())


def test_future_data_cannot_change_past_output(recording, checker) -> None:
    aggregator = FiveMinuteAggregator(checker)
    cutoff = recording.bars[3].end
    first = aggregator.aggregate(recording.bars[:4], as_of=cutoff)
    assert first == aggregator.aggregate(recording.bars, as_of=cutoff)
    assert not first[0].complete
    with pytest.raises(ValueError, match="canonical"):
        first[0].require_strategy_ready(cutoff)


def test_late_minute_and_duplicates_fail_closed(recording, checker, data_clock) -> None:
    aggregator = FiveMinuteAggregator(checker)
    late = recording.bars[-1].model_copy(
        update={"received_at": data_clock.now() + timedelta(seconds=1)}
    )
    bars = (*recording.bars[:-1], late)
    (partial,) = aggregator.aggregate(bars, as_of=data_clock.now())
    assert not partial.complete
    data_clock.advance(timedelta(seconds=1))
    (ready,) = aggregator.aggregate(bars, as_of=data_clock.now())
    assert ready.complete
    ready.require_strategy_ready(data_clock.now())
    (duplicate,) = aggregator.aggregate(
        (*recording.bars, recording.bars[0]), as_of=data_clock.now()
    )
    assert duplicate.volume == Decimal("600")
    assert QualityFlag.DUPLICATE in duplicate.quality_flags
    with pytest.raises(ValueError):
        duplicate.require_strategy_ready(data_clock.now())


def test_missing_middle_and_source_separation(recording, checker, data_clock) -> None:
    aggregator = FiveMinuteAggregator(checker)
    (missing,) = aggregator.aggregate(
        recording.bars[:2] + recording.bars[3:], as_of=data_clock.now()
    )
    assert QualityFlag.MISSING_INTERVAL in missing.quality_flags
    other = recording.bars[0].model_copy(update={"source": "other"})
    assert len(aggregator.aggregate((*recording.bars, other), as_of=data_clock.now())) == 2
    with pytest.raises(ValueError, match="1m"):
        aggregator.aggregate((missing,), as_of=data_clock.now())
