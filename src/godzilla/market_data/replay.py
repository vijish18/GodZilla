"""Versioned local recordings and a deterministic vendor fake (no sleeps or network)."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from godzilla.core.clock import FixedClock
from godzilla.market_data.models import Bar, FeedKind, Quote, utc
from godzilla.market_data.providers import load_yaml_model


class ReplayRecording(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: str = "1"
    snapshot_id: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    bars: tuple[Bar, ...] = ()
    quotes: tuple[Quote, ...] = ()

    @model_validator(mode="after")
    def known_schema(self) -> "ReplayRecording":
        if self.schema_version != "1":
            raise ValueError("unsupported replay schema")
        return self


class ReplaySubscription:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RecordedProvider:
    def __init__(self, recording: ReplayRecording, clock: FixedClock) -> None:
        self.recording, self.clock = recording, clock

    @classmethod
    def from_yaml(cls, path: Path, clock: FixedClock) -> "RecordedProvider":
        return cls(load_yaml_model(path, ReplayRecording), clock)

    def feed_instruments(self, kind: FeedKind) -> tuple[str, ...]:
        return tuple(
            sorted({bar.instrument_id for bar in self.recording.bars if bar.feed_kind is kind})
        )

    def historical_bars(
        self, instrument_id: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[Bar, ...]:
        cutoff = min(utc(as_of), utc(self.clock.now()))
        return tuple(
            bar
            for bar in self.recording.bars
            if bar.instrument_id == instrument_id
            and utc(start) <= bar.start
            and bar.end <= utc(end)
            and max(bar.end, bar.received_at) <= cutoff
        )

    def quote_snapshot(self, instrument_id: str) -> Quote:
        available = [
            quote
            for quote in self.recording.quotes
            if quote.instrument_id == instrument_id
            and max(quote.timestamp, quote.received_at) <= utc(self.clock.now())
        ]
        if not available:
            raise ValueError("no quote available at replay clock")
        return max(available, key=lambda quote: (quote.timestamp, quote.received_at))

    def subscribe(
        self,
        instruments: tuple[str, ...],
        on_bar: Callable[[Bar], None],
        on_quote: Callable[[Quote], None],
        on_disconnect: Callable[[str], None],
    ) -> ReplaySubscription:
        subscription = ReplaySubscription()
        events: list[Bar | Quote] = [*self.recording.bars, *self.recording.quotes]
        for event in sorted(events, key=lambda item: (item.received_at, item.model_dump_json())):
            if event.instrument_id not in instruments or event.received_at < self.clock.now():
                continue
            self.clock.set(event.received_at)
            if isinstance(event, Bar):
                on_bar(event)
            else:
                on_quote(event)
        on_disconnect("recording exhausted")
        subscription.close()
        return subscription
