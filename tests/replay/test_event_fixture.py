from pathlib import Path

import pytest

from godzilla.core.clock import ReplayClock
from godzilla.core.events.cli import main
from godzilla.core.events.replay import ReplayEngine, read_fixture

pytestmark = pytest.mark.replay


def test_event_fixture_preserves_ties_and_delayed_market_times(capsys):
    path = Path(__file__).parents[2] / "data/events/ordered-session.jsonl"
    events = read_fixture(path)
    first = []
    second = []
    for target in (first, second):
        clock = ReplayClock(events[0].received_at)
        assert (
            ReplayEngine(clock).run(
                events,
                lambda event, target=target, clock=clock: target.append(
                    (event.event_id, clock.now())
                ),
            )
            == 3
        )
    assert first == second
    assert first[0][1] == first[1][1]
    assert events[1].occurred_at < events[0].occurred_at
    assert [item[0] for item in first] == [event.event_id for event in events]
    assert main([str(path)]) == 0
    assert '"events": 3' in capsys.readouterr().out
