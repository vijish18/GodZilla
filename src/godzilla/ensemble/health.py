"""Deterministic causal rolling outcomes, bounded increases and immediate deterioration cuts."""

from datetime import datetime
from decimal import Decimal
from statistics import fmean
from uuid import UUID

from godzilla.ensemble.models import (
    AlphaHealthMultiplier,
    AlphaHealthSettings,
    AlphaHealthSnapshot,
    AlphaId,
    RegimeCount,
    TradeOutcome,
)
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.models import utc
from godzilla.market_state.models import MarketState


class _Outcomes(FeatureModel):
    trades: tuple[TradeOutcome, ...]


def measure_health(
    alpha: AlphaId,
    outcomes: tuple[TradeOutcome, ...],
    at: datetime,
    settings: AlphaHealthSettings,
    *,
    code_commit: str,
    config_hash: str,
    data_snapshot: str,
    previous: AlphaHealthSnapshot | None = None,
) -> AlphaHealthSnapshot:
    at = utc(at)
    unique: dict[UUID, TradeOutcome] = {}
    for trade in outcomes:
        if trade.alpha_id is not alpha or trade.received_at > at or trade.closed_at > at:
            continue
        if trade.trade_id in unique and unique[trade.trade_id] != trade:
            raise ValueError("conflicting trade outcome")
        unique[trade.trade_id] = trade
    trades = tuple(sorted(unique.values(), key=lambda x: (x.closed_at, str(x.trade_id))))[
        -settings.window_trades :
    ]
    if previous and (
        previous.alpha_id is not alpha or previous.settings != settings or previous.timestamp >= at
    ):
        raise ValueError("invalid health predecessor")
    pnl = tuple(t.gross_pnl - t.costs - t.slippage_cost for t in trades)
    returns = tuple(float(p / t.gross_capital) for p, t in zip(pnl, trades, strict=True))
    curve = peak = drawdown = 0.0
    for value in returns:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    expectancy = fmean(returns) if returns else None
    last_trade = trades[-1].closed_at if trades else None
    if (
        previous
        and previous.last_trade_at is not None
        and (last_trade is None or last_trade < previous.last_trade_at)
    ):
        raise ValueError("health history regressed or disappeared")
    multiplier = previous.multiplier.value if previous else settings.initial_multiplier
    if len(trades) < settings.minimum_trades:
        multiplier = min(multiplier, settings.initial_multiplier)
    last_increase = previous.last_increase_at if previous else at
    if expectancy is not None and (expectancy <= 0 or drawdown >= settings.max_return_drawdown):
        multiplier = settings.minimum_multiplier
    elif (
        previous
        and len(trades) >= settings.minimum_trades
        and last_trade is not None
        and (previous.last_trade_at is None or last_trade > previous.last_trade_at)
        and (at - last_increase).total_seconds() >= settings.increase_interval_seconds
    ):
        multiplier = min(settings.maximum_multiplier, multiplier + settings.max_increase)
        last_increase = at
    return AlphaHealthSnapshot(
        alpha_id=alpha,
        timestamp=at,
        trades=len(trades),
        net_expectancy=expectancy,
        net_pnl=sum(pnl, Decimal(0)),
        drawdown=drawdown,
        costs=sum((t.costs for t in trades), Decimal(0)),
        slippage_cost=sum((t.slippage_cost for t in trades), Decimal(0)),
        regimes=tuple(
            RegimeCount(regime=r, trades=sum(t.regime is r for t in trades)) for r in MarketState
        ),
        multiplier=AlphaHealthMultiplier(
            value=multiplier,
            minimum=settings.minimum_multiplier,
            maximum=settings.maximum_multiplier,
        ),
        last_increase_at=last_increase,
        last_trade_at=last_trade,
        settings=settings,
        outcomes_hash=content_hash(_Outcomes(trades=trades)),
        prior_hash=previous.snapshot_hash() if previous else None,
        code_commit=code_commit,
        config_hash=config_hash,
        data_snapshot=data_snapshot,
    )
