"""Research-only paired hypotheses with entry-frozen hedge and explicit exit reasons."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from godzilla.alphas.pairs_data import liquid, select_window
from godzilla.alphas.pairs_models import (
    CombinedRiskReference,
    PairCandidate,
    PairedExecutionRequirements,
    PairEvaluation,
    PairLeg,
    PairLiquidity,
    PairObservation,
    PairPosition,
    PairSignalIntent,
    PairsSettings,
)
from godzilla.alphas.pairs_statistics import (
    PairDiagnostics,
    fit_pair,
    relationship_rejections,
    spread_z,
)
from godzilla.config.modes import TradingMode
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.calendar import ExchangeCalendar
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import utc
from godzilla.market_data.quality import session_bounds
from godzilla.market_state.models import AlphaFamily, StateDecision
from godzilla.universe.builder import UniverseSnapshot


class _PairInputs(FeatureModel):
    candidate: PairCandidate
    window: tuple[PairObservation, ...]
    liquidity: tuple[PairLiquidity, PairLiquidity]
    position: PairPosition | None


class PairsEngine:
    def __init__(
        self,
        settings: PairsSettings,
        diagnostics: PairDiagnostics,
        calendar: ExchangeCalendar,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self.settings, self.diagnostics, self.calendar, self.timezone = (
            settings,
            diagnostics,
            calendar,
            timezone,
        )

    def evaluate(
        self,
        candidate: PairCandidate,
        observations: tuple[PairObservation, ...],
        liquidity: tuple[PairLiquidity, PairLiquidity],
        universe: UniverseSnapshot,
        master: InstrumentMasterSnapshot,
        state: StateDecision,
        at: datetime,
        mode: TradingMode,
        position: PairPosition | None = None,
    ) -> PairEvaluation:
        at, cfg = utc(at), self.settings
        empty = PairEvaluation(
            timestamp=at,
            pair_id=candidate.pair_id,
            reasons=(),
            input_hash=content_hash(candidate),
            settings=cfg,
            code_commit=state.code_commit,
            data_snapshot=state.data_snapshot,
            open_risk_attention=position is not None,
        )
        if mode is TradingMode.LIVE or not cfg.enabled:
            return empty.model_copy(
                update={
                    "reasons": (
                        "PAIR_LIVE_DISABLED" if mode is TradingMode.LIVE else "ALPHA_DISABLED",
                    )
                }
            )
        try:
            bounds = session_bounds(self.calendar, at, self.timezone)
            if bounds is None:
                raise ValueError("SESSION_UNAVAILABLE")
            opening, closing = bounds
            session = self.calendar.session_on(at.astimezone(ZoneInfo(self.timezone)).date())
            if session is None:
                raise ValueError("SESSION_UNAVAILABLE")
            flatten_at = utc(
                datetime.combine(
                    session.session_date,
                    session.times.flatten_start_time,
                    ZoneInfo(session.timezone),
                )
            )
            final_entry = utc(
                datetime.combine(
                    session.session_date, session.times.final_entry_time, ZoneInfo(session.timezone)
                )
            )
            if not opening <= at <= closing:
                raise ValueError("OUTSIDE_SESSION")
            if (
                candidate.known_at > opening
                or candidate.reviewed_at > opening
                or not candidate.effective_from <= at < candidate.effective_to
                or not candidate.corporate_actions_clear
            ):
                raise ValueError("PAIR_METADATA_OR_CORPORATE_ACTION_AMBIGUITY")
            day = at.astimezone(ZoneInfo(self.timezone)).date()
            if (
                universe.as_of != day
                or master.as_of != day
                or utc(universe.generated_at) > at
                or utc(master.generated_at) > at
                or universe.instrument_snapshot_id != master.snapshot_id
            ):
                raise ValueError("UNIVERSE_UNAVAILABLE")
            instruments = {i.token: i for i in master.instruments}
            if len(instruments) != len(master.instruments):
                raise ValueError("AMBIGUOUS_MASTER")
            for ref, sector in (
                (candidate.left, candidate.left_sector),
                (candidate.right, candidate.right_sector),
            ):
                instrument = instruments.get(ref.instrument_id)
                if (
                    instrument is None
                    or ref.instrument_id not in universe.long_tokens
                    or instrument.status is not InstrumentStatus.ACTIVE
                    or not instrument.active_on(day)
                    or instrument.surveillance
                    or instrument.data_ambiguous
                    or instrument.sector != sector
                ):
                    raise ValueError("PAIR_INSTRUMENT_INELIGIBLE")
            window = select_window(observations, candidate, cfg, self.calendar, self.timezone, at)
            if position is not None and (
                position.pair_id != candidate.pair_id
                or not opening <= position.opened_at <= at
                or position.entry_fit.training_end >= position.opened_at
                or position.entry_fit.beta <= 0
            ):
                raise ValueError("INVALID_RESEARCH_POSITION")
        except (ValueError, RuntimeError):
            return empty.model_copy(update={"reasons": ("INPUT_UNAVAILABLE_OR_AMBIGUOUS",)})
        digest = content_hash(
            _PairInputs(candidate=candidate, window=window, liquidity=liquidity, position=position)
        )
        empty = empty.model_copy(update={"input_hash": digest})
        try:
            fit = fit_pair(window[:-1], cfg, self.diagnostics)
            rejected = relationship_rejections(fit, cfg)
        except Exception:
            # Diagnostics are replaceable and may fail numerically; no permissive fallback.
            return empty.model_copy(update={"reasons": ("STATISTICAL_DIAGNOSTICS_UNAVAILABLE",)})
        active_fit = position.entry_fit if position else fit
        spread, zscore = spread_z(window[-1], active_fit)
        permitted = (
            state.timestamp == at
            and state.gross_risk_multiplier > 0
            and AlphaFamily.PAIRS_STAT_ARB in state.allowed_alpha_families
        )
        is_liquid = liquid(*liquidity, candidate, cfg, at)
        action: Literal["ENTER", "EXIT"] = "ENTER"
        direction: Literal["LONG_SPREAD", "SHORT_SPREAD"] = (
            "SHORT_SPREAD" if zscore > 0 else "LONG_SPREAD"
        )
        reasons: tuple[str, ...] = ()
        if position:
            direction, action = position.direction, "EXIT"
            adverse = zscore >= cfg.stop_z if direction == "SHORT_SPREAD" else zscore <= -cfg.stop_z
            if at >= flatten_at:
                reasons = ("SESSION_FLATTEN",)
            elif at >= position.opened_at + timedelta(minutes=cfg.max_hold_minutes):
                reasons = ("MAX_HOLD",)
            elif rejected or abs(fit.beta - active_fit.beta) / active_fit.beta > cfg.max_beta_drift:
                reasons = ("STRUCTURAL_BREAK", *rejected)
            elif not is_liquid:
                reasons = ("LIQUIDITY_DETERIORATION",)
            elif not permitted:
                reasons = ("STATE_PERMISSION_WITHDRAWN",)
            elif adverse:
                reasons = ("SPREAD_ADVERSE_BOUNDARY",)
            elif abs(zscore) <= cfg.exit_z or zscore * position.entry_z <= 0:
                reasons = ("SPREAD_REVERSION",)
        else:
            reasons = (
                *rejected,
                *(("POOR_LIQUIDITY",) if not is_liquid else ()),
                *(("STATE_OR_ENTRY_WINDOW_BLOCKED",) if not permitted or at >= final_entry else ()),
                *(("EXTREME_SPREAD",) if abs(zscore) >= cfg.stop_z else ()),
            )
            if reasons:
                return empty.model_copy(
                    update={
                        "fit": fit,
                        "zscore": zscore,
                        "reasons": reasons,
                        "accepted_relationship": not rejected,
                    }
                )
            if abs(zscore) >= cfg.entry_z:
                reasons = ("POSITIVE_SPREAD_SHORT" if zscore > 0 else "NEGATIVE_SPREAD_LONG",)
        if not reasons:
            return empty.model_copy(
                update={
                    "fit": fit,
                    "zscore": zscore,
                    "accepted_relationship": not rejected,
                    "reasons": ("HOLD" if position else "NO_ENTRY_THRESHOLD",),
                    "open_risk_attention": False,
                }
            )
        left_side: Literal["LONG", "SHORT"] = "LONG" if direction == "LONG_SPREAD" else "SHORT"
        if action == "EXIT":
            left_side = "SHORT" if left_side == "LONG" else "LONG"
        right_side: Literal["LONG", "SHORT"] = "SHORT" if left_side == "LONG" else "LONG"
        if action == "ENTER":
            short_id = (
                candidate.left.instrument_id
                if left_side == "SHORT"
                else candidate.right.instrument_id
            )
            if short_id not in universe.short_tokens or not instruments[short_id].fo_eligible:
                return empty.model_copy(
                    update={"fit": fit, "zscore": zscore, "reasons": ("SHORT_ROUTE_INELIGIBLE",)}
                )
        beta = active_fit.beta
        intent = PairSignalIntent(
            signal_id=uuid5(NAMESPACE_URL, f"godzilla:pairs:{candidate.pair_id}:{at.isoformat()}"),
            timestamp=at,
            pair_id=candidate.pair_id,
            action=action,
            direction=direction,
            legs=(
                PairLeg(
                    instrument_id=candidate.left.instrument_id,
                    side=left_side,
                    gross_fraction=1 / (1 + beta),
                ),
                PairLeg(
                    instrument_id=candidate.right.instrument_id,
                    side=right_side,
                    gross_fraction=beta / (1 + beta),
                ),
            ),
            hedge_ratio=beta,
            risk_reference=CombinedRiskReference(
                spread_mean=active_fit.spread_mean,
                spread_std=active_fit.spread_std,
                current_spread=spread,
                current_z=zscore,
                adverse_boundary_z=cfg.stop_z if direction == "SHORT_SPREAD" else -cfg.stop_z,
                maximum_hold_until=min(
                    flatten_at,
                    (position.opened_at if position else at)
                    + timedelta(minutes=cfg.max_hold_minutes),
                ),
                net_gross_fraction=abs(1 - beta) / (1 + beta),
            ),
            execution=PairedExecutionRequirements(max_leg_lag_seconds=cfg.max_leg_lag_seconds),
            reasons=reasons,
            fit=active_fit,
            settings=cfg,
            candidate=candidate,
            input_hash=digest,
            universe_hash=content_hash(universe),
            state_hash=state.decision_hash(),
            code_commit=state.code_commit,
            config_hash=state.config_hash,
            data_snapshot=state.data_snapshot,
        )
        return PairEvaluation(
            timestamp=at,
            pair_id=candidate.pair_id,
            intent=intent,
            fit=fit,
            zscore=zscore,
            accepted_relationship=not rejected,
            reasons=reasons,
            input_hash=digest,
            settings=cfg,
            code_commit=state.code_commit,
            data_snapshot=state.data_snapshot,
            open_risk_attention=action == "EXIT",
        )
