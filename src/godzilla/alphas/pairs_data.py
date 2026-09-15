"""Point-in-time, synchronized pair training data and two-leg liquidity checks."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from godzilla.alphas.pairs_models import (
    PairCandidate,
    PairLiquidity,
    PairObservation,
    PairsSettings,
)
from godzilla.market_data.calendar import ExchangeCalendar
from godzilla.market_data.models import FeedKind
from godzilla.market_data.quality import session_bounds


def select_window(
    observations: tuple[PairObservation, ...],
    candidate: PairCandidate,
    cfg: PairsSettings,
    calendar: ExchangeCalendar,
    timezone: str,
    at: datetime,
) -> tuple[PairObservation, ...]:
    available = sorted(
        (
            o
            for o in observations
            if max(o.left.end, o.right.end, o.left.received_at, o.right.received_at) <= at
        ),
        key=lambda o: o.left.end,
    )
    if len(available) < cfg.training_bars + 1:
        raise ValueError("INSUFFICIENT_HISTORY")
    window = tuple(available[-cfg.training_bars - 1 :])
    if not 0 <= (at - window[-1].left.end).total_seconds() <= cfg.max_bar_lag_seconds:
        raise ValueError("STALE_PAIR_BAR")
    if candidate.effective_from > window[0].left.start:
        raise ValueError("ADJUSTMENT_COVERAGE_INSUFFICIENT")
    for row in window:
        if row.left.start != row.right.start or row.left.end != row.right.end:
            raise ValueError("UNSYNCHRONIZED_LEGS")
        for bar, ref in ((row.left, candidate.left), (row.right, candidate.right)):
            bar.require_strategy_ready(at)
            if (
                bar.instrument_id != ref.instrument_id
                or bar.source != ref.source
                or bar.feed_kind is not FeedKind.EQUITY
            ):
                raise ValueError("PAIR_BAR_IDENTITY")
    # Enforce every expected trading slot across sessions, never fill a gap or weekend.
    first = window[0].left.start
    start_day = first.astimezone(ZoneInfo(timezone)).date()
    end_day = window[-1].left.end.astimezone(ZoneInfo(timezone)).date()
    if (end_day - start_day).days > 366:
        raise ValueError("HISTORY_TOO_SPARSE")
    expected = []
    for day_offset in range((end_day - start_day).days + 1):
        instant = first + timedelta(days=day_offset)
        bounds = session_bounds(calendar, instant, timezone)
        if bounds is None:
            continue
        begin, end = bounds
        cursor = begin
        while cursor + timedelta(minutes=5) <= end:
            if first <= cursor and cursor + timedelta(minutes=5) <= window[-1].left.end:
                expected.append(cursor)
            cursor += timedelta(minutes=5)
    if tuple(expected) != tuple(o.left.start for o in window):
        raise ValueError("MISSING_DUPLICATE_OR_MISALIGNED_PAIR_BARS")
    return window


def liquid(
    left: PairLiquidity,
    right: PairLiquidity,
    candidate: PairCandidate,
    cfg: PairsSettings,
    at: datetime,
) -> bool:
    for item, ref in ((left, candidate.left), (right, candidate.right)):
        quote = item.quote
        if (
            quote.instrument_id != ref.instrument_id
            or quote.source != ref.source
            or quote.quality_flags
            or not 0 < quote.bid <= quote.ask
            or quote.last <= 0
            or not quote.timestamp <= quote.received_at <= at
            or not 0 <= (at - quote.timestamp).total_seconds() <= cfg.quote_max_age_seconds
            or not 0
            <= (at - item.known_at).total_seconds()
            <= cfg.liquidity_metadata_max_age_seconds
            or item.adv < cfg.min_adv
            or item.daily_turnover < cfg.min_turnover
        ):
            return False
        spread = float((quote.ask - quote.bid) / ((quote.bid + quote.ask) / 2) * 10000)
        if spread > cfg.max_spread_bps:
            return False
    return True
