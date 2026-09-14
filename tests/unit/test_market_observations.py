from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from godzilla.market_data.models import Bar, QualityFlag
from godzilla.market_data.quality import BarQualityChecker
from godzilla.market_data.replay import ReplayRecording

pytestmark = pytest.mark.unit


def test_observation_timestamps_normalized_and_naive_rejected(recording: ReplayRecording) -> None:
    bar = recording.bars[0]
    assert bar.start.tzinfo is UTC
    payload = bar.model_dump()
    payload["start"] = datetime(2026, 9, 15)
    with pytest.raises(ValidationError, match="timezone-aware"):
        Bar.model_validate(payload)
    for change in ({"interval_minutes": 2}, {"end": bar.start}, {"open": "NaN"}):
        with pytest.raises(ValidationError):
            Bar.model_validate(bar.model_dump() | change)


@pytest.mark.parametrize(
    ("change", "flag"),
    [
        ({"open": Decimal("0")}, QualityFlag.INVALID_PRICE),
        ({"high": Decimal("1")}, QualityFlag.IMPOSSIBLE_OHLC),
        ({"volume": Decimal("-1")}, QualityFlag.INVALID_VOLUME),
        ({"volume": Decimal("0")}, QualityFlag.ZERO_VOLUME),
        ({"complete": False}, QualityFlag.INCOMPLETE),
    ],
)
def test_invalid_market_values_flagged(recording, checker, change, flag) -> None:
    checked = checker.check(recording.bars[0].model_copy(update=change))
    assert flag in checked.quality_flags


def test_causal_gap_and_volume_flags(
    recording: ReplayRecording, checker: BarQualityChecker
) -> None:
    previous, current = recording.bars[:2]
    current = current.model_copy(update={"open": Decimal("150"), "volume": Decimal("1500")})
    flags = checker.check(current, previous).quality_flags
    assert {QualityFlag.ABNORMAL_GAP, QualityFlag.ABNORMAL_VOLUME} <= flags
    late_previous = previous.model_copy(
        update={"received_at": current.received_at + timedelta(days=1)}
    )
    assert QualityFlag.ABNORMAL_GAP not in checker.check(current, late_previous).quality_flags


def test_session_missing_alignment_and_future_flags(recording, checker) -> None:
    bar = recording.bars[0]
    out = bar.model_copy(
        update={"start": bar.start - timedelta(hours=1), "end": bar.end - timedelta(hours=1)}
    )
    assert QualityFlag.OUT_OF_SESSION in checker.check(out).quality_flags
    shifted = bar.model_copy(
        update={"start": bar.start + timedelta(seconds=1), "end": bar.end + timedelta(seconds=1)}
    )
    assert QualityFlag.MISALIGNED in checker.check(shifted).quality_flags
    assert QualityFlag.FUTURE_TIMESTAMP in checker.check(shifted).quality_flags
    missing = checker.missing_intervals(
        recording.bars[:1], bar.start, recording.bars[-1].end, as_of=recording.bars[-1].end
    )
    assert missing == tuple(item.start for item in recording.bars[1:])
