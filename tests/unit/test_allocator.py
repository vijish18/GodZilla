from datetime import timedelta
from decimal import Decimal

from tests.allocation_fixtures import allocation_inputs

from godzilla.config.modes import TradingMode
from godzilla.portfolio.allocator import allocate, totals, within_caps
from godzilla.portfolio.models import AllocatorSettings


def test_deterministic_caps_and_no_risk_approval(checker):
    inputs = allocation_inputs(checker)
    result = allocate(inputs, checker.calendar)
    assert result == allocate(inputs, checker.calendar)
    assert result.targets and within_caps(result.targets, inputs.settings)
    assert not result.execution_authorized and result.independent_risk_required
    assert result.allocations[0].allocated_gross == Decimal("0.08")
    assert result.allocations[2].penalty > result.allocations[0].penalty


def test_baskets_preserve_weights_and_net(checker):
    inputs = allocation_inputs(checker, basket=True)
    result = allocate(inputs, checker.calendar)
    assert totals(result.targets)[1] == 0
    for record in result.allocations:
        legs = tuple(p for p in result.targets if p.group_id == record.signal_id)
        assert len(legs) == 2
        assert legs[0].signed_notional_fraction == -legs[1].signed_notional_fraction
        assert sum(abs(p.signed_notional_fraction) for p in legs) == record.allocated_gross


def test_caps_shrink_whole_bundle_and_pending_positions_consume_capacity(checker):
    inputs = allocation_inputs(checker, basket=True)
    cfg = AllocatorSettings(
        max_gross=Decimal("0.1"),
        max_net=Decimal("0.1"),
        max_symbol=Decimal("0.02"),
        max_sector=Decimal("0.05"),
        max_open_risk=Decimal("0.0005"),
        max_candidate_risk=Decimal("0.0003"),
    )
    inputs = inputs.model_copy(update={"settings": cfg})
    result = allocate(inputs, checker.calendar)
    assert within_caps(result.targets, cfg)
    assert totals(result.targets)[2] <= Decimal("0.0005")
    existing = tuple(t.model_copy(update={"origin": "EXISTING_OR_PENDING"}) for t in result.targets)
    repeated = allocate(
        inputs.model_copy(
            update={"portfolio": inputs.portfolio.model_copy(update={"positions": existing})}
        ),
        checker.calendar,
    )
    assert repeated.targets == existing
    assert all(r.allocated_gross == 0 for r in repeated.allocations)


def test_missing_correlation_health_liquidity_and_risk_inputs_fail_closed(checker):
    inputs = allocation_inputs(checker)
    assert not allocate(inputs.model_copy(update={"health": ()}), checker.calendar).targets
    assert not allocate(inputs.model_copy(update={"estimates": ()}), checker.calendar).targets
    assert not allocate(inputs.model_copy(update={"liquidity": ()}), checker.calendar).targets
    partial = inputs.model_copy(
        update={"correlations": inputs.correlations.model_copy(update={"estimates": ()})}
    )
    assert len(allocate(partial, checker.calendar).targets) == 1
    future = inputs.correlations.model_copy(update={"training_end": inputs.at})
    assert not allocate(
        inputs.model_copy(update={"correlations": future}), checker.calendar
    ).targets


def test_health_risk_session_and_live_vetoes(checker):
    inputs = allocation_inputs(checker)
    for changes in (
        {"mode": TradingMode.LIVE},
        {"portfolio": inputs.portfolio.model_copy(update={"complete": False})},
        {"portfolio": inputs.portfolio.model_copy(update={"blocks_new_entries": True})},
        {"state": inputs.state.model_copy(update={"gross_risk_multiplier": 0})},
        {"at": inputs.at + timedelta(hours=4)},
    ):
        assert not allocate(inputs.model_copy(update=changes), checker.calendar).targets


def test_liquidity_capacity_and_no_score_based_budget_inflation(checker):
    inputs = allocation_inputs(checker)
    capacities = tuple(
        c.model_copy(update={"max_notional": Decimal(100)}) for c in inputs.liquidity
    )
    result = allocate(inputs.model_copy(update={"liquidity": capacities}), checker.calendar)
    assert all(abs(p.signed_notional_fraction) <= Decimal("0.0001") for p in result.targets)
    for alpha in {p.alpha_id for p in result.targets}:
        assert (
            sum(abs(p.signed_notional_fraction) for p in result.targets if p.alpha_id is alpha)
            <= inputs.registry.registration(alpha).base_budget
        )


def test_settings_layering_and_hard_policy_bounds(monkeypatch):
    import pytest

    from godzilla.config.loader import load_settings
    from godzilla.config.models import Settings

    initial = load_settings("config")
    monkeypatch.setenv("GODZILLA_ALLOCATOR__MAX_SYMBOL", "0.1")
    changed = load_settings("config")
    assert changed.allocator.max_symbol == Decimal("0.1")
    assert changed.config_hash() != initial.config_hash()
    payload = initial.model_dump()
    payload["allocator"]["max_open_risk"] = Decimal("0.1")
    with pytest.raises(ValueError, match="cannot exceed"):
        Settings.model_validate(payload)
