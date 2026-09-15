"""Research sector leaders/laggards with training beta and indivisible combined exposure."""

from datetime import datetime
from statistics import fmean
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from godzilla.alphas.pairs_models import PairedExecutionRequirements
from godzilla.alphas.ranking import percentiles
from godzilla.alphas.relative_value_beta import estimate_beta, neutral_weights
from godzilla.alphas.relative_value_models import (
    RV_ALPHA_ID,
    BetaEstimate,
    RelativeValueIntent,
    RelativeValueInventory,
    RelativeValueLeg,
    RelativeValueObservation,
    RelativeValueRiskReference,
    RelativeValueSettings,
    security_id,
)
from godzilla.config.modes import TradingMode
from godzilla.core.health import HealthStatus
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import utc
from godzilla.market_state.models import AlphaFamily, MarketState, StateDecision
from godzilla.universe.builder import UniverseSnapshot


class RelativeValueReport(FeatureModel):
    entity: str
    reasons: tuple[str, ...]


class RelativeValueResult(FeatureModel):
    intents: tuple[RelativeValueIntent, ...] = ()
    reports: tuple[RelativeValueReport, ...] = ()
    reasons: tuple[str, ...]


class RankedSecurity(FeatureModel):
    instrument_id: str
    sector: str
    strength: float
    percentile: float = 0
    beta: BetaEstimate
    feature_hash: str


class _CrossSection(FeatureModel):
    candidates: tuple[RankedSecurity, ...]


def numeric(item: RelativeValueObservation, name: str) -> float:
    try:
        value = item.current.snapshot.get(name).value
    except StopIteration as error:
        raise ValueError("FEATURE_MISSING:" + name) from error
    if value is None:
        raise ValueError("FEATURE_MISSING:" + name)
    return value


class SectorRelativeValue:
    def __init__(
        self,
        settings: RelativeValueSettings,
        calendar: ExchangeCalendar,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self.settings, self.calendar, self.timezone = settings, calendar, timezone

    def evaluate(
        self,
        observations: tuple[RelativeValueObservation, ...],
        universe: UniverseSnapshot,
        master: InstrumentMasterSnapshot,
        state: StateDecision,
        inventory: RelativeValueInventory,
        at: datetime,
        mode: TradingMode,
    ) -> RelativeValueResult:
        at, cfg = utc(at), self.settings
        if mode is TradingMode.LIVE or not cfg.enabled:
            return RelativeValueResult(
                reasons=(
                    "LIVE_VALIDATION_REQUIRED" if mode is TradingMode.LIVE else "ALPHA_DISABLED",
                )
            )
        health = state.evidence.health
        if (
            state.timestamp != at
            or state.state in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK)
            or state.gross_risk_multiplier <= 0
            or AlphaFamily.SECTOR_RELATIVE_VALUE not in state.allowed_alpha_families
            or health.blocks_new_entries
            or health.system_status is not HealthStatus.HEALTHY
            or health.risk_status is not HealthStatus.HEALTHY
            or not 0
            <= (at - utc(health.observed_at)).total_seconds()
            <= state.settings.health_max_age_seconds
        ):
            return RelativeValueResult(reasons=("STATE_OR_HEALTH_BLOCKED",))
        if (
            not inventory.complete
            or not 0
            <= (at - inventory.observed_at).total_seconds()
            <= cfg.max_inventory_age_seconds
        ):
            return RelativeValueResult(reasons=("INVENTORY_UNAVAILABLE",))
        try:
            bounds = self.calendar.session_on(at.astimezone(ZoneInfo(self.timezone)).date())
            if bounds is None:
                raise ValueError("SESSION_UNAVAILABLE")
            opening = utc(
                datetime.combine(
                    bounds.session_date, bounds.times.open_time, ZoneInfo(bounds.timezone)
                )
            )
            final = utc(
                datetime.combine(
                    bounds.session_date, bounds.times.final_entry_time, ZoneInfo(bounds.timezone)
                )
            )
            flatten = utc(
                datetime.combine(
                    bounds.session_date, bounds.times.flatten_start_time, ZoneInfo(bounds.timezone)
                )
            )
            if not opening < at < min(final, flatten) or (at - opening).total_seconds() % 300 != 0:
                raise ValueError("ENTRY_WINDOW_BLOCKED")
        except (ValueError, CalendarCoverageError) as error:
            return RelativeValueResult(reasons=(str(error),))
        day = at.astimezone(ZoneInfo(self.timezone)).date()
        selected = tuple(
            sorted(
                (o for o in observations if o.current.snapshot.timestamp == at),
                key=lambda o: o.current.snapshot.entity,
            )
        )
        instruments = {i.token: i for i in master.instruments}
        expected = set(universe.long_tokens)
        if (
            universe.as_of != day
            or master.as_of != day
            or utc(universe.generated_at) > at
            or utc(master.generated_at) > at
            or universe.instrument_snapshot_id != master.snapshot_id
            or len(instruments) != len(master.instruments)
            or len({security_id(i) for i in master.instruments}) != len(master.instruments)
            or len(expected) != len(universe.long_tokens)
            or not set(universe.short_tokens) <= expected
            or {o.current.snapshot.entity for o in selected} != expected
            or len(selected) != len(expected)
            or not expected <= instruments.keys()
        ):
            return RelativeValueResult(reasons=("CROSS_SECTION_OR_MASTER_AMBIGUOUS",))
        reserved = {r.entity_id for r in inventory.reservations}
        reports, candidates = [], []
        for item in selected:
            snapshot, context = item.current.snapshot, item.current.context
            instrument = instruments[snapshot.entity]
            try:
                if (
                    instrument.status is not InstrumentStatus.ACTIVE
                    or not instrument.active_on(day)
                    or instrument.surveillance
                    or instrument.data_ambiguous
                ):
                    raise ValueError("INSTRUMENT_INELIGIBLE")
                if security_id(instrument) in reserved:
                    raise ValueError("STAT_ARB_OR_RV_INVENTORY_OVERLAP")
                if (
                    context.entity.instrument_id != instrument.token
                    or context.sector is None
                    or context.nifty is None
                    or context.sector.instrument_id != instrument.sector
                    or context.known_at > at
                    or not context.valid_from <= at < context.valid_to
                    or context.universe_version != str(universe.snapshot_id)
                    or snapshot.context_hash != content_hash(context)
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
                    raise ValueError("SECTOR_CONTEXT_OR_PROVENANCE_MISMATCH")
                if (
                    not 0 <= numeric(item, "liquidity.spread_bps") <= cfg.max_spread_bps
                    or not 0 <= numeric(item, "liquidity.quote_age") <= cfg.max_quote_age_seconds
                    or numeric(item, "liquidity.adv") < cfg.min_adv
                    or numeric(item, "liquidity.average_daily_turnover") < cfg.min_turnover
                ):
                    raise ValueError("LIQUIDITY_REJECTED")
                strength = cfg.sector_residual_weight * numeric(
                    item, "stock.relative_sector_30m"
                ) + (1 - cfg.sector_residual_weight) * numeric(item, "stock.relative_market_30m")
                beta = estimate_beta(item.history, context.entity, context.nifty, at, opening, cfg)
                candidates.append(
                    RankedSecurity(
                        instrument_id=instrument.token,
                        sector=instrument.sector,
                        strength=strength,
                        beta=beta,
                        feature_hash=snapshot.snapshot_hash(),
                    )
                )
            except ValueError as error:
                reports.append(RelativeValueReport(entity=instrument.token, reasons=(str(error),)))
        intents = []
        if len({c.beta.market_hash for c in candidates}) > 1:
            return RelativeValueResult(
                reports=tuple(reports), reasons=("INCONSISTENT_MARKET_TRAINING",)
            )
        digest = content_hash(_CrossSection(candidates=tuple(candidates)))
        for sector in sorted({c.sector for c in candidates}):
            group = tuple(c for c in candidates if c.sector == sector)
            if len(group) < cfg.minimum_sector_size:
                reports.append(RelativeValueReport(entity=sector, reasons=("SECTOR_WARMUP",)))
                continue
            ranks = percentiles(tuple(c.strength for c in group))
            ranked = tuple(
                c.model_copy(update={"percentile": p}) for c, p in zip(group, ranks, strict=True)
            )
            n = 1 if cfg.construction == "PAIR" else cfg.members_per_side
            leaders = tuple(
                sorted(
                    (c for c in ranked if c.percentile >= 1 - cfg.tail_fraction),
                    key=lambda c: (-c.strength, str(security_id(instruments[c.instrument_id]))),
                )
            )[:n]
            laggards = tuple(
                sorted(
                    (
                        c
                        for c in ranked
                        if c.percentile <= cfg.tail_fraction
                        and c.instrument_id in universe.short_tokens
                        and instruments[c.instrument_id].fo_eligible
                    ),
                    key=lambda c: (c.strength, str(security_id(instruments[c.instrument_id]))),
                )
            )[:n]
            if (
                len(leaders) != n
                or len(laggards) != n
                or min(c.strength for c in leaders) - max(c.strength for c in laggards)
                < cfg.min_residual_gap
            ):
                reports.append(
                    RelativeValueReport(
                        entity=sector, reasons=("TAILS_OR_RESIDUAL_GAP_INSUFFICIENT",)
                    )
                )
                continue
            long_beta, short_beta = (
                fmean(c.beta.beta for c in leaders),
                fmean(c.beta.beta for c in laggards),
            )
            try:
                lw, sw = neutral_weights(long_beta, short_beta, cfg)
            except ValueError as error:
                reports.append(RelativeValueReport(entity=sector, reasons=(str(error),)))
                continue
            legs = tuple(
                RelativeValueLeg(
                    entity_id=security_id(instruments[c.instrument_id]),
                    instrument_id=c.instrument_id,
                    symbol=instruments[c.instrument_id].symbol,
                    sector=sector,
                    side=side,
                    gross_fraction=weight / n,
                    residual_strength=c.strength,
                    percentile=c.percentile,
                    beta=c.beta,
                    feature_hash=c.feature_hash,
                )
                for side, weight, members in (("LONG", lw, leaders), ("SHORT", sw, laggards))
                for c in members
            )
            entity = uuid5(
                NAMESPACE_URL,
                "godzilla:rv-basket:" + ":".join(sorted(str(x.entity_id) for x in legs)),
            )
            intents.append(
                RelativeValueIntent(
                    signal_id=uuid5(
                        NAMESPACE_URL, f"godzilla:{RV_ALPHA_ID}:{entity}:{at.isoformat()}"
                    ),
                    entity_id=entity,
                    timestamp=at,
                    sector=sector,
                    legs=legs,
                    execution=PairedExecutionRequirements(
                        max_leg_lag_seconds=cfg.max_leg_lag_seconds
                    ),
                    hedge_ratio=sw / lw,
                    risk_reference=RelativeValueRiskReference(
                        signed_net_gross=lw - sw,
                        signed_beta_gross=lw * long_beta - sw * short_beta,
                        long_beta=long_beta,
                        short_beta=short_beta,
                        flatten_by=flatten,
                    ),
                    reasons=(
                        "SECTOR_LEADER_LAGGARD",
                        "TRAINING_ONLY_BETA",
                        "NET_AND_BETA_LIMITS_PASSED",
                    ),
                    settings=cfg,
                    universe_hash=content_hash(universe),
                    master_hash=content_hash(master),
                    inventory_hash=content_hash(inventory),
                    state_hash=state.decision_hash(),
                    cross_section_hash=digest,
                    code_commit=state.code_commit,
                    config_hash=state.config_hash,
                    data_snapshot=state.data_snapshot,
                    feature_set_hash=state.evidence.feature_set_hash,
                )
            )
        return RelativeValueResult(
            intents=tuple(intents), reports=tuple(reports), reasons=("EVALUATION_COMPLETE",)
        )
