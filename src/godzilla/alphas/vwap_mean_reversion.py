"""Independent research alpha: extreme stretch AND deceleration AND closed-bar reversal."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import field_validator

from godzilla.alphas.vwap_models import (
    VWAP_ALPHA_ID,
    VwapExitMetadata,
    VwapLiquidity,
    VwapSettings,
    VwapSignalEvidence,
)
from godzilla.config.modes import TradingMode
from godzilla.core.events.models import SignalIntent
from godzilla.core.health import HealthStatus
from godzilla.features.models import FeatureModel, content_hash
from godzilla.features.vwap_reversion import reversion_features
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import Bar, utc
from godzilla.market_data.quality import session_bounds
from godzilla.market_state.models import AlphaFamily, MarketState, StateDecision
from godzilla.universe.builder import UniverseSnapshot


class VwapResult(FeatureModel):
    timestamp: datetime
    intent: SignalIntent | None = None
    reasons: tuple[str, ...]

    _aware = field_validator("timestamp")(utc)


class VwapMeanReversion:
    def __init__(
        self, settings: VwapSettings, calendar: ExchangeCalendar, timezone: str = "Asia/Kolkata"
    ) -> None:
        self.settings, self.calendar, self.timezone = settings, calendar, timezone

    def evaluate(
        self,
        bars: tuple[Bar, ...],
        liquidity: VwapLiquidity,
        universe: UniverseSnapshot,
        master: InstrumentMasterSnapshot,
        state: StateDecision,
        at: datetime,
        mode: TradingMode,
    ) -> VwapResult:
        at, cfg = utc(at), self.settings

        def reject(reason: str) -> VwapResult:
            return VwapResult(timestamp=at, reasons=(reason,))

        if mode is TradingMode.LIVE:
            return reject("LIVE_PROMOTION_REQUIRED")
        if not cfg.enabled:
            return reject("ALPHA_DISABLED")
        health = state.evidence.health
        if (
            state.timestamp != at
            or state.gross_risk_multiplier <= 0
            or state.state in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK)
            or health.blocks_new_entries
            or health.system_status is not HealthStatus.HEALTHY
            or health.risk_status is not HealthStatus.HEALTHY
            or not 0
            <= (at - utc(health.observed_at)).total_seconds()
            <= state.settings.health_max_age_seconds
        ):
            return reject("HEALTH_OR_RISK_VETO")
        override = cfg.research_regime_override and mode in (
            TradingMode.RESEARCH,
            TradingMode.BACKTEST,
        )
        if not override and (
            state.state is not MarketState.CHOP
            or AlphaFamily.VWAP_MEAN_REVERSION not in state.allowed_alpha_families
        ):
            return reject("REGIME_PERMISSION_DENIED")
        try:
            bounds = session_bounds(self.calendar, at, self.timezone)
            if bounds is None:
                return reject("SESSION_UNAVAILABLE")
            opening, closing = bounds
            day = at.astimezone(ZoneInfo(self.timezone)).date()
            session = self.calendar.session_on(day)
            if session is None:
                return reject("SESSION_UNAVAILABLE")
            final_entry = utc(
                datetime.combine(day, session.times.final_entry_time, ZoneInfo(session.timezone))
            )
            flatten = utc(
                datetime.combine(day, session.times.flatten_start_time, ZoneInfo(session.timezone))
            )
            if (
                not opening + timedelta(minutes=cfg.start_minutes)
                <= at
                < min(closing, final_entry, flatten, opening + timedelta(minutes=cfg.end_minutes))
            ):
                return reject("ENTRY_WINDOW_BLOCKED")
            features = reversion_features(bars, at, opening, cfg.features)
        except (ValueError, CalendarCoverageError) as error:
            return reject(str(error))
        if not 0 <= (at - features.timestamp).total_seconds() <= cfg.max_bar_lag_seconds:
            return reject("STALE_BAR")
        instruments = {i.token: i for i in master.instruments}
        instrument = instruments.get(features.instrument_id)
        if (
            universe.as_of != day
            or master.as_of != day
            or universe.generated_at > at
            or master.generated_at > at
            or universe.instrument_snapshot_id != master.snapshot_id
            or len(instruments) != len(master.instruments)
            or len(set(universe.long_tokens)) != len(universe.long_tokens)
            or not set(universe.short_tokens) <= set(universe.long_tokens)
            or instrument is None
            or instrument.token not in universe.long_tokens
            or instrument.status is not InstrumentStatus.ACTIVE
            or not instrument.active_on(day)
            or instrument.surveillance
            or instrument.data_ambiguous
        ):
            return reject("UNIVERSE_OR_INSTRUMENT_INELIGIBLE")
        quote = liquidity.quote
        if (
            quote.instrument_id != features.instrument_id
            or quote.source != features.source
            or quote.quality_flags
            or not 0 < quote.bid <= quote.ask
            or quote.last <= 0
            or not quote.timestamp <= quote.received_at <= at
            or not 0 <= (at - quote.timestamp).total_seconds() <= cfg.max_quote_age_seconds
            or not 0 <= (at - liquidity.known_at).total_seconds() <= cfg.liquidity_max_age_seconds
            or liquidity.adv < cfg.min_adv
            or liquidity.daily_turnover < cfg.min_turnover
            or float((quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2) * 10000)
            > cfg.max_spread_bps
        ):
            return reject("LIQUIDITY_REJECTED")
        if not cfg.min_stretch <= abs(features.stretch) < cfg.max_stretch:
            return reject("STRETCH_OUTSIDE_LIMITS")
        side: Literal["LONG", "SHORT"] = "LONG" if features.stretch < 0 else "SHORT"
        sign = 1 if side == "LONG" else -1
        if side == "SHORT" and (
            instrument.token not in universe.short_tokens or not instrument.fo_eligible
        ):
            return reject("SHORT_INELIGIBLE")
        impulse, slower = -sign * features.impulse, -sign * features.decelerated_impulse
        if not (impulse >= cfg.min_impulse and 0 <= slower <= cfg.max_deceleration_ratio * impulse):
            return reject("IMPULSE_NOT_DECELERATING")
        location = features.close_location if side == "LONG" else 1 - features.close_location
        if (
            sign * features.reversal < cfg.min_reversal
            or sign * features.body < cfg.min_body
            or location < cfg.min_close_location
        ):
            return reject("REVERSAL_NOT_CONFIRMED")
        adverse = features.price - sign * cfg.adverse_scale * features.volatility_scale
        if adverse <= 0:
            return reject("INVALID_ADVERSE_REFERENCE")
        # Descriptive stretch intensity; never a success probability or sizing instruction.
        score = min(1, abs(features.stretch) / cfg.max_stretch)
        reasons: tuple[str, ...] = (
            "EXTREME_VWAP_STRETCH",
            "IMPULSE_DECELERATING",
            "CLOSED_BAR_REVERSAL",
        )
        if override:
            reasons += ("RESEARCH_REGIME_OVERRIDE",)
        evidence = VwapSignalEvidence(
            timestamp=at,
            instrument_id=instrument.token,
            symbol=instrument.symbol,
            source=features.source,
            side=side,
            score=score,
            features=features,
            exits=VwapExitMetadata(
                convergence_price=features.price
                + cfg.convergence_fraction * (features.session_vwap - features.price),
                adverse_price=adverse,
                time_stop=min(at + timedelta(minutes=cfg.max_hold_minutes), flatten),
                entry_state=state.state,
            ),
            settings=cfg,
            alpha_version_hash=cfg.version_hash(),
            feature_set_hash=cfg.features.version_hash(),
            universe_hash=content_hash(universe),
            instrument_master_hash=content_hash(master),
            state_hash=state.decision_hash(),
            liquidity_hash=content_hash(liquidity),
            code_commit=state.code_commit,
            config_hash=state.config_hash,
            data_snapshot=state.data_snapshot,
        )
        return VwapResult(
            timestamp=at,
            reasons=reasons,
            intent=SignalIntent(
                signal_id=uuid5(
                    NAMESPACE_URL,
                    f"godzilla:{VWAP_ALPHA_ID}:{instrument.token}:{features.timestamp.isoformat()}",
                ),
                instrument_id=instrument.token,
                alpha_id=VWAP_ALPHA_ID,
                side=side,
                score=score,
                reasons=reasons,
                evidence=evidence,
            ),
        )


def observe_vwap_exit(
    evidence: VwapSignalEvidence, bar: Bar, state: StateDecision, at: datetime
) -> tuple[str, ...]:
    """Observe exit conditions only; never claim that a position has been closed."""
    at = utc(at)
    if state.timestamp != at or bar.end <= evidence.features.timestamp:
        return ("EXIT_INPUT_UNAVAILABLE",)
    if bar.instrument_id != evidence.instrument_id or bar.source != evidence.source:
        return ("EXIT_INPUT_UNAVAILABLE",)
    try:
        bar.require_strategy_ready(at)
    except ValueError:
        return ("EXIT_INPUT_UNAVAILABLE",)
    if (at - bar.end).total_seconds() > evidence.settings.max_bar_lag_seconds:
        return ("EXIT_INPUT_UNAVAILABLE",)
    sign = 1 if evidence.side == "LONG" else -1
    reasons = []
    if sign * (float(bar.close) - evidence.exits.convergence_price) >= 0:
        reasons.append("VWAP_CONVERGENCE")
    if sign * (float(bar.close) - evidence.exits.adverse_price) <= 0:
        reasons.append("ADVERSE_CONTINUATION")
    if at >= evidence.exits.time_stop:
        reasons.append("TIME_STOP")
    if state.state != evidence.exits.entry_state or state.evidence.health.blocks_new_entries:
        reasons.append("STATE_CHANGE")
    return tuple(reasons) or ("HOLD",)
