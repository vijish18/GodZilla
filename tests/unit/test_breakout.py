from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.breakout_fixtures import breakout_inputs, breakout_result
from tests.momentum_fixtures import changed

from godzilla.alphas.breakout_models import (
    BreakoutSettings,
    BreakoutSignalEvidence,
    BreakoutWeights,
)
from godzilla.alphas.momentum_breakout import MomentumBreakout, observe_failed_breakout
from godzilla.config.loader import load_settings
from godzilla.core.events.models import SignalIntent
from godzilla.market_state.models import MarketState

pytestmark = pytest.mark.unit


def test_long_short_symmetry_and_components(checker):
    long, short = breakout_result(checker), breakout_result(checker, -1)
    assert [i.instrument_id for i in long.intents] == ["T08", "T09"]
    assert [i.instrument_id for i in short.intents] == ["T08", "T09"]
    for a, b in zip(long.intents, short.intents, strict=True):
        assert a.side == "LONG" and b.side == "SHORT"
        assert a.score == pytest.approx(b.score)
        assert a.signal_id == b.signal_id  # opposite-side re-use conflicts in the journal
        assert isinstance(a.evidence, BreakoutSignalEvidence)
        assert SignalIntent.model_validate_json(a.model_dump_json()) == a
        components = {c.name: c for c in a.evidence.components}
        assert components["relative_volume"].normalized_value == pytest.approx(2.5 / 3)
        assert components["relative_strength"].weight == 0.25
        assert components["reward_risk_proxy"].raw_value == pytest.approx(2 / 0.3)
        assert a.evidence.invalidation.invalidation_level == pytest.approx(99.8)
        assert b.evidence.invalidation.invalidation_level == pytest.approx(100.2)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"liquidity.spread_bps": 13}, "REJECTED:liquidity.spread_bps"),
        ({"liquidity.quote_age": 4}, "REJECTED:liquidity.quote_age"),
        ({"stock.relative_volume": 1}, "REJECTED:stock.relative_volume"),
        ({"sector.vwap_distance": -0.01}, "REJECTED:sector.direction_confirmed"),
        ({"stock.ema_20_slope": -0.01}, "REJECTED:stock.slope_confirmed"),
        ({"stock.session_vwap": 101}, "REJECTED:stock.above_below_vwap"),
        ({"stock.atr": 0}, "INVALID_ATR_OR_FLAT_BAR"),
        ({"stock.relative_volume": None}, "MISSING_FEATURE:stock.relative_volume"),
    ],
)
def test_liquidity_and_confirmation_rejections(checker, change, reason):
    items, *rest = breakout_inputs(checker)
    items = tuple(o.model_copy(update={"current": changed(o.current, change)}) for o in items)
    result = MomentumBreakout(BreakoutSettings(), checker.calendar).evaluate(items, *rest)
    assert not result.intents
    assert reason in result.reports[-1].reasons


def test_bar_close_reference_and_or_warmup_causality(checker):
    items, universe, master, state, at = breakout_inputs(checker)
    engine = MomentumBreakout(BreakoutSettings(), checker.calendar)
    assert not engine.evaluate(items, universe, master, state, at - timedelta(seconds=1)).intents
    for bar in (
        items[-1].bar.model_copy(update={"complete": False}),
        items[-1].bar.model_copy(update={"received_at": at + timedelta(seconds=1)}),
        items[-1].bar.model_copy(update={"end": at + timedelta(minutes=5)}),
    ):
        result = engine.evaluate(
            (*items[:-1], items[-1].model_copy(update={"bar": bar})), universe, master, state, at
        )
        assert result.reasons[0] == "INPUT_BLOCKED" and not result.intents
    future_ref = items[-1].reference.model_copy(
        update={"snapshot": items[-1].reference.snapshot.model_copy(update={"timestamp": at})}
    )
    assert not engine.evaluate(
        (*items[:-1], items[-1].model_copy(update={"reference": future_ref})),
        universe,
        master,
        state,
        at,
    ).intents
    # Even an erroneously populated early OR snapshot cannot satisfy its warm-up gate.
    early = state.evidence.session_open + timedelta(minutes=10)
    items = tuple(
        o.model_copy(
            update={
                "reference": o.reference.model_copy(
                    update={
                        "snapshot": o.reference.snapshot.model_copy(update={"timestamp": early})
                    }
                )
            }
        )
        for o in items
    )
    result = MomentumBreakout(
        BreakoutSettings(range_source="opening_range"), checker.calendar
    ).evaluate(items, universe, master, state, at)
    assert not result.intents
    assert result.reports[-1].reasons == ("NO_NEW_BREAK_OF_KNOWN_LEVEL",)


def test_swing_source_repeat_break_and_state_permissions(checker):
    assert all(
        i.evidence.invalidation.range_kind == "swing"
        for i in breakout_result(checker, range_source="swing").intents
    )
    items, universe, master, state, at = breakout_inputs(checker)
    engine = MomentumBreakout(BreakoutSettings(), checker.calendar)
    already = tuple(
        o.model_copy(
            update={"previous_bar": o.previous_bar.model_copy(update={"close": Decimal("100.2")})}
        )
        for o in items
    )
    assert not engine.evaluate(already, universe, master, state, at).intents
    for update in (
        {"state": MarketState.CHOP},
        {"directional_sides": ()},
        {"allowed_alpha_families": ()},
        {"gross_risk_multiplier": 0},
    ):
        assert not engine.evaluate(
            items, universe, master, state.model_copy(update=update), at
        ).intents
    assert not breakout_result(checker, enabled=False).intents
    assert not breakout_result(checker, minimum_score=1).intents
    items, universe, master, state, at = breakout_inputs(checker, -1)
    assert not engine.evaluate(
        items, universe.model_copy(update={"short_tokens": ()}), master, state, at
    ).intents


def test_missing_population_or_reference_and_configuration(checker, monkeypatch):
    items, universe, master, state, at = breakout_inputs(checker)
    engine = MomentumBreakout(BreakoutSettings(), checker.calendar)
    assert not engine.evaluate(items[:-1], universe, master, state, at).intents
    no_levels = tuple(
        o.model_copy(
            update={
                "reference": changed(
                    o.reference, {"stock.opening_range_high": None, "stock.swing_high": None}
                )
            }
        )
        for o in items
    )
    assert not engine.evaluate(no_levels, universe, master, state, at).intents
    path = Path(__file__).parents[2] / "config"
    before = load_settings(path)
    monkeypatch.setenv("GODZILLA_BREAKOUT__MINIMUM_RELATIVE_VOLUME", "2")
    after = load_settings(path)
    assert after.breakout.minimum_relative_volume == 2
    assert after.config_hash() != before.config_hash()
    for kwargs in (
        {"min_extension_atr": 1},
        {"minimum_relative_volume": 4},
        {"minimum_score": float("nan")},
        {"range_source": "unknown"},
    ):
        with pytest.raises(ValidationError):
            BreakoutSettings(**kwargs)
    with pytest.raises(ValidationError):
        BreakoutWeights(relative_strength=0.5)


def test_failed_breakout_metadata_is_observation_only(checker):
    items, *_rest = breakout_inputs(checker)
    intent = breakout_result(checker).intents[-1]
    detail = intent.evidence
    initial = items[-1].bar
    assert observe_failed_breakout(detail, initial, initial.end).status == "UNAVAILABLE"
    following = initial.model_copy(
        update={
            "start": initial.end,
            "end": initial.end + timedelta(minutes=5),
            "received_at": initial.end + timedelta(minutes=5),
        }
    )
    assert observe_failed_breakout(detail, following, following.end).status == "ACTIVE"
    failed = following.model_copy(update={"low": Decimal(99), "close": Decimal("99.7")})
    assert observe_failed_breakout(detail, failed, failed.end).status == "FAILED"
    assert observe_failed_breakout(detail, failed, initial.end).status == "UNAVAILABLE"
    expired = following.model_copy(
        update={
            "start": initial.end + timedelta(minutes=20),
            "end": initial.end + timedelta(minutes=25),
        }
    )
    assert observe_failed_breakout(detail, expired, expired.end).status == "EXPIRED"
    with pytest.raises(ValueError):
        observe_failed_breakout(
            detail, following.model_copy(update={"instrument_id": "wrong"}), following.end
        )
