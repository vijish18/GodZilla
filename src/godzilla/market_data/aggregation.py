"""Session-aligned 1m to 5m reduction evaluated at an explicit availability cutoff."""

from datetime import datetime, timedelta

from godzilla.market_data.models import Bar, QualityFlag, utc
from godzilla.market_data.quality import BarQualityChecker, session_bounds


class FiveMinuteAggregator:
    def __init__(self, checker: BarQualityChecker) -> None:
        self.checker = checker

    def aggregate(self, bars: tuple[Bar, ...], *, as_of: datetime) -> tuple[Bar, ...]:
        cutoff = utc(as_of)
        groups: dict[tuple[str, str, datetime], list[Bar]] = {}
        available: list[Bar] = []
        for bar in sorted(bars, key=lambda item: (item.received_at, item.start)):
            if bar.interval_minutes != 1:
                raise ValueError("aggregation requires 1m inputs")
            if max(bar.end, bar.received_at) > cutoff:
                continue
            previous = max(
                (
                    item
                    for item in available
                    if item.instrument_id == bar.instrument_id
                    and item.source == bar.source
                    and item.end <= bar.start
                ),
                key=lambda item: item.end,
                default=None,
            )
            checked = self.checker.check(bar, previous)
            available.append(checked)
            bounds = session_bounds(self.checker.calendar, bar.start, self.checker.timezone)
            if bounds is None or checked.quality_flags & {
                QualityFlag.OUT_OF_SESSION,
                QualityFlag.MISALIGNED,
            }:
                continue
            bucket = bounds[0] + timedelta(
                minutes=5 * int((bar.start - bounds[0]).total_seconds() // 300)
            )
            groups.setdefault((bar.instrument_id, bar.source, bucket), []).append(checked)
        result: list[Bar] = []
        for (instrument, source, start), group in sorted(groups.items()):
            ordered = sorted(
                group, key=lambda bar: (bar.start, bar.received_at, bar.model_dump_json())
            )
            unique = {bar.start: bar for bar in reversed(ordered)}
            values = sorted(unique.values(), key=lambda bar: bar.start)
            flags = set().union(*(bar.quality_flags for bar in ordered))
            if len(unique) != len(ordered):
                flags.add(QualityFlag.DUPLICATE)
            expected = {start + timedelta(minutes=minute) for minute in range(5)}
            end = start + timedelta(minutes=5)
            complete = (
                set(unique) == expected and end <= cutoff and all(bar.complete for bar in values)
            )
            if not complete:
                flags.update({QualityFlag.INCOMPLETE, QualityFlag.MISSING_INTERVAL})
            result.append(
                Bar(
                    instrument_id=instrument,
                    source=source,
                    start=start,
                    end=end,
                    received_at=max(bar.received_at for bar in values),
                    interval_minutes=5,
                    open=values[0].open,
                    high=max(bar.high for bar in values),
                    low=min(bar.low for bar in values),
                    close=values[-1].close,
                    volume=sum((bar.volume for bar in values), start=values[0].volume * 0),
                    complete=complete,
                    quality_flags=frozenset(flags),
                    feed_kind=values[0].feed_kind,
                )
            )
        return tuple(result)
