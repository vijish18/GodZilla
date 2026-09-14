from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from godzilla.market_data.calendar import (
    CalendarCoverageError,
    CalendarSnapshot,
    NseExchangeCalendar,
    SessionState,
    SessionTimes,
)
from godzilla.market_data.providers import LocalCalendarProvider

pytestmark = pytest.mark.unit
DATA = Path(__file__).parents[2] / "data/calendar/nse-cm-2026.yaml"
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def calendar() -> NseExchangeCalendar:
    return NseExchangeCalendar(LocalCalendarProvider(DATA).load())


def test_holiday_weekend_and_pending_special_are_closed(calendar: NseExchangeCalendar) -> None:
    assert calendar.session_on(date(2026, 9, 14)) is None
    assert calendar.session_on(date(2026, 9, 13)) is None
    assert calendar.session_on(date(2026, 11, 8)) is None


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 59, SessionState.CLOSED),
        (9, 0, SessionState.PREOPEN),
        (9, 15, SessionState.OPEN),
        (15, 0, SessionState.FINAL_ENTRY_BLOCKED),
        (15, 10, SessionState.FLATTENING),
        (15, 24, SessionState.CLOSED_POST),
    ],
)
def test_session_boundaries(
    calendar: NseExchangeCalendar, hour: int, minute: int, expected: SessionState
) -> None:
    assert calendar.state_at(datetime(2026, 9, 15, hour, minute, tzinfo=IST)) is expected


def test_calendar_converts_utc_and_fails_outside_coverage(
    calendar: NseExchangeCalendar,
) -> None:
    assert calendar.state_at(datetime(2026, 9, 15, 3, 30, tzinfo=UTC)) is SessionState.PREOPEN
    with pytest.raises(CalendarCoverageError):
        calendar.session_on(date(2027, 1, 1))
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.state_at(datetime(2026, 9, 15))


def test_confirmed_special_session_uses_its_own_times(calendar: NseExchangeCalendar) -> None:
    payload = calendar.snapshot.model_dump()
    payload["special_sessions"] = [
        {
            "date": "2026-11-08",
            "name": "confirmed fixture",
            "status": "CONFIRMED",
            "times": SessionTimes(
                preopen_start="17:45:00",
                open_time="18:00:00",
                final_entry_time="18:30:00",
                flatten_start_time="18:40:00",
                hard_flatten_deadline="18:50:00",
                market_close="19:00:00",
            ),
        }
    ]
    special_calendar = NseExchangeCalendar(CalendarSnapshot.model_validate(payload))
    session = special_calendar.session_on(date(2026, 11, 8))
    assert session is not None and session.special
    assert special_calendar.state_at(datetime(2026, 11, 8, 18, 5, tzinfo=IST)) is SessionState.OPEN
