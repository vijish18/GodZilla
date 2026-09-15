import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.momentum_fixtures import changed, momentum_inputs, momentum_result

from godzilla.alphas.cross_sectional_momentum import CrossSectionalMomentum
from godzilla.alphas.momentum_models import MomentumSettings, MomentumWeights
from godzilla.alphas.momentum_research import ForwardReturn, quantile_diagnostics
from godzilla.alphas.ranking import percentiles, without_recent
from godzilla.config.loader import load_settings
from godzilla.core.events.models import SignalIntent
from godzilla.core.events.serialization import deserialize_event, event_hash, serialize_event
from godzilla.market_state.models import MarketState
from godzilla.market_state.research import ExperimentIdentity

pytestmark = pytest.mark.unit


def test_hand_computed_ranks_scores_and_mirror(checker):
    assert percentiles((3, 1, 1, 5)) == (0.625, 0.25, 0.25, 0.875)
    long = momentum_result(checker)
    short = momentum_result(checker, -1)
    assert [s.instrument_id for s in long.intents] == ["T09", "T08"]
    assert [s.instrument_id for s in short.intents] == ["T00", "T01"]
    assert long.intents[0].score == pytest.approx(short.intents[0].score)
    component = long.intents[0].evidence.ranked.components[0]
    assert component.rank_value == 0.95
    assert component.long_contribution == pytest.approx(0.19)
    assert component.short_contribution == pytest.approx(0.01)
    assert len({s.instrument_id for s in long.intents}) == len(long.intents)
    assert SignalIntent.model_validate_json(long.intents[0].model_dump_json()) == long.intents[0]


def test_order_invariance_full_ties_and_capped_ties(checker):
    obs, *rest = momentum_inputs(checker)
    engine = CrossSectionalMomentum(MomentumSettings())
    assert engine.evaluate(obs[::-1], *rest) == engine.evaluate(obs, *rest)
    assert not engine.evaluate(*momentum_inputs(checker, tied=True)).intents
    obs = (
        *obs[:-1],
        changed(
            obs[-1],
            {
                name: obs[-2].snapshot.get(name).value
                for name in ("stock.return_30m", "stock.return_60m", "stock.return_120m")
            },
        ),
    )
    capped = CrossSectionalMomentum(MomentumSettings(max_candidates_per_side=1)).evaluate(
        obs, *rest
    )
    assert not capped.intents


def test_missing_duplicates_unknown_and_universe_metadata(checker):
    obs, universe, master, state, at = momentum_inputs(checker)
    engine = CrossSectionalMomentum(MomentumSettings())
    for items in (obs[:-1], (*obs, obs[0])):
        assert not engine.evaluate(items, universe, master, state, at).scores
    future = universe.model_copy(update={"generated_at": at + timedelta(seconds=1)})
    assert engine.evaluate(obs, future, master, state, at).reasons == ("UNIVERSE_UNAVAILABLE",)
    missing = (*obs[:-1], changed(obs[-1], {"stock.return_120m": None}))
    assert (
        engine.evaluate(missing, universe, master, state, at).reasons[0]
        == "INVALID_OR_MISSING_INPUT"
    )
    bad_context = obs[0].context.model_copy(update={"universe_version": "wrong"})
    assert not engine.evaluate(
        (obs[0].model_copy(update={"context": bad_context}), *obs[1:]), universe, master, state, at
    ).scores


def test_state_liquidity_trend_short_eligibility_and_disable(checker):
    obs, universe, master, state, at = momentum_inputs(checker)
    engine = CrossSectionalMomentum(MomentumSettings())
    for update in (
        {"state": MarketState.CHOP},
        {"directional_sides": ()},
        {"timestamp": at - timedelta(minutes=5)},
        {"gross_risk_multiplier": 0},
    ):
        assert not engine.evaluate(
            obs, universe, master, state.model_copy(update=update), at
        ).intents
    for changes in ({"liquidity.spread_bps": 13}, {"stock.ema_20_slope": -0.001}):
        result = engine.evaluate(
            (*obs[:-1], changed(obs[-1], changes)), universe, master, state, at
        )
        assert "T09" not in [s.instrument_id for s in result.intents]
    obs, universe, master, state, at = momentum_inputs(checker, -1)
    result = engine.evaluate(
        obs, universe.model_copy(update={"short_tokens": ()}), master, state, at
    )
    assert not result.intents
    assert (
        not CrossSectionalMomentum(MomentumSettings(enabled=False))
        .evaluate(obs, universe, master, state, at)
        .scores
    )


def test_micro_window_exclusion_and_invalid_returns(checker):
    assert without_recent(0.1, 0.05) == pytest.approx(1.1 / 1.05 - 1)
    obs, *rest = momentum_inputs(checker)
    obs = (*obs[:-1], changed(obs[-1], {"stock.return_5m": 0.2}))
    full = CrossSectionalMomentum(MomentumSettings()).evaluate(obs, *rest)
    excluded = CrossSectionalMomentum(MomentumSettings(exclude_recent_5m=True)).evaluate(obs, *rest)
    assert full.scores[-1].percentile > excluded.scores[-1].percentile
    assert (
        full.intents[0].evidence.alpha_version_hash
        != excluded.intents[0].evidence.alpha_version_hash
    )
    with pytest.raises(ValueError):
        without_recent(0, -1)
    bad = (*obs[:-1], changed(obs[-1], {"stock.return_30m": -1}))
    assert not CrossSectionalMomentum(MomentumSettings()).evaluate(bad, *rest).scores


def test_config_hash_validation(monkeypatch):
    path = Path(__file__).parents[2] / "config"
    before = load_settings(path)
    monkeypatch.setenv("GODZILLA_MOMENTUM__EXCLUDE_RECENT_5M", "true")
    after = load_settings(path)
    assert after.momentum.exclude_recent_5m
    assert before.config_hash() != after.config_hash()
    for values in (
        {"return_30m": 1},
        {"return_30m": float("nan")},
        {
            "return_30m": 0,
            "return_60m": 0,
            "return_120m": 0,
            "relative_sector_30m": 0,
            "relative_market_30m": 0,
            "trend_quality": 0,
            "liquidity_quality": 1,
        },
    ):
        with pytest.raises(ValidationError):
            MomentumWeights(**values)
    with pytest.raises(ValidationError):
        MomentumSettings(long_tail_fraction=0.6)
    with pytest.raises(ValueError):
        percentiles((float("nan"),))


def test_quantile_diagnostics_are_label_isolated(checker):
    result = momentum_result(checker)
    end = result.timestamp + timedelta(minutes=30)
    labels = tuple(
        ForwardReturn(
            instrument_id=s.instrument_id,
            decision_at=result.timestamp,
            measured_at=end,
            return_fraction=(i - 4.5) * 0.01,
        )
        for i, s in enumerate(result.scores)
    )
    experiment = ExperimentIdentity(
        hypothesis_id="alpha-a-v1",
        code_commit=result.code_commit,
        data_snapshot=result.data_snapshot,
        search_family="fixed-v1",
        trials=1,
    )
    report = quantile_diagnostics(result, labels, experiment, end)
    assert all(q.count == 1 for q in report.quantiles)
    assert report.gross_long_tail_return == pytest.approx(0.045)
    assert report.gross_short_tail_return == pytest.approx(0.045)
    assert report.gross_long_short_return == pytest.approx(0.045)
    assert report == quantile_diagnostics(result, labels[::-1], experiment, end)
    for subset, time in ((labels[:-1], end), (labels, result.timestamp)):
        with pytest.raises(ValueError):
            quantile_diagnostics(result, subset, experiment, time)
    with pytest.raises(ValueError):
        quantile_diagnostics(result, labels, experiment, end, 1)
    with pytest.raises(ValueError):
        quantile_diagnostics(
            result, labels, experiment.model_copy(update={"code_commit": "bad"}), end
        )
    with pytest.raises(ValidationError):
        ForwardReturn(instrument_id="X", decision_at=end, measured_at=end, return_fraction=0)


def test_legacy_signal_hash_and_score_consistency(event, checker):
    raw = event.model_dump(mode="json")
    raw["payload"] = {
        "kind": "SIGNAL_INTENT",
        "signal_id": str(event.event_id),
        "instrument_id": "X",
        "alpha_id": "legacy",
        "side": "LONG",
        "reasons": [],
    }
    wire = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert serialize_event(deserialize_event(wire)) == wire
    assert event_hash(deserialize_event(wire)) == hashlib.sha256(wire.encode()).hexdigest()
    intent = momentum_result(checker).intents[0]
    with pytest.raises(ValidationError):
        SignalIntent.model_validate(intent.model_dump() | {"score": 0})
