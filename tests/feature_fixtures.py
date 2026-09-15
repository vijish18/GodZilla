from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from godzilla.core.events.models import BarClose, EventEnvelope, Provenance
from godzilla.features.models import FeatureContext, InstrumentRef
from godzilla.market_data.models import Bar


def feature_provenance():
    return Provenance(
        code_commit="feature-test",
        config_hash="0" * 64,
        data_snapshot="feature-fixture",
        feature_version="pending",
    )


def feature_context(**changes):
    return FeatureContext(
        entity=InstrumentRef(instrument_id="STOCK", source="fixture"),
        universe_version="fixture-universe-v1",
        known_at=datetime.fromisoformat("2026-09-01T00:00:00Z"),
        valid_from=datetime.fromisoformat("2026-09-01T00:00:00Z"),
        valid_to=datetime.fromisoformat("2026-10-01T00:00:00Z"),
        **changes,
    )


def bar_events(count=25, *, symbol="STOCK", day="2026-09-15", offset=100, volume=10):
    opening = datetime.fromisoformat(day + "T09:15:00+05:30")
    events = []
    for i in range(count):
        start = opening + timedelta(minutes=5 * i)
        close = Decimal(offset + i)
        bar = Bar(
            instrument_id=symbol,
            source="fixture",
            start=start,
            end=start + timedelta(minutes=5),
            received_at=start + timedelta(minutes=5),
            interval_minutes=5,
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=Decimal(volume),
        )
        events.append(
            EventEnvelope(
                event_id=uuid5(NAMESPACE_URL, f"{symbol}:{start}"),
                correlation_id=uuid5(NAMESPACE_URL, "features-fixture"),
                source="fixture",
                occurred_at=bar.end,
                received_at=bar.received_at,
                provenance=feature_provenance(),
                payload=BarClose(bar=bar),
            )
        )
    return tuple(events)
