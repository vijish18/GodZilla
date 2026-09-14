"""Freshness is recomputed from exchange time on every read, never cached as healthy."""

from godzilla.core.clock import Clock
from godzilla.core.health import ComponentHealth, HealthStatus
from godzilla.market_data.metrics import Metrics
from godzilla.market_data.models import QualityFlag, Quote, utc


class QuoteFreshnessTracker:
    def __init__(self, clock: Clock, stale_seconds: float, metrics: Metrics) -> None:
        if stale_seconds <= 0:
            raise ValueError("stale_seconds must be positive")
        self.clock = clock
        self.stale_seconds = stale_seconds
        self.metrics = metrics
        self._quotes: dict[str, Quote] = {}
        self._invalid: set[str] = set()

    def update(self, quote: Quote) -> Quote:
        now = utc(self.clock.now())
        flags = set(quote.quality_flags)
        if min(quote.bid, quote.ask, quote.last) <= 0:
            flags.add(QualityFlag.INVALID_PRICE)
        if quote.bid > quote.ask:
            flags.add(QualityFlag.CROSSED_QUOTE)
        if quote.timestamp > quote.received_at or quote.received_at > now:
            flags.add(QualityFlag.FUTURE_TIMESTAMP)
        if (now - quote.timestamp).total_seconds() > self.stale_seconds:
            flags.add(QualityFlag.STALE)
        previous = self._quotes.get(quote.instrument_id)
        if previous and quote.timestamp <= previous.timestamp:
            flags.add(QualityFlag.DUPLICATE)
        checked = quote.model_copy(update={"quality_flags": frozenset(flags)})
        self.metrics.observe("quote_age_seconds", (now - quote.timestamp).total_seconds())
        if flags:
            self._invalid.add(quote.instrument_id)
            self.metrics.increment("quality_violations", len(flags))
        else:
            self._quotes[quote.instrument_id] = checked
            self._invalid.discard(quote.instrument_id)
        return checked

    def health(self, instrument_id: str) -> ComponentHealth:
        now = utc(self.clock.now())
        quote = self._quotes.get(instrument_id)
        age = (now - quote.timestamp).total_seconds() if quote else None
        if age is not None:
            self.metrics.observe("quote_age_seconds", age)
        healthy = (
            age is not None
            and 0 <= age <= self.stale_seconds
            and instrument_id not in self._invalid
        )
        return ComponentHealth(
            component=f"quote:{instrument_id}",
            status=HealthStatus.HEALTHY if healthy else HealthStatus.UNHEALTHY,
            observed_at=now,
            blocking=True,
            message="fresh" if healthy else "missing, stale or invalid quote",
            details={"quote_age_seconds": age},
        )

    def require_fresh(self, instrument_id: str) -> Quote:
        if self.health(instrument_id).status is not HealthStatus.HEALTHY:
            raise ValueError("execution quote unavailable or stale")
        return self._quotes[instrument_id]
