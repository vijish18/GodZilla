import socket
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from godzilla.core.clock import FixedClock
from godzilla.core.events.models import BarClose, EventEnvelope, Provenance
from godzilla.market_data.calendar import NseExchangeCalendar
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_data.providers import LocalCalendarProvider, load_yaml_model
from godzilla.market_data.quality import BarQualityChecker, DataQualitySettings
from godzilla.market_data.replay import ReplayRecording

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("postgres"):
        return  # Dedicated fixtures restrict these tests to a loopback test database.

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def recording() -> ReplayRecording:
    return load_yaml_model(ROOT / "data/replay/opening-five-minutes.yaml", ReplayRecording)


@pytest.fixture
def data_clock() -> FixedClock:
    return FixedClock(datetime.fromisoformat("2026-09-15T09:20:00+05:30"))


@pytest.fixture
def metrics() -> MemoryMetrics:
    return MemoryMetrics()


@pytest.fixture
def checker() -> BarQualityChecker:
    calendar = NseExchangeCalendar(
        LocalCalendarProvider(ROOT / "data/calendar/nse-cm-2026.yaml").load()
    )
    return BarQualityChecker(calendar, DataQualitySettings(), "Asia/Kolkata")


@pytest.fixture
def event(recording: ReplayRecording) -> EventEnvelope:
    bar = recording.bars[0]
    return EventEnvelope(
        event_id=UUID(int=1),
        correlation_id=UUID(int=100),
        occurred_at=bar.end,
        received_at=bar.received_at,
        source="test-recording",
        provenance=Provenance(
            code_commit="fixture-commit",
            config_hash="0" * 64,
            data_snapshot="synthetic-opening-v1",
            feature_version="not-applicable",
        ),
        payload=BarClose(bar=bar),
    )
