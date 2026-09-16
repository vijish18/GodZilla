"""Conservative deterministic unit-equity target allocation, never risk approval."""

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from godzilla.config.modes import TradingMode
from godzilla.core.health import HealthStatus
from godzilla.ensemble.models import AlphaHealthSnapshot, AlphaId, AlphaRegistry
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar, Session
from godzilla.market_data.models import utc
from godzilla.market_state.models import AlphaFamily, MarketState, StateDecision
from godzilla.portfolio.models import (
    AllocationRecord,
    AllocatorSettings,
    CandidateRiskEstimate,
    CorrelationSnapshot,
    EnsembleDecision,
    LiquidityCapacity,
    PortfolioDecision,
    PortfolioSnapshot,
    TargetPosition,
)


class AllocationInputs(FeatureModel):
    ensemble: EnsembleDecision
    registry: AlphaRegistry
    health: tuple[AlphaHealthSnapshot, ...]
    portfolio: PortfolioSnapshot
    liquidity: tuple[LiquidityCapacity, ...]
    estimates: tuple[CandidateRiskEstimate, ...]
    correlations: CorrelationSnapshot
    state: StateDecision
    settings: AllocatorSettings
    at: datetime
    mode: TradingMode


class _CalendarAudit(FeatureModel):
    inputs_hash: str
    session: Session


def totals(positions: tuple[TargetPosition, ...]) -> tuple[Decimal, Decimal, Decimal]:
    return (
        sum((abs(p.signed_notional_fraction) for p in positions), Decimal(0)),
        sum((p.signed_notional_fraction for p in positions), Decimal(0)),
        sum((p.open_risk_fraction for p in positions), Decimal(0)),
    )


def within_caps(positions: tuple[TargetPosition, ...], cfg: AllocatorSettings) -> bool:
    gross, net, risk = totals(positions)
    if (
        gross > cfg.max_gross
        or abs(net) > cfg.max_net
        or risk > cfg.max_open_risk
        or len({p.group_id for p in positions}) > cfg.max_groups
    ):
        return False
    for attr, cap in (
        ("alpha_id", cfg.max_alpha),
        ("sector", cfg.max_sector),
        ("entity_id", cfg.max_symbol),
    ):
        for value in {getattr(p, attr) for p in positions}:
            if (
                sum(abs(p.signed_notional_fraction) for p in positions if getattr(p, attr) == value)
                > cap
            ):
                return False
    return all(
        len({p.group_id for p in positions if p.sector == sector}) <= cfg.max_sector_groups
        for sector in {p.sector for p in positions}
    )


def allocate(inputs: AllocationInputs, calendar: ExchangeCalendar) -> PortfolioDecision:
    at, cfg = utc(inputs.at), inputs.settings
    digest = content_hash(inputs)
    decision_id = uuid5(NAMESPACE_URL, "godzilla:portfolio:" + digest)
    portfolio, state = inputs.portfolio, inputs.state
    targets = list(portfolio.positions)
    records: list[AllocationRecord] = []

    def finish(reason: str) -> PortfolioDecision:
        return PortfolioDecision(
            decision_id=decision_id,
            timestamp=at,
            targets=tuple(targets),
            allocations=tuple(records),
            reasons=(reason,),
            input_hash=digest,
            settings=cfg,
        )

    health = state.evidence.health
    try:
        session = calendar.session_on(at.astimezone(ZoneInfo("Asia/Kolkata")).date())
        if session is None:
            return finish("CALENDAR_BLOCKED")
        digest = content_hash(_CalendarAudit(inputs_hash=digest, session=session))
        decision_id = uuid5(NAMESPACE_URL, "godzilla:portfolio:" + digest)
        opening = utc(
            datetime.combine(
                session.session_date, session.times.open_time, ZoneInfo(session.timezone)
            )
        )
        final_entry = utc(
            datetime.combine(
                session.session_date, session.times.final_entry_time, ZoneInfo(session.timezone)
            )
        )
        if not opening <= at < final_entry:
            return finish("CALENDAR_BLOCKED")
    except CalendarCoverageError:
        return finish("CALENDAR_BLOCKED")
    if (
        inputs.mode is TradingMode.LIVE
        or not portfolio.complete
        or portfolio.blocks_new_entries
        or not 0 <= (at - portfolio.timestamp).total_seconds() <= cfg.max_input_age_seconds
        or inputs.ensemble.timestamp != at
        or state.timestamp != at
        or state.state in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK)
        or state.gross_risk_multiplier <= 0
        or health.blocks_new_entries
        or health.system_status is not HealthStatus.HEALTHY
        or health.risk_status is not HealthStatus.HEALTHY
        or not 0
        <= (at - utc(health.observed_at)).total_seconds()
        <= state.settings.health_max_age_seconds
    ):
        return finish("NEW_ALLOCATION_BLOCKED")
    if not within_caps(tuple(targets), cfg):
        return finish("EXISTING_EXPOSURE_REQUIRES_RISK_REVIEW")
    health_by_alpha = {h.alpha_id: h for h in inputs.health}
    capacities = {c.entity_id: c for c in inputs.liquidity}
    estimates = {r.signal_id: r for r in inputs.estimates}
    correlations = {
        frozenset((x.left, x.right)): abs(x.value) for x in inputs.correlations.estimates
    }
    if (
        len(health_by_alpha) != len(inputs.health)
        or len(capacities) != len(inputs.liquidity)
        or len(estimates) != len(inputs.estimates)
        or len(correlations) != len(inputs.correlations.estimates)
        or inputs.correlations.training_end >= at
        or inputs.correlations.training_end > inputs.correlations.timestamp
        or not 0
        <= (at - inputs.correlations.timestamp).total_seconds()
        <= cfg.max_input_age_seconds
    ):
        return finish("ALLOCATION_INPUT_AMBIGUOUS")
    candidates = tuple(
        sorted(inputs.ensemble.candidates, key=lambda x: (-x.strength, str(x.signal_id)))
    )
    if len({c.signal_id for c in candidates}) != len(candidates):
        return finish("DUPLICATE_CANDIDATE")
    score_sums = {
        alpha: max(Decimal(1), sum(c.strength for c in candidates if c.alpha_id is alpha))
        for alpha in AlphaId
    }
    for candidate in candidates:
        multiplier, penalty = Decimal(0), Decimal(1)
        reason = "CANDIDATE_INPUT_UNAVAILABLE"
        amount = Decimal(0)
        try:
            registration = inputs.registry.registration(candidate.alpha_id)
            h = health_by_alpha[candidate.alpha_id]
            estimate = estimates[candidate.signal_id]
            if (
                not registration.enabled
                or candidate.registry_hash != content_hash(inputs.registry)
                or candidate.timestamp != at
                or AlphaFamily(candidate.alpha_id.value) not in state.allowed_alpha_families
                or not 0 <= (at - h.timestamp).total_seconds() <= h.settings.health_max_age_seconds
                or (h.code_commit, h.config_hash, h.data_snapshot)
                != (state.code_commit, state.config_hash, state.data_snapshot)
                or not h.settings.minimum_multiplier
                <= h.multiplier.value
                <= h.settings.maximum_multiplier
                or not 0 <= (at - estimate.timestamp).total_seconds() <= cfg.max_input_age_seconds
            ):
                raise ValueError(reason)
            multiplier = Decimal(str(h.multiplier.value))
            occupied = {p.entity_id for p in targets}
            if occupied & {x.entity_id for x in candidate.legs}:
                raise ValueError("EXISTING_OR_SELECTED_SYMBOL_OVERLAP")
            if len({p.group_id for p in targets}) >= cfg.max_groups:
                raise ValueError("POSITION_GROUP_CAP")
            sectors = {x.sector for x in candidate.legs}
            if any(
                len({p.group_id for p in targets if p.sector == sector}) >= cfg.max_sector_groups
                for sector in sectors
            ):
                raise ValueError("SECTOR_GROUP_CAP")
            corr = Decimal(0)
            for leg in candidate.legs:
                for other in targets:
                    key = frozenset((leg.entity_id, other.entity_id))
                    if key not in correlations:
                        raise ValueError("CORRELATION_UNAVAILABLE")
                    corr = max(corr, correlations[key])
            duplicates = len({p.group_id for p in targets if p.sector in sectors})
            penalty = estimate.volatility_penalty * (
                1
                + cfg.sector_duplication_penalty * duplicates
                + cfg.correlation_penalty
                * max(Decimal(0), corr - cfg.correlation_threshold)
                / (1 - cfg.correlation_threshold)
            )
            desired = (
                registration.base_budget
                * Decimal(str(state.gross_risk_multiplier))
                * multiplier
                * candidate.strength
                / score_sums[candidate.alpha_id]
                / penalty
            )
            gross, net, risk = totals(tuple(targets))
            limits = [
                desired,
                registration.base_budget * Decimal(str(state.gross_risk_multiplier)) * multiplier
                - sum(
                    abs(p.signed_notional_fraction)
                    for p in targets
                    if p.alpha_id is candidate.alpha_id
                ),
                cfg.max_gross - gross,
                (cfg.max_open_risk - risk) / estimate.loss_per_gross,
                cfg.max_candidate_risk / estimate.loss_per_gross,
                cfg.max_alpha
                - sum(
                    abs(p.signed_notional_fraction)
                    for p in targets
                    if p.alpha_id is candidate.alpha_id
                ),
            ]
            net_weight = sum(x.signed_weight for x in candidate.legs)
            if net_weight > 0:
                limits.append((cfg.max_net - net) / net_weight)
            elif net_weight < 0:
                limits.append((cfg.max_net + net) / (-net_weight))
            for sector in sectors:
                weight = sum(abs(x.signed_weight) for x in candidate.legs if x.sector == sector)
                limits.append(
                    (
                        cfg.max_sector
                        - sum(
                            abs(p.signed_notional_fraction) for p in targets if p.sector == sector
                        )
                    )
                    / weight
                )
            for leg in candidate.legs:
                capacity = capacities[leg.entity_id]
                if (
                    not capacity.eligible
                    or not capacity.timestamp <= at <= capacity.valid_until
                    or (at - capacity.timestamp).total_seconds() > cfg.max_input_age_seconds
                ):
                    raise ValueError("LIQUIDITY_CAPACITY_UNAVAILABLE")
                cap = min(
                    cfg.max_symbol,
                    capacity.max_notional / portfolio.equity,
                    capacity.adv_shares
                    * capacity.price
                    * cfg.max_adv_participation
                    / portfolio.equity,
                    capacity.daily_turnover * cfg.max_turnover_participation / portfolio.equity,
                )
                limits.append(cap / abs(leg.signed_weight))
            amount = max(Decimal(0), min(limits))
            # Round down in equity-fraction space, never round a cap upwards.
            from decimal import ROUND_DOWN

            amount = amount.quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            additions = tuple(
                TargetPosition(
                    entity_id=x.entity_id,
                    instrument_id=x.instrument_id,
                    sector=x.sector,
                    alpha_id=candidate.alpha_id,
                    group_id=candidate.signal_id,
                    signed_notional_fraction=amount * x.signed_weight,
                    open_risk_fraction=amount * estimate.loss_per_gross * abs(x.signed_weight),
                    origin="PROPOSED",
                )
                for x in candidate.legs
            )
            if amount > 0 and within_caps((*targets, *additions), cfg):
                targets.extend(additions)
                reason = "PROPOSED_REQUIRES_INDEPENDENT_RISK"
            else:
                amount, reason = Decimal(0), "NO_CAPACITY"
        except (KeyError, ValueError) as error:
            reason = str(error)
            amount = Decimal(0)
        records.append(
            AllocationRecord(
                signal_id=candidate.signal_id,
                allocated_gross=amount,
                health_multiplier=multiplier,
                penalty=penalty,
                reasons=(reason,),
            )
        )
    return finish("PROPOSALS_ONLY_NO_EXECUTION")
