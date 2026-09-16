from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from godzilla.ensemble.health import measure_health
from godzilla.ensemble.models import (
    AlphaHealthMultiplier,
    AlphaHealthSettings,
    AlphaId,
    TradeOutcome,
)
from godzilla.market_state.models import MarketState

AT = datetime(2026, 9, 15, 7, tzinfo=UTC)


def outcomes(n=20, pnl=100, at=AT):
    return tuple(
        TradeOutcome(
            trade_id=UUID(int=i + 1),
            alpha_id=AlphaId.MOMENTUM,
            closed_at=at - timedelta(minutes=n - i),
            received_at=at - timedelta(minutes=n - i),
            gross_pnl=Decimal(pnl),
            costs=Decimal(10),
            slippage_cost=Decimal(5),
            gross_capital=Decimal(10000),
            regime=MarketState.CHOP,
        )
        for i in range(n)
    )


def measured(trades, at=AT, previous=None, **settings):
    return measure_health(
        AlphaId.MOMENTUM,
        trades,
        at,
        AlphaHealthSettings(**settings),
        code_commit="test",
        config_hash="c" * 64,
        data_snapshot="trades",
        previous=previous,
    )


def test_hand_computed_metrics_and_deterioration():
    good = measured(outcomes(2), minimum_trades=2)
    assert good.trades == 2 and good.net_pnl == 170
    assert good.net_expectancy == pytest.approx(0.0085)
    assert good.costs == 20 and good.slippage_cost == 10
    assert good.drawdown == 0
    bad = measured(outcomes(2, -100), minimum_trades=2)
    assert bad.net_pnl == -230 and bad.drawdown == pytest.approx(0.023)
    assert bad.multiplier.value == 0
    assert sum(r.trades for r in good.regimes) == 2


def test_increase_is_slow_requires_new_trades_and_cannot_restart_at_maximum():
    first = measured(outcomes())
    assert first.multiplier.value == 0.5
    soon = measured(outcomes(at=AT + timedelta(minutes=1)), AT + timedelta(minutes=1), first)
    assert soon.multiplier.value == 0.5
    later = AT + timedelta(days=1)
    advanced = measured(outcomes(at=later), later, soon)
    assert advanced.multiplier.value == 0.55
    unchanged = measured(outcomes(at=later), later + timedelta(days=1), advanced)
    assert unchanged.multiplier.value == 0.55
    with pytest.raises(ValueError):
        AlphaHealthMultiplier(value=2, minimum=0, maximum=1)


def test_future_outcomes_duplicates_and_rolling_window():
    trades = outcomes(25)
    base = measured(trades, window_trades=20)
    assert base.trades == 20
    future = outcomes(1, at=AT + timedelta(days=1))[0]
    assert measured((*trades, future), window_trades=20) == base
    assert measured((*trades, trades[-1]), window_trades=20) == base
    with pytest.raises(ValueError, match="conflicting"):
        measured((*trades, trades[-1].model_copy(update={"gross_pnl": Decimal(999)})))
    with pytest.raises(ValueError, match="predecessor"):
        measured(trades, previous=base)
