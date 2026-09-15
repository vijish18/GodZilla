"""Descriptive comparison only: no return labels, optimization, orders or profitability claim."""

from collections import Counter
from itertools import pairwise

from pydantic import Field

from godzilla.features.models import FeatureModel, FeatureSnapshot, content_hash
from godzilla.market_data.calendar import ExchangeCalendar
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_state.models import MarketState, RouterSettings, RoutingHealth
from godzilla.market_state.router import MarketStateRouter


class ResearchObservation(FeatureModel):
    snapshot: FeatureSnapshot
    health: RoutingHealth


class ExperimentIdentity(FeatureModel):
    hypothesis_id: str = Field(min_length=1)
    code_commit: str = Field(min_length=1)
    data_snapshot: str = Field(min_length=1)
    search_family: str = Field(min_length=1)
    trials: int = Field(ge=1)
    seed: int | None = None
    window_role: str = "synthetic-development"
    previously_inspected: bool = True


class BaselineComparison(FeatureModel):
    experiment: ExperimentIdentity
    parameters: RouterSettings
    input_hashes: tuple[str, ...]
    router_states: tuple[MarketState, ...]
    baseline_states: tuple[MarketState, ...]
    agreement_fraction: float
    router_transitions: int
    baseline_transitions: int
    router_counts: dict[str, int]
    baseline_counts: dict[str, int]


def simple_vwap_slope(snapshot: FeatureSnapshot, cfg: RouterSettings) -> MarketState:
    values = {item.name: item.value for item in snapshot.values}
    vwap, slope = values.get("market.vwap_distance"), values.get("market.slope_15m")
    if vwap is None or slope is None:
        return MarketState.RISK_OFF
    if vwap >= cfg.vwap_distance and slope >= cfg.slope_15m:
        return MarketState.TREND_UP
    if vwap <= -cfg.vwap_distance and slope <= -cfg.slope_15m:
        return MarketState.TREND_DOWN
    return MarketState.CHOP


def compare_baseline(
    observations: tuple[ResearchObservation, ...],
    cfg: RouterSettings,
    calendar: ExchangeCalendar,
    timezone: str,
    experiment: ExperimentIdentity,
) -> BaselineComparison:
    if not observations:
        raise ValueError("comparison requires observations")
    router = MarketStateRouter(cfg, calendar, timezone, MemoryMetrics())
    routed, baseline = [], []
    previous_time = None
    for item in observations:
        if previous_time is not None and item.snapshot.timestamp <= previous_time:
            raise ValueError("research observations must be strictly chronological")
        if (
            item.snapshot.code_commit != experiment.code_commit
            or item.snapshot.data_snapshot != experiment.data_snapshot
        ):
            raise ValueError("experiment provenance differs from feature inputs")
        previous_time = item.snapshot.timestamp
        decision = router.route(item.snapshot, item.health, item.snapshot.timestamp)
        routed.append(decision.state)
        # Share fail-closed/opening guards; omit the volatility state and smoothing.
        baseline.append(
            decision.candidate
            if "SAFETY_VETO" in decision.evidence.reasons
            or decision.candidate is MarketState.OPEN_SHOCK
            else simple_vwap_slope(item.snapshot, cfg)
        )
    return BaselineComparison(
        experiment=experiment,
        parameters=cfg,
        input_hashes=tuple(content_hash(item) for item in observations),
        router_states=tuple(routed),
        baseline_states=tuple(baseline),
        agreement_fraction=sum(a == b for a, b in zip(routed, baseline, strict=True)) / len(routed),
        router_transitions=sum(a != b for a, b in pairwise(routed)),
        baseline_transitions=sum(a != b for a, b in pairwise(baseline)),
        router_counts=dict(Counter(routed)),
        baseline_counts=dict(Counter(baseline)),
    )
