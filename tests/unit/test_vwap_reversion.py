from datetime import timedelta
from decimal import Decimal

import pytest
from tests.vwap_fixtures import vwap_inputs, vwap_result

from godzilla.alphas.vwap_mean_reversion import VwapMeanReversion, observe_vwap_exit
from godzilla.alphas.vwap_models import VwapSettings
from godzilla.config.modes import TradingMode
from godzilla.core.events.models import SignalIntent
from godzilla.features.vwap_reversion import ReversionFeatureVersion, reversion_features
from godzilla.market_state.models import MarketState


def test_numeric_vwap_scale_exhaustion_and_symmetric_intents(checker):
    long, short = vwap_result(checker), vwap_result(checker, short=True)
    assert long.intent.side == "LONG" and short.intent.side == "SHORT"
    f = long.intent.evidence.features
    # 10 ordinary ranges of .2, a 2.1 drop true range and a 1.1 drop true range.
    assert f.volatility_scale == pytest.approx((10 * 0.2 + 2.1 + 1.1) / 12)
    assert f.session_vwap == pytest.approx((12 * 100 + 98 + 97 + (97.5 + 96.9 + 97.4) / 3) / 15)
    assert f.stretch == pytest.approx((97.4 - f.session_vwap) / f.volatility_scale)
    assert f.impulse == pytest.approx(-2 / f.volatility_scale)
    assert f.decelerated_impulse == pytest.approx(-1 / f.volatility_scale)
    assert f.reversal == pytest.approx(0.4 / f.volatility_scale)
    assert short.intent.evidence.features.stretch == pytest.approx(-f.stretch)
    assert long.intent.score == pytest.approx(short.intent.score)
    assert long.intent.evidence.exits.adverse_price == pytest.approx(
        200 - short.intent.evidence.exits.adverse_price
    )
    assert SignalIntent.model_validate_json(long.intent.model_dump_json()) == long.intent


@pytest.mark.parametrize("regime", list(MarketState))
def test_regime_and_override_never_bypass_risk(checker, regime):
    args = list(vwap_inputs(checker))
    args[4] = args[4].model_copy(update={"state": regime})
    engine = VwapMeanReversion(VwapSettings(), checker.calendar)
    assert bool(engine.evaluate(*args).intent) == (regime is MarketState.CHOP)
    override = VwapMeanReversion(VwapSettings(research_regime_override=True), checker.calendar)
    assert bool(override.evaluate(*args).intent) == (
        regime not in (MarketState.RISK_OFF, MarketState.OPEN_SHOCK)
    )
    args[-1] = TradingMode.PAPER
    assert bool(override.evaluate(*args).intent) == (regime is MarketState.CHOP)
    args[-1] = TradingMode.LIVE
    assert override.evaluate(*args).reasons == ("LIVE_PROMOTION_REQUIRED",)


def test_stretch_is_insufficient_without_reversal_or_deceleration(checker):
    args = list(vwap_inputs(checker))
    bars = list(args[0])
    bars[-1] = bars[-1].model_copy(update={"open": Decimal("97.8"), "high": Decimal("97.9")})
    args[0] = tuple(bars)
    engine = VwapMeanReversion(VwapSettings(), checker.calendar)
    assert engine.evaluate(*args).reasons == ("REVERSAL_NOT_CONFIRMED",)
    args = list(vwap_inputs(checker))
    bars = list(args[0])
    bars[-3] = bars[-3].model_copy(
        update={
            "open": Decimal("99.5"),
            "close": Decimal("99.5"),
            "low": Decimal("99.4"),
            "high": Decimal("99.6"),
        }
    )
    args[0] = tuple(bars)
    assert engine.evaluate(*args).reasons == ("IMPULSE_NOT_DECELERATING",)


def test_time_freshness_warmup_and_ambiguity(checker):
    args = list(vwap_inputs(checker))
    engine = VwapMeanReversion(VwapSettings(), checker.calendar)
    assert VwapMeanReversion(VwapSettings(start_minutes=90), checker.calendar).evaluate(
        *args
    ).reasons == ("ENTRY_WINDOW_BLOCKED",)
    assert VwapMeanReversion(VwapSettings(enabled=False), checker.calendar).evaluate(
        *args
    ).reasons == ("ALPHA_DISABLED",)
    bars = args[0]
    args[0] = bars[1:]
    assert engine.evaluate(*args).reasons == ("SESSION_HISTORY_AMBIGUOUS",)
    args[0] = bars[:-2]
    assert engine.evaluate(*args).reasons == ("FEATURE_WARMUP",)
    args[0] = bars[:-1]
    assert engine.evaluate(*args).reasons == ("STALE_BAR",)
    args[0] = (*bars, bars[-1])
    assert engine.evaluate(*args).reasons == ("SESSION_HISTORY_AMBIGUOUS",)
    args[0] = (
        *bars[:-1],
        bars[-1].model_copy(update={"received_at": args[5] + timedelta(seconds=1)}),
    )
    assert engine.evaluate(*args).intent is None


def test_liquidity_universe_health_and_short_rejection(checker):
    args = list(vwap_inputs(checker, True))
    engine = VwapMeanReversion(VwapSettings(), checker.calendar)
    args[2] = args[2].model_copy(update={"short_tokens": ()})
    assert engine.evaluate(*args).reasons == ("SHORT_INELIGIBLE",)
    args = list(vwap_inputs(checker))
    args[1] = args[1].model_copy(update={"adv": 1})
    assert engine.evaluate(*args).reasons == ("LIQUIDITY_REJECTED",)
    args = list(vwap_inputs(checker))
    args[3] = args[3].model_copy(update={"generated_at": args[5] + timedelta(seconds=1)})
    assert engine.evaluate(*args).reasons == ("UNIVERSE_OR_INSTRUMENT_INELIGIBLE",)
    args = list(vwap_inputs(checker))
    args[4] = args[4].model_copy(
        update={
            "evidence": args[4].evidence.model_copy(
                update={
                    "health": args[4].evidence.health.model_copy(
                        update={"blocks_new_entries": True}
                    )
                }
            )
        }
    )
    assert engine.evaluate(*args).reasons == ("HEALTH_OR_RISK_VETO",)


@pytest.mark.parametrize("short", [False, True])
def test_exit_metadata_observation_is_symmetric_and_not_execution(checker, short):
    args = vwap_inputs(checker, short)
    evidence = vwap_result(checker, short).intent.evidence
    initial = args[0][-1]
    at = args[5] + timedelta(minutes=5)
    state = args[4].model_copy(update={"timestamp": at})

    def subsequent(price):
        price = Decimal(str(price))
        return initial.model_copy(
            update={
                "start": initial.end,
                "end": at,
                "received_at": at,
                "open": price,
                "close": price,
                "high": price + 1,
                "low": price - 1,
            }
        )

    assert observe_vwap_exit(evidence, subsequent(evidence.exits.convergence_price), state, at) == (
        "VWAP_CONVERGENCE",
    )
    assert observe_vwap_exit(evidence, subsequent(evidence.exits.adverse_price), state, at) == (
        "ADVERSE_CONTINUATION",
    )
    bar = subsequent(evidence.features.price)
    assert observe_vwap_exit(evidence, bar, state, at) == ("HOLD",)
    assert observe_vwap_exit(
        evidence, bar, state.model_copy(update={"state": MarketState.TREND_UP}), at
    ) == ("STATE_CHANGE",)
    assert observe_vwap_exit(evidence, initial, state, at) == ("EXIT_INPUT_UNAVAILABLE",)
    late = evidence.exits.time_stop
    bar = bar.model_copy(
        update={"start": late - timedelta(minutes=5), "end": late, "received_at": late}
    )
    assert observe_vwap_exit(evidence, bar, state.model_copy(update={"timestamp": late}), late) == (
        "TIME_STOP",
    )


def test_feature_and_settings_invalid_inputs(checker):
    args = vwap_inputs(checker)
    with pytest.raises(ValueError):
        VwapSettings(live_enabled=True)
    with pytest.raises(ValueError):
        VwapSettings(min_stretch=7)
    with pytest.raises(ValueError):
        VwapSettings(start_minutes=301)
    with pytest.raises(ValueError):
        reversion_features(
            args[0], args[5].replace(tzinfo=None), args[0][0].start, ReversionFeatureVersion()
        )
    flat = tuple(
        b.model_copy(
            update={
                "open": Decimal(100),
                "close": Decimal(100),
                "high": Decimal(100),
                "low": Decimal(100),
            }
        )
        for b in args[0]
    )
    with pytest.raises(ValueError, match="DEGENERATE_VOLATILITY"):
        reversion_features(flat, args[5], flat[0].start, ReversionFeatureVersion())


def test_configuration_layering_hash_and_live_promotion(monkeypatch):
    from godzilla.config.loader import load_settings
    from godzilla.config.models import Settings

    original = load_settings("config")
    assert original.vwap_reversion == VwapSettings()
    monkeypatch.setenv("GODZILLA_VWAP_REVERSION__MIN_STRETCH", "2.5")
    changed = load_settings("config")
    assert changed.vwap_reversion.min_stretch == 2.5
    assert changed.config_hash() != original.config_hash()
    assert changed.vwap_reversion.version_hash() != original.vwap_reversion.version_hash()
    payload = original.model_dump()
    payload["mode"] = "LIVE"
    payload["alphas"]["vwap_mean_reversion"] = "ENABLED"
    with pytest.raises(ValueError, match="independent backtest promotion"):
        Settings.model_validate(payload)


def test_trigger_cannot_change_prior_scale_and_no_signal_before_close(checker):
    args = list(vwap_inputs(checker))
    original = reversion_features(args[0], args[5], args[0][0].start, ReversionFeatureVersion())
    altered = args[0][-1].model_copy(update={"high": Decimal(110)})
    updated = reversion_features(
        (*args[0][:-1], altered), args[5], args[0][0].start, ReversionFeatureVersion()
    )
    assert original.volatility_scale == updated.volatility_scale
    assert original.input_hash != updated.input_hash
    args[5] -= timedelta(seconds=1)
    args[4] = args[4].model_copy(update={"timestamp": args[5]})
    assert VwapMeanReversion(VwapSettings(), checker.calendar).evaluate(*args).intent is None


def test_extreme_stretch_and_evidence_integrity(checker):
    args = vwap_inputs(checker)
    assert VwapMeanReversion(VwapSettings(max_stretch=3), checker.calendar).evaluate(
        *args
    ).reasons == ("STRETCH_OUTSIDE_LIMITS",)
    evidence = vwap_result(checker).intent.evidence
    payload = evidence.model_dump()
    payload["alpha_version_hash"] = "wrong"
    with pytest.raises(ValueError, match="identity/version/timing"):
        type(evidence).model_validate(payload)
