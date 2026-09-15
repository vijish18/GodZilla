import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.state_fixtures import healthy, state_snapshot

from godzilla.config.loader import load_settings
from godzilla.core.events.models import MarketStateUpdate
from godzilla.core.events.serialization import deserialize_event, event_hash, serialize_event
from godzilla.core.health import ComponentHealth, HealthStatus, SystemHealth
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_state.models import (
    AlphaFamily,
    MarketState,
    RouterSettings,
    RoutingHealth,
    StateDecision,
)
from godzilla.market_state.research import (
    ExperimentIdentity,
    ResearchObservation,
    compare_baseline,
    simple_vwap_slope,
)
from godzilla.market_state.router import MarketStateRouter

pytestmark = pytest.mark.unit


def router(checker, **kwargs):
    return MarketStateRouter(
        RouterSettings(**kwargs), checker.calendar, checker.timezone, MemoryMetrics()
    )


@pytest.mark.parametrize(
    "changes,state",
    [
        ({}, MarketState.TREND_UP),
        (
            {
                "market.vwap_distance": -0.003,
                "market.slope_15m": -0.001,
                "market.slope_30m": -0.0005,
                "market.breadth_advancing": 0.3,
                "market.breadth_declining": 0.7,
            },
            MarketState.TREND_DOWN,
        ),
        (
            {
                "market.vwap_distance": 0,
                "market.slope_15m": 0,
                "market.slope_30m": 0,
                "market.breadth_advancing": 0.5,
                "market.breadth_declining": 0.5,
            },
            MarketState.CHOP,
        ),
        ({"market.realized_volatility": 0.006}, MarketState.HIGH_VOL),
        ({"market.vix_level": 30}, MarketState.HIGH_VOL),
        ({"market.range_volatility": 0.02}, MarketState.HIGH_VOL),
        ({"market.slope_15m": None}, MarketState.RISK_OFF),
        ({"market.breadth_coverage": 0.9}, MarketState.RISK_OFF),
    ],
)
def test_bull_bear_chop_vol_and_missing(checker, changes, state):
    engine = router(checker, confirmation_count=1, min_duration_seconds=0)
    snapshot = state_snapshot(changes=changes)
    result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is state
    assert result.evidence.router_version_hash == engine.settings.version_hash()
    assert StateDecision.model_validate_json(result.model_dump_json()) == result
    assert result.evidence.thresholds
    if state is MarketState.RISK_OFF:
        assert not result.allowed_alpha_families and result.gross_risk_multiplier == 0
    if state is MarketState.CHOP:
        assert AlphaFamily.VWAP_MEAN_REVERSION in result.allowed_alpha_families


@pytest.mark.parametrize(
    "start,gap", [("2026-09-15T09:20:00+05:30", 0), ("2026-09-15T09:40:00+05:30", -0.02)]
)
def test_opening_shock_is_immediate_and_observe_only(checker, start, gap):
    snapshot = state_snapshot(changes={"market.opening_gap": gap}, start=start)
    result = router(checker).route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.OPEN_SHOCK
    assert result.gross_risk_multiplier == 0
    assert not result.allowed_alpha_families


@pytest.mark.parametrize(
    "update",
    [
        {"blocks_new_entries": True},
        {"risk_status": HealthStatus.UNHEALTHY},
        {"system_status": HealthStatus.UNKNOWN},
        {"system_status": HealthStatus.DEGRADED},
    ],
)
def test_risk_off_overrides_trend_and_duration(checker, update):
    engine = router(checker, confirmation_count=1, min_duration_seconds=0)
    first = state_snapshot()
    assert engine.route(first, healthy(first), first.timestamp).state is MarketState.TREND_UP
    second = state_snapshot(1)
    result = engine.route(second, healthy(second).model_copy(update=update), second.timestamp)
    assert result.state is MarketState.RISK_OFF
    assert result.gross_risk_multiplier == 0
    assert "SAFETY_VETO" in result.evidence.reasons


def test_confirmation_duration_duplicate_and_pending_permissions(checker):
    engine = router(checker)
    for step, expected in enumerate(
        (MarketState.RISK_OFF, MarketState.RISK_OFF, MarketState.TREND_UP)
    ):
        snapshot = state_snapshot(step)
        result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
        assert result.state is expected
        assert engine.route(snapshot, healthy(snapshot), snapshot.timestamp) == result
    assert engine.metrics.counters["market_state.decisions"] == 3
    bear = {
        "market.vwap_distance": -0.003,
        "market.slope_15m": -0.001,
        "market.slope_30m": -0.001,
        "market.breadth_advancing": 0.3,
        "market.breadth_declining": 0.7,
    }
    snapshot = state_snapshot(3, bear)
    pending = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert pending.state is MarketState.TREND_UP
    assert pending.candidate is MarketState.TREND_DOWN
    assert not pending.directional_sides
    assert AlphaFamily.MOMENTUM_BREAKOUT not in pending.allowed_alpha_families
    snapshot = state_snapshot(4)
    assert (
        engine.route(snapshot, healthy(snapshot), snapshot.timestamp).state is MarketState.TREND_UP
    )
    assert engine.metrics.counters["market_state.suppressed_transitions"] == 3


def test_repeated_feature_does_not_confirm_and_gaps_reset(checker):
    engine = router(checker, min_duration_seconds=0)
    snapshot = state_snapshot()
    engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    later = snapshot.timestamp + timedelta(seconds=1)
    result = engine.route(snapshot, healthy(snapshot), later)
    assert result.confirmation_count == 1
    jumped = state_snapshot(5)
    result = engine.route(jumped, healthy(jumped), jumped.timestamp)
    assert result.confirmation_count == 1 and result.state is MarketState.RISK_OFF


def test_stale_future_closed_session_and_input_conflicts(checker):
    for start in (
        "2026-09-14T11:00:00+05:30",
        "2027-09-15T11:00:00+05:30",
        "2026-09-15T16:00:00+05:30",
    ):
        snapshot = state_snapshot(start=start)
        assert (
            router(checker).route(snapshot, healthy(snapshot), snapshot.timestamp).state
            is MarketState.RISK_OFF
        )
    snapshot = state_snapshot()
    for offset in (-1, 301):
        at = snapshot.timestamp + timedelta(seconds=offset)
        result = router(checker).route(snapshot, healthy(snapshot), at)
        assert result.state is MarketState.RISK_OFF
    engine = router(checker)
    engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    with pytest.raises(ValueError, match="conflicting"):
        engine.route(
            state_snapshot(changes={"market.vix_level": 30}), healthy(snapshot), snapshot.timestamp
        )
    with pytest.raises(ValueError, match="regressed"):
        engine.route(snapshot, healthy(snapshot), snapshot.timestamp - timedelta(seconds=1))


def test_settings_layer_hash_and_validation(monkeypatch):
    path = Path(__file__).parents[2] / "config"
    before = load_settings(path)
    monkeypatch.setenv("GODZILLA_MARKET_STATE__BREADTH_FRACTION", "0.7")
    after = load_settings(path)
    assert after.market_state.breadth_fraction == 0.7
    assert after.config_hash() != before.config_hash()
    assert after.market_state.version_hash() != before.market_state.version_hash()
    for kwargs in (
        {"high_vix": float("nan")},
        {"breadth_fraction": 0.4},
        {"confirmation_count": 0},
        {"high_vol_multiplier": 0.9},
        {"shock_window_seconds": 1},
        {"formula_revision": "2"},
    ):
        with pytest.raises(ValidationError):
            RouterSettings(**kwargs)


def test_system_health_adapter_and_legacy_event_hash(event):
    snapshot = state_snapshot()
    component = ComponentHealth("data", HealthStatus.UNHEALTHY, snapshot.timestamp, blocking=True)
    result = RoutingHealth.from_system(SystemHealth.aggregate([component]), HealthStatus.HEALTHY)
    assert result.blocks_new_entries
    with pytest.raises(ValueError):
        RoutingHealth.from_system(SystemHealth.aggregate([]), HealthStatus.HEALTHY)
    raw = event.model_dump(mode="json")
    raw["payload"] = {"kind": "MARKET_STATE_UPDATE", "state": "CHOP", "reasons": ["legacy"]}
    wire = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert serialize_event(deserialize_event(wire)) == wire
    assert event_hash(deserialize_event(wire)) == hashlib.sha256(wire.encode()).hexdigest()


def test_research_baseline_reproducibility_and_flicker(checker):
    observations = tuple(
        ResearchObservation(
            snapshot=state_snapshot(i, {"market.vwap_distance": 0 if i % 2 else 0.003}),
            health=healthy(state_snapshot(i)),
        )
        for i in range(8)
    )
    identity = ExperimentIdentity(
        hypothesis_id="router-v1-vs-vwap-slope",
        code_commit="fixture-commit",
        data_snapshot="state-fixtures-v1",
        search_family="fixed-rules",
        trials=1,
    )
    cfg = RouterSettings(min_duration_seconds=0)
    report = compare_baseline(observations, cfg, checker.calendar, checker.timezone, identity)
    assert report.baseline_transitions == 7
    assert report.router_transitions == 0
    assert report == compare_baseline(
        observations, cfg, checker.calendar, checker.timezone, identity
    )
    assert report.agreement_fraction == 0
    with pytest.raises(ValueError):
        compare_baseline((), cfg, checker.calendar, checker.timezone, identity)
    with pytest.raises(ValueError):
        compare_baseline(observations[::-1], cfg, checker.calendar, checker.timezone, identity)
    with pytest.raises(ValueError):
        compare_baseline(
            observations,
            cfg,
            checker.calendar,
            checker.timezone,
            identity.model_copy(update={"code_commit": "wrong"}),
        )
    assert (
        simple_vwap_slope(state_snapshot(changes={"market.slope_15m": None}), cfg)
        is MarketState.RISK_OFF
    )
    assert (
        simple_vwap_slope(
            state_snapshot(changes={"market.slope_15m": -1, "market.vwap_distance": -0.1}), cfg
        )
        is MarketState.TREND_DOWN
    )


def test_event_must_match_decision(checker):
    snapshot = state_snapshot()
    decision = router(checker).route(snapshot, healthy(snapshot), snapshot.timestamp)
    with pytest.raises(ValidationError):
        MarketStateUpdate(state="CHOP", reasons=(), decision=decision)


def test_high_vol_flicker_recovery_and_session_reset(checker):
    engine = router(checker, min_duration_seconds=0)
    for i in range(2):
        snapshot = state_snapshot(i)
        result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.TREND_UP
    snapshot = state_snapshot(2, {"market.vix_level": 30})
    result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.HIGH_VOL
    assert result.gross_risk_multiplier == 0.25
    assert AlphaFamily.PAIRS_STAT_ARB not in result.allowed_alpha_families
    assert engine.metrics.counters["market_state.flicker_transitions"] >= 1
    for i in (3, 4):
        snapshot = state_snapshot(i, {"market.vix_level": None})
        result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.TREND_UP
    snapshot = state_snapshot(start="2026-09-16T11:00:00+05:30")
    result = engine.route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.RISK_OFF and result.confirmation_count == 1


def test_health_staleness_independent_of_features(checker):
    snapshot = state_snapshot()
    engine = router(checker, min_duration_seconds=0, confirmation_count=1)
    stale = healthy(snapshot).model_copy(
        update={"observed_at": snapshot.timestamp - timedelta(seconds=31)}
    )
    assert engine.route(snapshot, stale, snapshot.timestamp).state is MarketState.RISK_OFF


def test_threshold_boundary_and_invalid_decision(checker):
    snapshot = state_snapshot(changes={"market.realized_volatility": 0.005})
    result = router(checker).route(snapshot, healthy(snapshot), snapshot.timestamp)
    assert result.state is MarketState.HIGH_VOL
    evidence = next(e for e in result.evidence.thresholds if e.name == "market.realized_volatility")
    assert evidence.value == evidence.threshold and evidence.passed
    for update in (
        {"state_since": result.timestamp + timedelta(seconds=1)},
        {"settings": RouterSettings(high_vix=28)},
        {"state": MarketState.RISK_OFF},
    ):
        with pytest.raises(ValidationError):
            StateDecision.model_validate(result.model_dump() | update)
