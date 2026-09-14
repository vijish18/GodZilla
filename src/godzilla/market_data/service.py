"""Shared historical and streaming normalization without strategy dependencies."""

from datetime import datetime

from godzilla.core.clock import Clock
from godzilla.core.health import ComponentHealth, HealthStatus
from godzilla.market_data.aggregation import FiveMinuteAggregator
from godzilla.market_data.freshness import QuoteFreshnessTracker
from godzilla.market_data.interfaces import MarketDataProvider
from godzilla.market_data.metrics import Metrics
from godzilla.market_data.models import Bar, QualityFlag, Quote, utc
from godzilla.market_data.quality import BarQualityChecker
from godzilla.market_data.storage import NormalizedStorage


class MarketDataService:
    def __init__(
        self,
        provider: MarketDataProvider,
        clock: Clock,
        checker: BarQualityChecker,
        storage: NormalizedStorage,
        freshness: QuoteFreshnessTracker,
        metrics: Metrics,
    ) -> None:
        self.provider, self.clock, self.checker = provider, clock, checker
        self.storage, self.freshness, self.metrics = storage, freshness, metrics
        self._bars: list[Bar] = []
        self._seen: set[tuple[str, str, datetime]] = set()
        self._reported_missing: set[tuple[str, str, datetime]] = set()

    def ingest_bar(self, bar: Bar) -> None:
        if bar.interval_minutes != 1:
            raise ValueError("ingestion requires raw 1m bars")
        if max(bar.end, bar.received_at) > utc(self.clock.now()):
            self.metrics.increment("quality_violations")
            raise ValueError("bar is unavailable at current clock time")
        previous = max(
            (
                item
                for item in self._bars
                if item.instrument_id == bar.instrument_id
                and item.source == bar.source
                and item.end <= bar.start
            ),
            key=lambda item: item.end,
            default=None,
        )
        checked = self.checker.check(bar, previous)
        key = (bar.instrument_id, bar.source, bar.start)
        if key in self._seen:
            checked = checked.model_copy(
                update={"quality_flags": checked.quality_flags | {QualityFlag.DUPLICATE}}
            )
        self.metrics.increment("quality_violations", len(checked.quality_flags))
        self.storage.append_observation(
            checked, version=self.checker.settings.normalization_version
        )
        self._seen.add(key)
        self._bars.append(checked)

    def ingest_quote(self, quote: Quote) -> None:
        checked = self.freshness.update(quote)
        self.storage.append_observation(
            checked, version=self.checker.settings.normalization_version
        )

    def load_history(self, instrument: str, start: datetime, end: datetime) -> tuple[Bar, ...]:
        before = utc(self.clock.now())
        try:
            bars = self.provider.historical_bars(instrument, start, end, as_of=before)
        finally:
            self.metrics.observe(
                "provider_latency_seconds", (utc(self.clock.now()) - before).total_seconds()
            )
        for bar in sorted(bars, key=lambda item: (item.received_at, item.start)):
            if (
                bar.instrument_id != instrument
                or bar.start < utc(start)
                or bar.end > utc(end)
                or max(bar.end, bar.received_at) > before
            ):
                raise ValueError("provider returned data outside request scope")
            self.ingest_bar(bar)
        missing = self.checker.missing_intervals(bars, start, end, as_of=before)
        self.metrics.increment("missing_bars", len(missing))
        return tuple(
            bar
            for bar in self.canonical_bars()
            if bar.instrument_id == instrument and utc(start) <= bar.start and bar.end <= utc(end)
        )

    def window_health(
        self, instrument: str, source: str, start: datetime, end: datetime
    ) -> ComponentHealth:
        """Called at scheduled closes even when the provider emits no data."""
        bars = tuple(
            bar
            for bar in self._bars
            if bar.instrument_id == instrument
            and bar.source == source
            and utc(start) <= bar.start < utc(end)
        )
        missing = self.checker.missing_intervals(bars, start, end, as_of=self.clock.now())
        new_missing = {(instrument, source, stamp) for stamp in missing} - self._reported_missing
        self.metrics.increment("missing_bars", len(new_missing))
        self._reported_missing.update(new_missing)
        bad = any(bar.quality_flags for bar in bars)
        ready = utc(end) <= utc(self.clock.now()) and bool(bars) and not missing and not bad
        return ComponentHealth(
            component=f"bars:{instrument}:{source}",
            observed_at=utc(self.clock.now()),
            status=HealthStatus.HEALTHY if ready else HealthStatus.UNHEALTHY,
            blocking=True,
            message="validated window" if ready else "incomplete or invalid window",
            details={"missing_bars": len(missing)},
        )

    def refresh_quote(self, instrument: str) -> None:
        before = utc(self.clock.now())
        try:
            quote = self.provider.quote_snapshot(instrument)
        finally:
            self.metrics.observe(
                "provider_latency_seconds", (utc(self.clock.now()) - before).total_seconds()
            )
        if quote.instrument_id != instrument:
            raise ValueError("quote instrument differs from request")
        self.ingest_quote(quote)

    def canonical_bars(self) -> tuple[Bar, ...]:
        return FiveMinuteAggregator(self.checker).aggregate(
            tuple(self._bars), as_of=self.clock.now()
        )
