"""Causal cross-sectional ranks and directional intent selection, before portfolio sizing."""

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from godzilla.alphas.momentum_models import (
    ALPHA_ID,
    MomentumObservation,
    MomentumScore,
    MomentumSettings,
    MomentumSignalEvidence,
    ScoreComponent,
)
from godzilla.alphas.ranking import percentiles, without_recent
from godzilla.core.events.models import SignalIntent
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import utc
from godzilla.market_state.models import AlphaFamily, MarketState, StateDecision
from godzilla.universe.builder import UniverseSnapshot

RETURN_NAMES = (
    "return_30m",
    "return_60m",
    "return_120m",
    "relative_sector_30m",
    "relative_market_30m",
)


class MomentumResult(FeatureModel):
    timestamp: datetime
    scores: tuple[MomentumScore, ...]
    intents: tuple[SignalIntent, ...]
    reasons: tuple[str, ...]
    cross_section_hash: str
    settings: MomentumSettings
    code_commit: str
    data_snapshot: str


def _number(observation: MomentumObservation, name: str) -> float:
    values = {item.name: item.value for item in observation.snapshot.values}
    value = values.get(name)
    if value is None:
        raise ValueError("missing required feature: " + name)
    return value


def _returns(item: MomentumObservation, cfg: MomentumSettings) -> tuple[float, ...]:
    stock = tuple(_number(item, "stock.return_" + str(h) + "m") for h in (30, 60, 120))
    sector, market = _number(item, "sector.return_30m"), _number(item, "market.return_30m")
    if min(*stock, sector, market) <= -1:
        raise ValueError("invalid price return")
    if cfg.exclude_recent_5m:
        stock = tuple(without_recent(r, _number(item, "stock.return_5m")) for r in stock)
        sector = without_recent(sector, _number(item, "sector.return_5m"))
        market = without_recent(market, _number(item, "market.return_5m"))
    return (*stock, stock[0] - sector, stock[0] - market)


def _confirmation(
    item: MomentumObservation, cfg: MomentumSettings
) -> tuple[bool, bool, bool, float, float]:
    trend = tuple(
        _number(item, key)
        for key in (
            "stock.vwap_distance",
            "stock.ema_20_slope",
            "sector.vwap_distance",
        )
    )
    spread, age, adv, turnover = tuple(
        _number(item, key)
        for key in (
            "liquidity.spread_bps",
            "liquidity.quote_age",
            "liquidity.adv",
            "liquidity.average_daily_turnover",
        )
    )
    valid = (
        0 <= spread <= cfg.max_spread_bps
        and 0 <= age <= cfg.max_quote_age_seconds
        and adv >= cfg.min_adv
        and turnover >= cfg.min_daily_turnover
    )
    quality = (
        max(
            0,
            min(1, ((1 - spread / cfg.max_spread_bps) + (1 - age / cfg.max_quote_age_seconds)) / 2),
        )
        if valid
        else 0
    )
    trend_quality = sum(1 if value > 0 else 0 if value < 0 else 0.5 for value in trend) / len(trend)
    return all(v > 0 for v in trend), all(v < 0 for v in trend), valid, trend_quality, quality


class CrossSectionalMomentum:
    def __init__(self, settings: MomentumSettings, timezone: str = "Asia/Kolkata") -> None:
        self.settings, self.timezone = settings, timezone

    def evaluate(
        self,
        observations: tuple[MomentumObservation, ...],
        universe: UniverseSnapshot,
        master: InstrumentMasterSnapshot,
        state: StateDecision,
        at: datetime,
    ) -> MomentumResult:
        at = utc(at)
        # Future snapshots do not enter either ranks or their provenance hash.
        selected = tuple(
            sorted(
                (o for o in observations if o.snapshot.timestamp == at),
                key=lambda o: o.snapshot.entity,
            )
        )
        digest = content_hash(_CrossSection(observations=selected))
        empty = MomentumResult(
            timestamp=at,
            scores=(),
            intents=(),
            reasons=(),
            cross_section_hash=digest,
            settings=self.settings,
            code_commit=state.code_commit,
            data_snapshot=state.data_snapshot,
        )
        cfg = self.settings
        if not cfg.enabled:
            return empty.model_copy(update={"reasons": ("ALPHA_DISABLED",)})
        day = at.astimezone(ZoneInfo(self.timezone)).date()
        if (
            utc(universe.generated_at) > at
            or utc(master.generated_at) > at
            or universe.as_of != day
            or master.as_of != day
            or universe.instrument_snapshot_id != master.snapshot_id
        ):
            return empty.model_copy(update={"reasons": ("UNIVERSE_UNAVAILABLE",)})
        expected = set(universe.long_tokens)
        by_token = {o.snapshot.entity: o for o in selected}
        instruments = {i.token: i for i in master.instruments}
        if (
            len(expected) < cfg.minimum_universe_size
            or len(instruments) != len(master.instruments)
            or len(expected) != len(universe.long_tokens)
            or len(by_token) != len(selected)
            or set(by_token) != expected
            or not expected <= instruments.keys()
            or not set(universe.short_tokens) <= expected
            or len({instruments[t].symbol for t in expected}) != len(expected)
        ):
            return empty.model_copy(update={"reasons": ("INCOMPLETE_OR_AMBIGUOUS_CROSS_SECTION",)})
        try:
            for token, o in by_token.items():
                instrument, context = instruments[token], o.context
                if (
                    instrument.status is not InstrumentStatus.ACTIVE
                    or not instrument.active_on(day)
                    or instrument.surveillance
                    or instrument.data_ambiguous
                    or context.entity.instrument_id != token
                    or context.sector is None
                    or context.nifty is None
                    or context.universe_version != str(universe.snapshot_id)
                    or context.known_at > at
                    or not context.valid_from <= at < context.valid_to
                    or content_hash(context) != o.snapshot.context_hash
                    or (
                        o.snapshot.code_commit,
                        o.snapshot.config_hash,
                        o.snapshot.data_snapshot,
                        o.snapshot.feature_set_hash,
                    )
                    != (
                        state.code_commit,
                        state.config_hash,
                        state.data_snapshot,
                        state.evidence.feature_set_hash,
                    )
                ):
                    raise ValueError("invalid context/provenance")
            returns = tuple(_returns(o, cfg) for o in selected)
            confirmations = tuple(_confirmation(o, cfg) for o in selected)
        except ValueError as error:
            return empty.model_copy(update={"reasons": ("INVALID_OR_MISSING_INPUT", str(error))})
        ranks = tuple(percentiles(tuple(row[i] for row in returns)) for i in range(5))
        weights = cfg.weights.model_dump()
        cards = []
        for index, o in enumerate(selected):
            long_ok, short_ok, liquid, trend, quality = confirmations[index]
            raw = (*returns[index], trend, quality)
            ranked = (*(rank[index] for rank in ranks), trend, quality)
            components = tuple(
                ScoreComponent(
                    name=name,
                    raw_value=value,
                    rank_value=p,
                    weight=weights[name],
                    long_contribution=weights[name] * p,
                    short_contribution=weights[name]
                    * (p if name == "liquidity_quality" else 1 - p),
                )
                for name, value, p in zip(
                    (*RETURN_NAMES, "trend_quality", "liquidity_quality"), raw, ranked, strict=True
                )
            )
            long_score = sum(c.long_contribution for c in components)
            short_score = sum(c.short_contribution for c in components)
            strength = sum(
                c.long_contribution for c in components if c.name != "liquidity_quality"
            ) / (1 - cfg.weights.liquidity_quality)
            instrument = instruments[o.snapshot.entity]
            cards.append(
                MomentumScore(
                    instrument_id=o.snapshot.entity,
                    symbol=instrument.symbol,
                    sector=instrument.sector,
                    strength=min(1, max(0, strength)),
                    percentile=0.5,
                    long_score=min(1, long_score),
                    short_score=min(1, short_score),
                    components=components,
                    long_confirmed=long_ok,
                    short_confirmed=short_ok,
                    liquidity_confirmed=liquid,
                    reasons=(
                        "LONG_TREND_CONFIRMED" if long_ok else "LONG_TREND_REJECTED",
                        "SHORT_TREND_CONFIRMED" if short_ok else "SHORT_TREND_REJECTED",
                        "LIQUIDITY_CONFIRMED" if liquid else "LIQUIDITY_REJECTED",
                    ),
                    feature_hash=o.snapshot.snapshot_hash(),
                )
            )
        scores = tuple(
            card.model_copy(update={"percentile": p})
            for card, p in zip(
                cards, percentiles(tuple(card.strength for card in cards)), strict=True
            )
        )
        permitted = (
            state.timestamp == at
            and state.gross_risk_multiplier > 0
            and AlphaFamily.CROSS_SECTIONAL_MOMENTUM in state.allowed_alpha_families
        )
        side: Literal["LONG", "SHORT"] | None = (
            "LONG"
            if state.state is MarketState.TREND_UP and "LONG" in state.directional_sides
            else "SHORT"
            if state.state is MarketState.TREND_DOWN and "SHORT" in state.directional_sides
            else None
        )
        if not permitted or side is None:
            return empty.model_copy(
                update={"scores": scores, "reasons": ("STATE_PERMISSION_DENIED",)}
            )
        fraction = cfg.long_tail_fraction if side == "LONG" else cfg.short_tail_fraction
        tail = tuple(
            s
            for s in scores
            if (s.percentile >= 1 - fraction if side == "LONG" else s.percentile <= fraction)
        )
        cap = min(cfg.max_candidates_per_side, int(len(scores) * fraction))
        ordered = sorted(
            tail, key=lambda s: (-s.strength if side == "LONG" else s.strength, s.instrument_id)
        )
        # Drop the entire boundary tie group when a cap would split it.
        if 0 < cap < len(ordered) and ordered[cap - 1].strength == ordered[cap].strength:
            boundary = ordered[cap].strength
            ordered = [s for s in ordered[:cap] if s.strength != boundary]
        else:
            ordered = ordered[:cap]
        intents = []
        for card in ordered:
            if not card.liquidity_confirmed or not (
                card.long_confirmed if side == "LONG" else card.short_confirmed
            ):
                continue
            if side == "SHORT" and (
                card.instrument_id not in universe.short_tokens
                or not instruments[card.instrument_id].fo_eligible
            ):
                continue
            evidence = MomentumSignalEvidence(
                timestamp=at,
                side=side,
                score=card.long_score if side == "LONG" else card.short_score,
                ranked=card,
                settings=cfg,
                alpha_version_hash=cfg.version_hash(),
                universe_hash=content_hash(universe),
                instrument_master_hash=content_hash(master),
                cross_section_hash=digest,
                state_hash=state.decision_hash(),
                code_commit=state.code_commit,
                config_hash=state.config_hash,
                data_snapshot=state.data_snapshot,
                feature_set_hash=state.evidence.feature_set_hash,
            )
            intents.append(
                SignalIntent(
                    signal_id=uuid5(
                        NAMESPACE_URL, f"godzilla:{ALPHA_ID}:{at.isoformat()}:{card.instrument_id}"
                    ),
                    instrument_id=card.instrument_id,
                    alpha_id=ALPHA_ID,
                    side=side,
                    score=evidence.score,
                    reasons=(
                        *card.reasons,
                        "TOP_TAIL" if side == "LONG" else "BOTTOM_TAIL",
                        "MICRO_WINDOW_EXCLUDED" if cfg.exclude_recent_5m else "FULL_WINDOW",
                    ),
                    evidence=evidence,
                )
            )
        return MomentumResult(
            timestamp=at,
            scores=scores,
            intents=tuple(intents),
            reasons=("RANKING_COMPLETE",),
            cross_section_hash=digest,
            settings=cfg,
            code_commit=state.code_commit,
            data_snapshot=state.data_snapshot,
        )


class _CrossSection(FeatureModel):
    observations: tuple[MomentumObservation, ...]
