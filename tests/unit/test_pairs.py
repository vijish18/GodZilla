import math
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.pairs_fixtures import pairs_inputs

from godzilla.alphas.pairs_engine import PairsEngine
from godzilla.alphas.pairs_models import PairPosition, PairSignalIntent, PairsSettings
from godzilla.alphas.pairs_research import CostScenario, PairRoundTrip, pair_research_report
from godzilla.alphas.pairs_statistics import (
    StatsmodelsDiagnostics,
    fit_pair,
    ols,
    relationship_rejections,
    spread_z,
)
from godzilla.config.loader import load_settings
from godzilla.config.modes import TradingMode
from godzilla.market_state.research import ExperimentIdentity

pytestmark = pytest.mark.unit


def engine(checker, **settings):
    return PairsEngine(PairsSettings(**settings), StatsmodelsDiagnostics(), checker.calendar)


def test_synthetic_cointegrated_and_noncointegrated(checker):
    accepted = engine(checker).evaluate(*pairs_inputs(checker))
    assert accepted.accepted_relationship, accepted.reasons
    assert accepted.fit.tests.cointegration_p < 0.05
    assert accepted.fit.half_life_bars is not None
    assert accepted.intent is not None, accepted.reasons
    rejected = engine(checker).evaluate(*pairs_inputs(checker, cointegrated=False))
    assert not rejected.accepted_relationship
    assert rejected.intent is None
    assert "COINTEGRATION_REJECTED" in rejected.reasons


def test_zscore_ols_and_sign_symmetry(checker):
    positive = engine(checker).evaluate(*pairs_inputs(checker, z=2.5))
    negative = engine(checker).evaluate(*pairs_inputs(checker, z=-2.5))
    assert positive.fit == negative.fit
    assert positive.zscore == pytest.approx(2.5)
    assert negative.zscore == pytest.approx(-2.5)
    assert positive.intent.direction == "SHORT_SPREAD"
    assert negative.intent.direction == "LONG_SPREAD"
    assert positive.intent.legs[0].side == "SHORT" and positive.intent.legs[1].side == "LONG"
    assert negative.intent.legs[0].side == "LONG" and negative.intent.legs[1].side == "SHORT"
    assert sum(leg.gross_fraction for leg in positive.intent.legs) == pytest.approx(1)
    assert positive.intent.execution.both_legs_required
    assert not positive.intent.execution.broker_atomicity_assumed
    assert (
        PairSignalIntent.model_validate_json(positive.intent.model_dump_json()) == positive.intent
    )
    assert ols((3.0, 5.0, 7.0), (1.0, 2.0, 3.0)) == pytest.approx((1, 2))
    with pytest.raises(ValueError):
        ols((1.0, 2.0, 3.0), (1.0, 1.0, 1.0))


def test_hard_rejections_and_live_never_enabled(checker):
    args = list(pairs_inputs(checker))
    for mutation in (
        {0: args[0].model_copy(update={"corporate_actions_clear": False})},
        {1: args[1][-20:]},
        {2: (args[2][0].model_copy(update={"adv": 1}), args[2][1])},
        {7: TradingMode.LIVE},
        {3: args[3].model_copy(update={"short_tokens": ()})},
    ):
        edited = args.copy()
        for index, value in mutation.items():
            edited[index] = value
        assert engine(checker).evaluate(*edited).intent is None
    assert engine(checker).evaluate(*args[:-1], TradingMode.LIVE).reasons == ("PAIR_LIVE_DISABLED",)
    assert not engine(checker, enabled=False).evaluate(*args).intent
    assert not engine(checker).evaluate(*pairs_inputs(checker, z=5)).intent
    with pytest.raises(ValidationError):
        PairsSettings(live_enabled=True)
    for kwargs in ({"entry_z": 0.1}, {"min_beta": 3}, {"training_bars": 10}):
        with pytest.raises(ValidationError):
            PairsSettings(**kwargs)


def test_structural_diagnostics_reject_breaks(checker):
    fit = engine(checker).evaluate(*pairs_inputs(checker)).fit
    for change, reason in (
        ({"split_beta_drift": 1}, "UNSTABLE_BETA"),
        ({"spread_std_ratio": 10}, "UNSTABLE_SPREAD_VARIANCE"),
        ({"spread_mean_shift": 4}, "UNSTABLE_SPREAD_MEAN"),
        ({"half_life_bars": None}, "HALF_LIFE_REJECTED"),
    ):
        assert reason in relationship_rejections(fit.model_copy(update=change), PairsSettings())


def test_exit_frozen_model_max_hold_and_structural_break(checker):
    args = list(pairs_inputs(checker, z=0.1))
    fit = fit_pair(args[1][:-1], PairsSettings(), StatsmodelsDiagnostics())
    # A prior, separately frozen entry fit; its training ends before entry.
    earlier_fit = fit.model_copy(update={"training_end": args[6] - timedelta(minutes=70)})
    position = PairPosition(
        position_id=UUID(int=9),
        pair_id=args[0].pair_id,
        direction="SHORT_SPREAD",
        opened_at=args[6] - timedelta(minutes=5),
        entry_fit=earlier_fit,
        entry_z=2.5,
    )
    result = engine(checker).evaluate(*args, position=position)
    assert result.intent.action == "EXIT" and result.reasons == ("SPREAD_REVERSION",)
    assert result.intent.hedge_ratio == earlier_fit.beta
    assert result.intent.legs[0].side == "LONG"
    held = position.model_copy(update={"opened_at": args[6] - timedelta(minutes=61)})
    assert engine(checker).evaluate(*args, position=held).reasons == ("MAX_HOLD",)
    broken = position.model_copy(
        update={"entry_fit": earlier_fit.model_copy(update={"beta": earlier_fit.beta * 2})}
    )
    assert engine(checker).evaluate(*args, position=broken).reasons[0] == "STRUCTURAL_BREAK"


def test_bad_diagnostic_provider_fails_closed(checker):
    class Broken:
        def test(self, *args):
            raise RuntimeError("unavailable")

    result = PairsEngine(PairsSettings(), Broken(), checker.calendar).evaluate(
        *pairs_inputs(checker)
    )
    assert result.reasons == ("STATISTICAL_DIAGNOSTICS_UNAVAILABLE",)


def test_report_two_leg_costs_and_config(checker, monkeypatch):
    result = engine(checker).evaluate(*pairs_inputs(checker))
    identity = ExperimentIdentity(
        hypothesis_id="pair-fixed-v1",
        code_commit=result.code_commit,
        data_snapshot=result.data_snapshot,
        search_family="synthetic",
        trials=1,
        seed=7,
    )
    report = pair_research_report(
        (result,),
        (
            PairRoundTrip(
                left_gross_fraction=0.5, left_signed_return=0.01, right_signed_return=0.02
            ),
        ),
        (
            CostScenario(name="synthetic-base", left_one_way_bps=5, right_one_way_bps=10),
            CostScenario(name="stress", left_one_way_bps=10, right_one_way_bps=20),
        ),
        identity,
    )
    assert report.initial_survivor_fraction == 1
    assert report.costs[0].mean_gross_return == pytest.approx(0.015)
    assert report.costs[0].mean_round_trip_cost == pytest.approx(0.0015)
    assert report.costs[1].mean_net_return == pytest.approx(0.012)
    path = Path(__file__).parents[2] / "config"
    before = load_settings(path)
    monkeypatch.setenv("GODZILLA_PAIRS__ENTRY_Z", "2.2")
    after = load_settings(path)
    assert after.config_hash() != before.config_hash()
    assert after.pairs.entry_z == 2.2


def test_training_excludes_current_and_rejects_gaps(checker):
    args = list(pairs_inputs(checker))
    result = engine(checker).evaluate(*args)
    current = args[1][-1]
    changed = current.model_copy(
        update={
            "left": current.left.model_copy(
                update={
                    "open": Decimal(500),
                    "close": Decimal(500),
                    "high": Decimal(501),
                    "low": Decimal(499),
                }
            )
        }
    )
    args[1] = (*args[1][:-1], changed)
    other = engine(checker).evaluate(*args)
    assert other.fit == result.fit
    assert other.zscore != result.zscore
    assert result.fit.training_end < current.left.end
    spread, z = spread_z(current, result.fit)
    assert spread == pytest.approx(
        math.log(float(current.left.close)) - result.fit.beta * math.log(float(current.right.close))
    )
    assert z == pytest.approx((spread - result.fit.spread_mean) / result.fit.spread_std)
    args[1] = (*args[1][:100], *args[1][101:])
    assert not engine(checker).evaluate(*args).intent


def test_flat_window_delayed_receipts_and_liquidity_exit(checker):
    args = list(pairs_inputs(checker))
    result = engine(checker).evaluate(*args)
    position = PairPosition(
        position_id=UUID(int=9),
        pair_id=args[0].pair_id,
        direction="SHORT_SPREAD",
        opened_at=args[6] - timedelta(minutes=5),
        entry_fit=result.fit.model_copy(update={"training_end": args[6] - timedelta(minutes=10)}),
        entry_z=2.5,
    )
    args[2] = (args[2][0].model_copy(update={"adv": 1}), args[2][1])
    assert engine(checker).evaluate(*args, position=position).reasons == (
        "LIQUIDITY_DETERIORATION",
    )
    original = args[1]
    late = original[-1].model_copy(
        update={
            "left": original[-1].left.model_copy(
                update={"received_at": args[6] + timedelta(seconds=1)}
            )
        }
    )
    args[1] = (*original[:-1], late)
    assert engine(checker).evaluate(*args, position=position).open_risk_attention
    with pytest.raises(ValueError):
        fit_pair(original[:10], PairsSettings(), StatsmodelsDiagnostics())
    with pytest.raises(ValueError):
        ols((1.0, 2.0), (1.0, 2.0))


def test_adverse_and_state_exit_without_new_entry(checker):
    args = list(pairs_inputs(checker, z=4.5))
    fitted = fit_pair(args[1][:-1], PairsSettings(), StatsmodelsDiagnostics())
    position = PairPosition(
        position_id=UUID(int=9),
        pair_id=args[0].pair_id,
        direction="SHORT_SPREAD",
        opened_at=args[6] - timedelta(minutes=5),
        entry_fit=fitted.model_copy(update={"training_end": args[6] - timedelta(minutes=10)}),
        entry_z=2.5,
    )
    assert engine(checker).evaluate(*args, position=position).reasons == (
        "SPREAD_ADVERSE_BOUNDARY",
    )
    args[5] = args[5].model_copy(update={"allowed_alpha_families": ()})
    assert engine(checker).evaluate(*args, position=position).reasons == (
        "STATE_PERMISSION_WITHDRAWN",
    )


def test_report_survival_cannot_recover_and_bad_sequences(checker):
    good = engine(checker).evaluate(*pairs_inputs(checker))
    bad = good.model_copy(
        update={"timestamp": good.timestamp + timedelta(minutes=5), "accepted_relationship": False}
    )
    recovered = good.model_copy(update={"timestamp": good.timestamp + timedelta(minutes=10)})
    identity = ExperimentIdentity(
        hypothesis_id="fixed",
        code_commit=good.code_commit,
        data_snapshot=good.data_snapshot,
        search_family="fixed",
        trials=1,
    )
    report = pair_research_report((good, bad, recovered), (), (), identity)
    assert report.initial_survivor_fraction == 0
    for rows in ((), (good, good), (good.model_copy(update={"code_commit": "wrong"}),)):
        with pytest.raises(ValueError):
            pair_research_report(rows, (), (), identity)
