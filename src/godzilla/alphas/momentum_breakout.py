"""Independent Alpha B: relative momentum, range cross and closed-bar participation."""

from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from godzilla.alphas.breakout_models import (
    BREAKOUT_ALPHA_ID,
    BreakoutObservation,
    BreakoutSettings,
    BreakoutSignalEvidence,
    FailedBreakoutObservation,
)
from godzilla.alphas.breakout_scoring import measure_breakout, numeric
from godzilla.alphas.ranking import percentiles
from godzilla.core.events.models import SignalIntent
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import Bar, FeedKind, utc
from godzilla.market_data.quality import session_bounds
from godzilla.market_state.models import AlphaFamily, MarketState, StateDecision, ThresholdEvidence
from godzilla.market_state.rules import EvidenceBuilder
from godzilla.universe.builder import UniverseSnapshot


class BreakoutCandidateReport(FeatureModel):
    instrument_id: str
    reasons: tuple[str, ...]
    checks: tuple[ThresholdEvidence, ...] = ()


class BreakoutResult(FeatureModel):
    timestamp: datetime
    intents: tuple[SignalIntent, ...]
    reports: tuple[BreakoutCandidateReport, ...]
    reasons: tuple[str, ...]
    cross_section_hash: str


class _BreakoutCrossSection(FeatureModel):
    observations: tuple[BreakoutObservation, ...]


class MomentumBreakout:
    def __init__(
        self, settings: BreakoutSettings, calendar: ExchangeCalendar, timezone: str = "Asia/Kolkata"
    ) -> None:
        self.settings, self.calendar, self.timezone = settings, calendar, timezone

    def evaluate(
        self,
        observations: tuple[BreakoutObservation, ...],
        universe: UniverseSnapshot,
        master: InstrumentMasterSnapshot,
        state: StateDecision,
        at: datetime,
    ) -> BreakoutResult:
        at, cfg = utc(at), self.settings
        selected = tuple(
            sorted(
                (o for o in observations if o.current.snapshot.timestamp == at),
                key=lambda o: o.current.snapshot.entity,
            )
        )
        digest = content_hash(_BreakoutCrossSection(observations=selected))
        empty = BreakoutResult(
            timestamp=at, intents=(), reports=(), reasons=(), cross_section_hash=digest
        )
        if not cfg.enabled:
            return empty.model_copy(update={"reasons": ("ALPHA_DISABLED",)})
        side: Literal["LONG", "SHORT"] | None = (
            "LONG"
            if state.state is MarketState.TREND_UP and "LONG" in state.directional_sides
            else "SHORT"
            if state.state is MarketState.TREND_DOWN and "SHORT" in state.directional_sides
            else None
        )
        if (
            side is None
            or state.timestamp != at
            or state.gross_risk_multiplier <= 0
            or AlphaFamily.MOMENTUM_BREAKOUT not in state.allowed_alpha_families
        ):
            return empty.model_copy(update={"reasons": ("STATE_PERMISSION_DENIED",)})
        try:
            bounds = session_bounds(self.calendar, at, self.timezone)
        except CalendarCoverageError:
            bounds = None
        if bounds is None or not bounds[0] <= at < bounds[1]:
            return empty.model_copy(update={"reasons": ("SESSION_UNAVAILABLE",)})
        opening, closing = bounds
        day = at.astimezone(ZoneInfo(self.timezone)).date()
        instruments = {i.token: i for i in master.instruments}
        expected = set(universe.long_tokens)
        ids = [o.current.snapshot.entity for o in selected]
        if (
            universe.as_of != day
            or master.as_of != day
            or utc(universe.generated_at) > at
            or utc(master.generated_at) > at
            or universe.instrument_snapshot_id != master.snapshot_id
            or len(expected) < cfg.minimum_universe_size
            or set(ids) != expected
            or len(ids) != len(expected)
            or len(expected) != len(universe.long_tokens)
            or not set(universe.short_tokens) <= expected
            or len(instruments) != len(master.instruments)
            or not expected <= instruments.keys()
            or len({instruments[t].symbol for t in expected}) != len(expected)
        ):
            return empty.model_copy(update={"reasons": ("UNIVERSE_UNAVAILABLE_OR_AMBIGUOUS",)})
        try:
            for item in selected:
                current, reference = item.current, item.reference
                if (
                    current.context.entity != reference.context.entity
                    or current.context.nifty != reference.context.nifty
                    or current.context.sector != reference.context.sector
                ):
                    raise ValueError("REFERENCE_SOURCE_MISMATCH")
                instrument = instruments[current.snapshot.entity]
                if (
                    instrument.status is not InstrumentStatus.ACTIVE
                    or not instrument.active_on(day)
                    or instrument.surveillance
                    or instrument.data_ambiguous
                ):
                    raise ValueError("INSTRUMENT_INELIGIBLE")
                for observation in (current, reference):
                    snapshot, context = observation.snapshot, observation.context
                    if (
                        snapshot.entity != current.snapshot.entity
                        or context.entity.instrument_id != snapshot.entity
                        or context.nifty is None
                        or context.sector is None
                        or context.universe_version != str(universe.snapshot_id)
                        or content_hash(context) != snapshot.context_hash
                        or context.known_at > snapshot.timestamp
                        or not context.valid_from <= snapshot.timestamp < context.valid_to
                        or snapshot.feature_set_hash != item.feature_definition.version_hash()
                        or (
                            snapshot.code_commit,
                            snapshot.config_hash,
                            snapshot.data_snapshot,
                            snapshot.feature_set_hash,
                        )
                        != (
                            state.code_commit,
                            state.config_hash,
                            state.data_snapshot,
                            state.evidence.feature_set_hash,
                        )
                    ):
                        raise ValueError("CONTEXT_OR_PROVENANCE_MISMATCH")
                if (
                    not opening <= reference.snapshot.timestamp <= item.bar.start
                    or item.previous_bar.end != item.bar.start
                    or item.previous_bar.start < opening
                    or (item.bar.start - opening).total_seconds() % 300 != 0
                    or not 0 <= (at - item.bar.end).total_seconds() <= cfg.max_bar_lag_seconds
                ):
                    raise ValueError("BAR_OR_REFERENCE_NOT_CAUSAL")
                for bar in (item.bar, item.previous_bar):
                    bar.require_strategy_ready(at)
                    if (
                        bar.instrument_id != current.snapshot.entity
                        or bar.source != current.context.entity.source
                        or bar.feed_kind is not FeedKind.EQUITY
                    ):
                        raise ValueError("BAR_IDENTITY_MISMATCH")
            sector = tuple(numeric(o, "stock.relative_sector_30m") for o in selected)
            market = tuple(numeric(o, "stock.relative_market_30m") for o in selected)
        except ValueError as error:
            return empty.model_copy(update={"reasons": ("INPUT_BLOCKED", str(error))})
        strengths = tuple(
            (a + b) / 2 for a, b in zip(percentiles(sector), percentiles(market), strict=True)
        )
        reports, intents = [], []
        for item, strength in zip(selected, strengths, strict=True):
            instrument = instruments[item.current.snapshot.entity]
            if side == "SHORT" and (
                instrument.token not in universe.short_tokens or not instrument.fo_eligible
            ):
                reports.append(
                    BreakoutCandidateReport(
                        instrument_id=instrument.token, reasons=("SHORT_INELIGIBLE",)
                    )
                )
                continue
            evidence = EvidenceBuilder(item.current.snapshot)
            try:
                components, invalidation, reasons = measure_breakout(
                    item,
                    side,
                    strength if side == "LONG" else 1 - strength,
                    cfg,
                    opening,
                    closing,
                    evidence,
                )
            except ValueError as error:
                components, invalidation, reasons = (), None, (str(error),)
            score = min(1, sum(c.contribution for c in components))
            if invalidation is not None and not evidence.record(
                "candidate.score", score, "ge", cfg.minimum_score
            ):
                invalidation, reasons = None, ("SCORE_BELOW_THRESHOLD",)
            reports.append(
                BreakoutCandidateReport(
                    instrument_id=instrument.token, reasons=reasons, checks=tuple(evidence.checks)
                )
            )
            if invalidation is None:
                continue
            detail = BreakoutSignalEvidence(
                timestamp=at,
                instrument_id=instrument.token,
                symbol=instrument.symbol,
                source=item.bar.source,
                side=side,
                score=score,
                components=components,
                checks=tuple(evidence.checks),
                invalidation=invalidation,
                settings=cfg,
                alpha_version_hash=cfg.version_hash(),
                observation_hash=content_hash(item),
                cross_section_hash=digest,
                universe_hash=content_hash(universe),
                instrument_master_hash=content_hash(master),
                state_hash=state.decision_hash(),
                code_commit=state.code_commit,
                config_hash=state.config_hash,
                data_snapshot=state.data_snapshot,
                feature_set_hash=state.evidence.feature_set_hash,
            )
            intents.append(
                SignalIntent(
                    signal_id=uuid5(
                        NAMESPACE_URL,
                        f"godzilla:{BREAKOUT_ALPHA_ID}:{item.bar.end.isoformat()}:{instrument.token}",
                    ),
                    instrument_id=instrument.token,
                    alpha_id=BREAKOUT_ALPHA_ID,
                    side=side,
                    score=score,
                    evidence=detail,
                    reasons=reasons,
                )
            )
        return BreakoutResult(
            timestamp=at,
            intents=tuple(intents),
            reports=tuple(reports),
            reasons=("EVALUATION_COMPLETE",),
            cross_section_hash=digest,
        )


def observe_failed_breakout(
    evidence: BreakoutSignalEvidence, bar: Bar, at: datetime
) -> FailedBreakoutObservation:
    at = utc(at)
    if bar.instrument_id != evidence.instrument_id or bar.source != evidence.source:
        raise ValueError("invalidation bar identity mismatch")
    try:
        bar.require_strategy_ready(at)
    except ValueError:
        return FailedBreakoutObservation(
            status="UNAVAILABLE", reason="BAR_NOT_READY", observed_at=at
        )
    rule = evidence.invalidation
    if bar.end <= rule.trigger_bar_end:
        return FailedBreakoutObservation(
            status="UNAVAILABLE", reason="NOT_SUBSEQUENT_BAR", observed_at=at
        )
    if bar.end > rule.expires_at:
        return FailedBreakoutObservation(
            status="EXPIRED", reason="OBSERVATION_WINDOW_ENDED", observed_at=at
        )
    failed = (
        float(bar.close) <= rule.invalidation_level
        if evidence.side == "LONG"
        else float(bar.close) >= rule.invalidation_level
    )
    return FailedBreakoutObservation(
        status="FAILED" if failed else "ACTIVE",
        reason="CLOSED_BACK_THROUGH_LEVEL" if failed else "LEVEL_HOLDS",
        observed_at=at,
    )
