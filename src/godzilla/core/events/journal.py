"""Fail-closed publication boundary: durable commit precedes downstream dispatch."""

from collections.abc import Callable

from godzilla.core.clock import Clock
from godzilla.core.events.models import EventEnvelope, SystemStateSnapshot
from godzilla.core.health import ComponentHealth, HealthStatus
from godzilla.market_data.instruments import InstrumentMasterSnapshot
from godzilla.storage.contracts import EventRepository, InstrumentRepository


class CriticalWriteFailure(RuntimeError):
    pass


class EventJournal:
    def __init__(self, repository: EventRepository, clock: Clock) -> None:
        self.repository, self.clock = repository, clock
        self._blocked = False
        self._validated = False

    def publish(
        self,
        event: EventEnvelope,
        key: str,
        *,
        state: SystemStateSnapshot | None = None,
        emit: Callable[[EventEnvelope], None] | None = None,
    ) -> bool:
        if self._blocked:
            raise CriticalWriteFailure("storage halt is latched; recovery review required")
        try:
            accepted = self.repository.append(event, key, state=state)
        except Exception:
            self._blocked = True
            # Driver errors may contain credentials, DSNs or payloads: do not leak them.
            raise CriticalWriteFailure("critical transaction failed; new entries blocked") from None
        self._validated = True
        if accepted and emit is not None:
            try:
                emit(event)
            except Exception:
                self._blocked = True
                raise CriticalWriteFailure(
                    "dispatch failed after commit; replay recovery required"
                ) from None
        return accepted

    def health(self) -> ComponentHealth:
        return ComponentHealth(
            component="event-persistence",
            observed_at=self.clock.now(),
            blocking=True,
            status=(
                HealthStatus.UNHEALTHY
                if self._blocked
                else HealthStatus.HEALTHY
                if self._validated
                else HealthStatus.UNKNOWN
            ),
            message="critical write halted" if self._blocked else "no critical write failure",
        )

    def save_instruments(
        self, repository: InstrumentRepository, snapshot: InstrumentMasterSnapshot
    ) -> None:
        if self._blocked:
            raise CriticalWriteFailure("storage halt is latched; recovery review required")
        try:
            repository.save_master(snapshot)
        except Exception:
            self._blocked = True
            raise CriticalWriteFailure(
                "critical instrument write failed; new entries blocked"
            ) from None
        self._validated = True
