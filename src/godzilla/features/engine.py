"""One causal implementation for batch history and incremental event ingestion."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from statistics import fmean, median

from godzilla.core.events.models import BarClose, EventEnvelope, Provenance, QuoteUpdate
from godzilla.core.events.serialization import event_hash
from godzilla.features.math import covariance, population_std
from godzilla.features.models import (
    FeatureContext,
    FeatureSetVersion,
    FeatureSnapshot,
    InstrumentRef,
    MissingReason,
    content_hash,
)
from godzilla.features.series import HORIZONS, Values, series_features, typical
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar
from godzilla.market_data.models import Bar, FeedKind, QualityFlag, Quote, utc
from godzilla.market_data.quality import price_flags, session_bounds


class CausalFeatureEngine:
    def __init__(
        self,
        calendar: ExchangeCalendar,
        timezone: str,
        version: FeatureSetVersion,
        provenance: Provenance,
    ) -> None:
        self.calendar, self.timezone, self.version, self.provenance = (
            calendar,
            timezone,
            version,
            provenance,
        )
        self._events: dict[str, EventEnvelope] = {}

    def on_event(self, event: EventEnvelope) -> None:
        identity = str(event.event_id)
        previous = self._events.get(identity)
        if previous and event_hash(previous) != event_hash(event):
            raise ValueError("conflicting feature input event")
        self._events[identity] = event

    def snapshot(self, context: FeatureContext, at: datetime) -> FeatureSnapshot:
        at = utc(at)
        if context.known_at > at or not context.valid_from <= at < context.valid_to:
            raise ValueError("feature context unavailable or outside effective window")
        refs = {context.entity, *context.breadth_members, *context.sector_members}
        refs.update(
            ref for ref in (context.nifty, context.sector, context.vix, context.pair) if ref
        )
        selected = []
        for event in self._events.values():
            if event.received_at > at:
                continue
            if isinstance(event.payload, BarClose) and event.payload.bar.interval_minutes != 5:
                continue
            observation = (
                event.payload.bar
                if isinstance(event.payload, BarClose)
                else event.payload.quote
                if isinstance(event.payload, QuoteUpdate)
                else None
            )
            if (
                observation
                and InstrumentRef(
                    instrument_id=observation.instrument_id, source=observation.source
                )
                in refs
            ):
                selected.append(event)
        selected.sort(key=lambda item: (item.received_at, str(item.event_id)))
        bars = tuple(event.payload.bar for event in selected if isinstance(event.payload, BarClose))
        quotes = tuple(
            event.payload.quote for event in selected if isinstance(event.payload, QuoteUpdate)
        )
        bounds = session_bounds(self.calendar, at, self.timezone)
        if bounds is None:
            raise ValueError("no confirmed session for feature snapshot")
        opening, closing = bounds
        endpoint = min(
            closing,
            opening + timedelta(minutes=5 * max(0, int((at - opening).total_seconds() // 300))),
        )
        result = Values()
        own: dict[str, tuple[Bar, ...]] = {}
        for prefix, ref in (
            ("stock", context.entity),
            ("market", context.nifty),
            ("sector", context.sector),
        ):
            series, reason = self._session(bars, ref, opening, endpoint, at)
            own[prefix] = series
            series_features(result, prefix, series, self.version, reason)
            prior = self._prior_sessions(bars, ref, opening, at)
            previous = prior[-1][-1].close if prior and prior[-1] else None
            result.put(
                f"{prefix}.opening_gap",
                float(series[0].open / previous - 1) if series and previous else None,
                MissingReason.MISSING_INPUT,
            )
        self._relative(result)
        self._breadth(result, "market", context.breadth_members, bars, opening, endpoint, at)
        self._breadth(result, "sector", context.sector_members, bars, opening, endpoint, at)
        vix, vix_reason = self._session(bars, context.vix, opening, endpoint, at)
        result.put("market.vix_level", float(vix[-1].close) if vix else None, vix_reason)
        result.put(
            "market.vix_change",
            float(vix[-1].close / vix[-2].close - 1) if len(vix) >= 2 else None,
            vix_reason,
        )
        self._liquidity(result, context, own["stock"], bars, quotes, opening, at)
        pair, _ = self._session(bars, context.pair, opening, endpoint, at)
        self._pairs(result, own["stock"], pair)
        inputs = [event_hash(event) for event in selected]
        input_hash = hashlib.sha256(json.dumps(inputs, separators=(",", ":")).encode()).hexdigest()
        return FeatureSnapshot(
            entity=context.entity.instrument_id,
            timestamp=at,
            feature_set_hash=self.version.version_hash(),
            context_hash=content_hash(context),
            input_hash=input_hash,
            code_commit=self.provenance.code_commit,
            config_hash=self.provenance.config_hash,
            data_snapshot=self.provenance.data_snapshot,
            values=tuple(result.values[name] for name in sorted(result.values)),
        )

    def _session(
        self,
        bars: tuple[Bar, ...],
        ref: InstrumentRef | None,
        opening: datetime,
        endpoint: datetime,
        at: datetime,
    ) -> tuple[tuple[Bar, ...], MissingReason]:
        if ref is None:
            return (), MissingReason.MISSING_INPUT
        selected = [
            bar
            for bar in bars
            if bar.instrument_id == ref.instrument_id
            and bar.source == ref.source
            and opening <= bar.start < endpoint
            and bar.end <= endpoint
        ]
        expected = int((endpoint - opening).total_seconds() // 300)
        if not selected:
            return (), MissingReason.MISSING_INPUT
        starts = {bar.start for bar in selected}
        if len(starts) != len(selected):
            return (), MissingReason.INVALID_INPUT
        try:
            for bar in selected:
                if bar.feed_kind is FeedKind.EQUITY:
                    bar.require_strategy_ready(at)
                elif (
                    not bar.complete
                    or (bar.quality_flags | price_flags(bar)) - {QualityFlag.ZERO_VOLUME}
                    or max(bar.end, bar.received_at) > at
                ):
                    return (), MissingReason.INVALID_INPUT
                if not all(
                    math.isfinite(float(value))
                    for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)
                ):
                    return (), MissingReason.INVALID_INPUT
        except ValueError:
            return (), MissingReason.INVALID_INPUT
        if starts != {opening + timedelta(minutes=5 * i) for i in range(expected)}:
            return (), MissingReason.MISSING_INPUT
        return tuple(sorted(selected, key=lambda bar: bar.start)), MissingReason.WARMUP

    def _prior_sessions(
        self, bars: tuple[Bar, ...], ref: InstrumentRef | None, opening: datetime, at: datetime
    ) -> tuple[tuple[Bar, ...], ...]:
        if ref is None:
            return ()
        sessions: list[tuple[Bar, ...]] = []
        day = opening
        # A finite search also supports calendars with long closures.
        for _ in range(366):
            day -= timedelta(days=1)
            try:
                window = session_bounds(self.calendar, day, self.timezone)
            except CalendarCoverageError:
                break
            if window is None:
                continue
            begin, end = window
            series, _ = self._session(bars, ref, begin, end, at)
            sessions.append(series if series and series[-1].end == end else ())
            if len(sessions) == self.version.baseline_sessions:
                break
        return tuple(reversed(sessions))

    @staticmethod
    def _relative(result: Values) -> None:
        for horizon in HORIZONS:
            for target, benchmark in (
                ("stock", "market"),
                ("stock", "sector"),
                ("sector", "market"),
            ):
                left = result.values[f"{target}.return_{horizon}m"].value
                right = result.values[f"{benchmark}.return_{horizon}m"].value
                result.put(
                    f"{target}.relative_{benchmark}_{horizon}m",
                    left - right if left is not None and right is not None else None,
                    MissingReason.MISSING_INPUT,
                )

    def _breadth(
        self,
        result: Values,
        prefix: str,
        members: tuple[InstrumentRef, ...],
        bars: tuple[Bar, ...],
        opening: datetime,
        endpoint: datetime,
        at: datetime,
    ) -> None:
        returns = []
        for ref in members:
            series, _ = self._session(bars, ref, opening, endpoint, at)
            if len(series) >= 2:
                returns.append(float(series[-1].close / series[-2].close - 1))
        result.put(
            f"{prefix}.breadth_coverage",
            len(returns) / len(members) if members else None,
            MissingReason.MISSING_INPUT,
        )
        complete = bool(members) and len(returns) == len(members)
        result.put(
            f"{prefix}.breadth_advancing",
            sum(value > 0 for value in returns) / len(returns) if complete else None,
            MissingReason.MISSING_INPUT,
        )
        result.put(
            f"{prefix}.breadth_declining",
            sum(value < 0 for value in returns) / len(returns) if complete else None,
            MissingReason.MISSING_INPUT,
        )

    def _liquidity(
        self,
        result: Values,
        context: FeatureContext,
        current: tuple[Bar, ...],
        bars: tuple[Bar, ...],
        quotes: tuple[Quote, ...],
        opening: datetime,
        at: datetime,
    ) -> None:
        relevant = sorted(
            (
                quote
                for quote in quotes
                if quote.instrument_id == context.entity.instrument_id
                and quote.source == context.entity.source
                and quote.received_at <= at
            ),
            key=lambda quote: (quote.received_at, quote.timestamp),
        )
        quote = relevant[-1] if relevant else None
        age = (at - quote.timestamp).total_seconds() if quote else None
        valid = (
            quote is not None
            and not quote.quality_flags
            and min(quote.bid, quote.ask, quote.last) > 0
            and quote.bid <= quote.ask
            and quote.timestamp <= quote.received_at
        )
        fresh = valid and age is not None and 0 <= age <= self.version.quote_stale_seconds
        result.put("liquidity.quote_age", age, MissingReason.MISSING_INPUT)
        result.put(
            "liquidity.spread_bps",
            float((quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2) * 10000)
            if fresh and quote
            else None,
            MissingReason.STALE if valid else MissingReason.INVALID_INPUT,
        )
        spreads = [
            float((q.ask - q.bid) / ((q.ask + q.bid) / 2) * 10000)
            for q in relevant
            if not q.quality_flags
            and 0 <= (at - q.timestamp).total_seconds() <= self.version.spread_window_seconds
            and q.timestamp <= q.received_at
            and 0 < q.bid <= q.ask
            and q.last > 0
        ]
        result.put(
            "liquidity.median_spread_bps",
            median(spreads) if fresh and spreads else None,
            MissingReason.STALE,
        )
        prior = self._prior_sessions(bars, context.entity, opening, at)
        sessions = prior[-self.version.baseline_sessions :]
        ready = len(sessions) == self.version.baseline_sessions and all(sessions)
        adv = (
            fmean(sum(float(bar.volume) for bar in session) for session in sessions)
            if ready
            else None
        )
        turnover = (
            fmean(sum(typical(bar) * float(bar.volume) for bar in session) for session in sessions)
            if ready
            else None
        )
        result.put("liquidity.adv", adv)
        result.put("liquidity.average_daily_turnover", turnover)
        quantity = context.proposed_quantity
        result.put(
            "liquidity.adv_participation",
            quantity / adv if quantity is not None and adv else None,
            MissingReason.MISSING_INPUT,
        )
        result.put(
            "liquidity.turnover_participation",
            quantity * float((quote.bid + quote.ask) / 2) / turnover
            if quantity is not None and turnover and fresh and quote
            else None,
            MissingReason.MISSING_INPUT,
        )
        slot = len(current) - 1
        matching = [session[slot] for session in sessions if 0 <= slot < len(session)]
        enough = ready and len(matching) == len(sessions) and bool(current)
        volume_base = fmean(float(bar.volume) for bar in matching) if enough else 0
        turnover_base = fmean(typical(bar) * float(bar.volume) for bar in matching) if enough else 0
        result.put(
            "stock.relative_volume",
            float(current[-1].volume) / volume_base if volume_base else None,
        )
        result.put(
            "stock.relative_turnover",
            typical(current[-1]) * float(current[-1].volume) / turnover_base
            if turnover_base
            else None,
        )

    def _pairs(self, result: Values, left: tuple[Bar, ...], right: tuple[Bar, ...]) -> None:
        n = self.version.statistics_bars
        ready = len(left) >= n + 1 and len(right) >= n + 1
        a = tuple(math.log(float(bar.close)) for bar in left[-n - 1 :])
        b = tuple(math.log(float(bar.close)) for bar in right[-n - 1 :])
        result.put("pairs.log_left", a[-1] if a else None)
        result.put("pairs.log_right", b[-1] if b else None)
        cov = covariance(a[:-1], b[:-1]) if ready else None
        var = covariance(b[:-1], b[:-1]) if ready else None
        beta = cov / var if cov is not None and var else None
        result.put("pairs.log_covariance", cov)
        result.put("pairs.log_variance_right", var)
        result.put(
            "pairs.hedge_ratio",
            beta,
            MissingReason.ZERO_DENOMINATOR if ready else MissingReason.WARMUP,
        )
        spreads = (
            tuple(x - beta * y for x, y in zip(a[:-1], b[:-1], strict=True))
            if beta is not None
            else ()
        )
        mean = fmean(spreads) if spreads else None
        std = population_std(spreads) if spreads else None
        spread = a[-1] - beta * b[-1] if beta is not None else None
        result.put("pairs.spread", spread)
        result.put("pairs.spread_mean", mean)
        result.put("pairs.spread_std", std)
        result.put(
            "pairs.zscore",
            (spread - mean) / std
            if spread is not None and mean is not None and std and std > 1e-12
            else None,
            MissingReason.ZERO_DENOMINATOR if ready else MissingReason.WARMUP,
        )
        denominator = population_std(a[:-1]) * population_std(b[:-1]) if ready else 0
        result.put(
            "pairs.log_correlation", cov / denominator if cov is not None and denominator else None
        )
        result.put("pairs.stationarity_pvalue", None, MissingReason.NOT_IMPLEMENTED)
        result.put("pairs.half_life", None, MissingReason.NOT_IMPLEMENTED)


def batch_snapshot(
    events: tuple[EventEnvelope, ...],
    context: FeatureContext,
    at: datetime,
    *,
    calendar: ExchangeCalendar,
    timezone: str,
    version: FeatureSetVersion,
    provenance: Provenance,
) -> FeatureSnapshot:
    engine = CausalFeatureEngine(calendar, timezone, version, provenance)
    for event in events:
        engine.on_event(event)
    return engine.snapshot(context, at)
