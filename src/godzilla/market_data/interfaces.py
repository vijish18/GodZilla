"""Provider capabilities are explicit; market-data adapters never place orders."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from godzilla.market_data.models import Bar, FeedKind, Quote


class MarketDataProvider(Protocol):
    def historical_bars(
        self, instrument_id: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[Bar, ...]:
        """Return observations available by as_of in the half-open requested window."""
        ...

    def quote_snapshot(self, instrument_id: str) -> Quote: ...


class Subscription(Protocol):
    def close(self) -> None: ...


class StreamingMarketDataProvider(MarketDataProvider, Protocol):
    """Optional capability; callbacks must be serialized by an adapter."""

    def subscribe(
        self,
        instruments: tuple[str, ...],
        on_bar: Callable[[Bar], None],
        on_quote: Callable[[Quote], None],
        on_disconnect: Callable[[str], None],
    ) -> Subscription: ...


class ContextFeedProvider(Protocol):
    """INDEX, SECTOR and VIX use the same causal bar contract as equities."""

    def feed_instruments(self, kind: FeedKind) -> tuple[str, ...]: ...

    def historical_bars(
        self, instrument_id: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[Bar, ...]: ...
