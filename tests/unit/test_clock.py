from datetime import UTC, datetime, timedelta

import pytest

from godzilla.core.clock import Clock, FixedClock, ProductionClock

pytestmark = pytest.mark.unit


def test_fixed_clock_is_deterministic_and_advanceable() -> None:
    instant = datetime(2026, 9, 14, 9, 15, tzinfo=UTC)
    clock = FixedClock(instant)

    assert isinstance(clock, Clock)
    assert clock.now() == instant
    assert clock.advance(timedelta(minutes=5)) == instant + timedelta(minutes=5)
    assert clock.now() == instant + timedelta(minutes=5)


def test_fixed_clock_rejects_naive_instants() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 9, 14, 9, 15))


def test_production_clock_returns_utc_aware_time() -> None:
    instant = ProductionClock().now()
    assert instant.tzinfo is UTC
    assert instant.utcoffset() == timedelta(0)
