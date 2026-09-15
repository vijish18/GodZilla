from datetime import datetime, timedelta
from decimal import Decimal

from godzilla.alphas.vwap_mean_reversion import VwapMeanReversion
from godzilla.alphas.vwap_models import VwapLiquidity, VwapSettings
from godzilla.config.modes import TradingMode
from godzilla.market_data.models import Bar, Quote
from godzilla.market_state.models import AlphaFamily, MarketState
from tests.momentum_fixtures import momentum_inputs


def vwap_inputs(checker, short=False):
    _, universe, master, state, _ = momentum_inputs(checker)
    opening = datetime.fromisoformat("2026-09-15T09:15:00+05:30")
    closes = [Decimal("100")] * 12 + [Decimal("98"), Decimal("97"), Decimal("97.4")]
    bars = []
    for index, close in enumerate(closes):
        op = Decimal("97") if index == 14 else close
        high, low = close + Decimal("0.1"), min(op, close) - Decimal("0.1")
        if short:
            op, close, high, low = 200 - op, 200 - close, 200 - low, 200 - high
        start = opening + timedelta(minutes=5 * index)
        bars.append(
            Bar(
                instrument_id="T00",
                source="fixture",
                start=start,
                end=start + timedelta(minutes=5),
                received_at=start + timedelta(minutes=5),
                interval_minutes=5,
                open=op,
                close=close,
                high=high,
                low=low,
                volume=Decimal(1000),
            )
        )
    at = bars[-1].end
    state = state.model_copy(
        update={
            "timestamp": at,
            "state_since": at,
            "state": MarketState.CHOP,
            "allowed_alpha_families": (AlphaFamily.VWAP_MEAN_REVERSION,),
            "evidence": state.evidence.model_copy(
                update={
                    "health": state.evidence.health.model_copy(update={"observed_at": at}),
                }
            ),
        }
    )
    liquidity = VwapLiquidity(
        quote=Quote(
            instrument_id="T00",
            source="fixture",
            timestamp=at,
            received_at=at,
            bid=bars[-1].close - Decimal("0.01"),
            ask=bars[-1].close + Decimal("0.01"),
            last=bars[-1].close,
        ),
        adv=200000,
        daily_turnover=20000000,
        known_at=at,
        snapshot_id="synthetic-liquidity-v1",
    )
    return tuple(bars), liquidity, universe, master, state, at, TradingMode.RESEARCH


def vwap_result(checker, short=False, **settings):
    return VwapMeanReversion(VwapSettings(**settings), checker.calendar).evaluate(
        *vwap_inputs(checker, short)
    )
