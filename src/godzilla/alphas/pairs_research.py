"""Descriptive survival/stability and two-leg cost stress, separate from generation."""

from statistics import fmean

from pydantic import Field

from godzilla.alphas.pairs_models import PairEvaluation, PairsSettings
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_state.research import ExperimentIdentity


class PairRoundTrip(FeatureModel):
    """Externally observed gross-weight returns over a common holding interval."""

    left_gross_fraction: float = Field(gt=0, lt=1)
    left_signed_return: float
    right_signed_return: float


class CostScenario(FeatureModel):
    name: str
    left_one_way_bps: float = Field(ge=0)
    right_one_way_bps: float = Field(ge=0)


class CostResult(FeatureModel):
    scenario: CostScenario
    mean_gross_return: float | None
    mean_round_trip_cost: float | None
    mean_net_return: float | None


class PairResearchReport(FeatureModel):
    experiment: ExperimentIdentity
    settings: PairsSettings
    evaluation_hashes: tuple[str, ...]
    evaluations: int
    accepted_fraction: float
    initial_survivor_fraction: float
    mean_beta_drift: float | None
    mean_half_life_bars: float | None
    costs: tuple[CostResult, ...]
    round_trips: tuple[PairRoundTrip, ...]
    notice: str = (
        "Descriptive research only; costs are supplied scenarios, not NSE fees or fill simulation."
    )


def pair_research_report(
    evaluations: tuple[PairEvaluation, ...],
    trades: tuple[PairRoundTrip, ...],
    scenarios: tuple[CostScenario, ...],
    experiment: ExperimentIdentity,
) -> PairResearchReport:
    if not evaluations:
        raise ValueError("pair report requires evaluations")
    cfg = evaluations[0].settings
    last_by_pair: dict[str, PairEvaluation] = {}
    initially_accepted = set()
    failed_after_acceptance = set()
    for evaluation in evaluations:
        if (
            evaluation.settings != cfg
            or evaluation.code_commit != experiment.code_commit
            or evaluation.data_snapshot != experiment.data_snapshot
        ):
            raise ValueError("report provenance/settings mismatch")
        previous = last_by_pair.get(evaluation.pair_id)
        if previous and evaluation.timestamp <= previous.timestamp:
            raise ValueError("pair evaluations must be chronological")
        if previous is None and evaluation.accepted_relationship:
            initially_accepted.add(evaluation.pair_id)
        if not evaluation.accepted_relationship and evaluation.pair_id in initially_accepted:
            failed_after_acceptance.add(evaluation.pair_id)
        last_by_pair[evaluation.pair_id] = evaluation
    fits = tuple(e.fit for e in evaluations if e.fit is not None)
    lives = tuple(f.half_life_bars for f in fits if f.half_life_bars is not None)
    costs = []
    for scenario in scenarios:
        gross = tuple(
            t.left_gross_fraction * t.left_signed_return
            + (1 - t.left_gross_fraction) * t.right_signed_return
            for t in trades
        )
        charges = tuple(
            2
            * (
                t.left_gross_fraction * scenario.left_one_way_bps
                + (1 - t.left_gross_fraction) * scenario.right_one_way_bps
            )
            / 10000
            for t in trades
        )
        costs.append(
            CostResult(
                scenario=scenario,
                mean_gross_return=fmean(gross) if gross else None,
                mean_round_trip_cost=fmean(charges) if charges else None,
                mean_net_return=fmean(g - c for g, c in zip(gross, charges, strict=True))
                if gross
                else None,
            )
        )
    return PairResearchReport(
        experiment=experiment,
        settings=cfg,
        evaluation_hashes=tuple(content_hash(e) for e in evaluations),
        evaluations=len(evaluations),
        accepted_fraction=sum(e.accepted_relationship for e in evaluations) / len(evaluations),
        initial_survivor_fraction=len(initially_accepted - failed_after_acceptance)
        / len(initially_accepted)
        if initially_accepted
        else 0,
        mean_beta_drift=fmean(f.split_beta_drift for f in fits) if fits else None,
        mean_half_life_bars=fmean(lives) if lives else None,
        costs=tuple(costs),
        round_trips=trades,
    )
