"""Bounded retry scheduling and a recovery latch; no strategy activation side effects."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from godzilla.core.clock import Clock
from godzilla.core.health import ComponentHealth, HealthStatus
from godzilla.market_data.freshness import QuoteFreshnessTracker
from godzilla.market_data.metrics import Metrics
from godzilla.market_data.models import utc


class Backoff(Protocol):
    def delay(self, attempt: int) -> timedelta | None: ...


@dataclass(frozen=True)
class ExponentialBackoff:
    initial_seconds: float = 1
    maximum_seconds: float = 30
    max_attempts: int = 5

    def __post_init__(self) -> None:
        if not 0 < self.initial_seconds <= self.maximum_seconds or self.max_attempts < 1:
            raise ValueError("invalid backoff limits")

    def delay(self, attempt: int) -> timedelta | None:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        if attempt > self.max_attempts:
            return None
        return timedelta(
            seconds=min(self.maximum_seconds, self.initial_seconds * 2 ** (attempt - 1))
        )


class StreamState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"
    RECOVERY_VALIDATED = "RECOVERY_VALIDATED"
    EXHAUSTED = "EXHAUSTED"


class ReconnectController:
    def __init__(self, clock: Clock, backoff: Backoff, metrics: Metrics) -> None:
        self.clock, self.backoff, self.metrics = clock, backoff, metrics
        self.state = StreamState.DISCONNECTED
        self.attempts = 0
        self.connected_at: datetime | None = None

    def disconnected(self, reason: str) -> None:
        if not reason:
            raise ValueError("disconnect requires a reason")
        self.state = StreamState.DISCONNECTED
        self.connected_at = None

    def next_retry_at(self) -> datetime | None:
        if self.state not in {StreamState.DISCONNECTED, StreamState.EXHAUSTED}:
            raise ValueError("stream is already connected")
        self.attempts += 1
        delay = self.backoff.delay(self.attempts)
        if delay is None:
            self.state = StreamState.EXHAUSTED
            return None
        self.metrics.increment("reconnects")
        return utc(self.clock.now()) + delay

    def connected(self) -> None:
        self.state = StreamState.SNAPSHOT_REQUIRED
        self.connected_at = utc(self.clock.now())

    def validate_recovery(
        self, tracker: QuoteFreshnessTracker, instruments: tuple[str, ...], *, gaps_reconciled: bool
    ) -> None:
        if (
            self.state is not StreamState.SNAPSHOT_REQUIRED
            or not instruments
            or not gaps_reconciled
        ):
            raise ValueError("snapshot and gap reconciliation required")
        for instrument in instruments:
            quote = tracker.require_fresh(instrument)
            if self.connected_at is None or quote.received_at < self.connected_at:
                raise ValueError("fresh post-connect snapshot required")
        self.state = StreamState.RECOVERY_VALIDATED
        self.attempts = 0

    def health(self) -> ComponentHealth:
        return ComponentHealth(
            component="market-data-stream",
            observed_at=utc(self.clock.now()),
            blocking=True,
            status=(
                HealthStatus.HEALTHY
                if self.state is StreamState.RECOVERY_VALIDATED
                else HealthStatus.UNHEALTHY
            ),
            message=self.state.value,
        )
