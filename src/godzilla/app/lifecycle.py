"""Explicit application lifecycle state transitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from signal import Signals

from godzilla.config.models import SystemState
from godzilla.core.clock import Clock
from godzilla.core.ids import EventId


class InvalidStateTransition(RuntimeError):
    """Raised when a lifecycle transition is not permitted."""


@dataclass(frozen=True, slots=True)
class StateTransition:
    event_id: EventId
    previous: SystemState
    current: SystemState
    occurred_at: datetime
    reason: str


_ALLOWED_TRANSITIONS: dict[SystemState, frozenset[SystemState]] = {
    SystemState.STARTING: frozenset(
        {
            SystemState.READY,
            SystemState.DEGRADED,
            SystemState.HALTED,
            SystemState.EMERGENCY,
            SystemState.STOPPING,
        }
    ),
    SystemState.READY: frozenset(
        {
            SystemState.DEGRADED,
            SystemState.HALTED,
            SystemState.EMERGENCY,
            SystemState.STOPPING,
        }
    ),
    SystemState.DEGRADED: frozenset(
        {SystemState.READY, SystemState.HALTED, SystemState.EMERGENCY, SystemState.STOPPING}
    ),
    SystemState.HALTED: frozenset({SystemState.EMERGENCY, SystemState.STOPPING}),
    SystemState.EMERGENCY: frozenset({SystemState.STOPPING}),
    SystemState.STOPPING: frozenset({SystemState.STOPPED}),
    SystemState.STOPPED: frozenset({SystemState.STARTING}),
}


class LifecycleController:
    """Owns the system state and produces immutable transition records."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._state = SystemState.STARTING
        self._listeners: list[Callable[[StateTransition], None]] = []

    @property
    def state(self) -> SystemState:
        return self._state

    def add_listener(self, listener: Callable[[StateTransition], None]) -> None:
        self._listeners.append(listener)

    def transition(self, target: SystemState, reason: str) -> StateTransition:
        if target not in _ALLOWED_TRANSITIONS[self._state]:
            raise InvalidStateTransition(f"cannot transition from {self._state} to {target}")
        transition = StateTransition(
            event_id=EventId.new(),
            previous=self._state,
            current=target,
            occurred_at=self._clock.now(),
            reason=reason,
        )
        self._state = target
        for listener in self._listeners:
            listener(transition)
        return transition

    def mark_ready(self, reason: str) -> StateTransition:
        return self.transition(SystemState.READY, reason)

    def request_stop(self, reason: str) -> StateTransition:
        if self._state is SystemState.STOPPING:
            raise InvalidStateTransition("shutdown already in progress")
        return self.transition(SystemState.STOPPING, reason)

    def handle_signal(self, signum: int, _frame: object = None) -> StateTransition:
        try:
            signal_name = Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        return self.request_stop(f"signal:{signal_name}")

    def mark_stopped(self, reason: str) -> StateTransition:
        return self.transition(SystemState.STOPPED, reason)
