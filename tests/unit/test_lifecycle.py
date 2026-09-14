from datetime import UTC, datetime
from signal import SIGTERM

import pytest

from godzilla.app.lifecycle import InvalidStateTransition, LifecycleController
from godzilla.config.models import SystemState
from godzilla.core.clock import FixedClock

pytestmark = pytest.mark.unit


def test_normal_lifecycle_and_listener() -> None:
    clock = FixedClock(datetime(2026, 9, 14, 3, 30, tzinfo=UTC))
    controller = LifecycleController(clock)
    observed = []
    controller.add_listener(observed.append)

    ready = controller.mark_ready("checks complete")
    stopping = controller.handle_signal(SIGTERM)
    stopped = controller.mark_stopped("done")

    assert ready.previous is SystemState.STARTING
    assert stopping.current is SystemState.STOPPING
    assert stopped.current is SystemState.STOPPED
    assert controller.state is SystemState.STOPPED
    assert observed == [ready, stopping, stopped]


def test_invalid_transition_fails_closed() -> None:
    controller = LifecycleController(FixedClock(datetime(2026, 9, 14, tzinfo=UTC)))
    with pytest.raises(InvalidStateTransition, match="cannot transition"):
        controller.mark_stopped("skipped shutdown")


def test_halted_state_cannot_return_directly_to_ready() -> None:
    controller = LifecycleController(FixedClock(datetime(2026, 9, 14, tzinfo=UTC)))
    controller.transition(SystemState.HALTED, "risk uncertainty")
    with pytest.raises(InvalidStateTransition):
        controller.mark_ready("unsafe shortcut")
