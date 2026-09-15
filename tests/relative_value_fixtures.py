from datetime import timedelta
from decimal import Decimal

from godzilla.alphas.relative_value_models import (
    BetaObservation,
    RelativeValueInventory,
    RelativeValueObservation,
    RelativeValueSettings,
)
from godzilla.alphas.sector_relative_value import SectorRelativeValue
from godzilla.config.modes import TradingMode
from godzilla.market_data.models import Bar, FeedKind
from godzilla.market_state.models import AlphaFamily
from tests.momentum_fixtures import changed, momentum_inputs


def relative_value_inputs(checker):
    observations, universe, master, state, at = momentum_inputs(checker)
    state = state.model_copy(
        update={"allowed_alpha_families": (AlphaFamily.SECTOR_RELATIVE_VALUE,)}
    )
    values = []
    # Fixed synthetic return paths with exactly known OLS slopes; no fitting/tuning seeds.
    for number, observation in enumerate(observations):
        beta = Decimal(1) + Decimal(number) / 100
        stock, market = Decimal(100), Decimal(1000)
        rows = []
        for index in range(26):
            change = Decimal((index % 5) - 2) / 1000 + Decimal("0.0003")
            stock *= 1 + beta * change
            market *= 1 + change
            start = at - timedelta(minutes=5 * (26 - index))

            def bar(token, price, kind, start=start):
                return Bar(
                    instrument_id=token,
                    source="fixture",
                    start=start,
                    end=start + timedelta(minutes=5),
                    received_at=start + timedelta(minutes=5),
                    interval_minutes=5,
                    feed_kind=kind,
                    open=price,
                    close=price,
                    high=price + 1,
                    low=price - 1,
                    volume=Decimal(1000),
                )

            rows.append(
                BetaObservation(
                    stock=bar(f"T{number:02}", stock, FeedKind.EQUITY),
                    market=bar("NIFTY", market, FeedKind.INDEX),
                )
            )
        values.append(
            RelativeValueObservation(
                current=changed(
                    observation,
                    {
                        "stock.relative_sector_30m": (number - 4.5) * 0.01,
                        "stock.relative_market_30m": (number - 4.5) * 0.02,
                    },
                ),
                history=tuple(rows),
            )
        )
    inventory = RelativeValueInventory(
        snapshot_id="empty-confirmed-v1", observed_at=at, complete=True
    )
    return tuple(values), universe, master, state, inventory, at, TradingMode.RESEARCH


def relative_value_result(checker, **settings):
    return SectorRelativeValue(RelativeValueSettings(**settings), checker.calendar).evaluate(
        *relative_value_inputs(checker)
    )
