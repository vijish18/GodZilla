from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from godzilla.market_data.instruments import (
    LocalBrokerSymbolMapper,
    LocalInstrumentProvider,
    TickRounding,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def test_tick_rounding_is_decimal_and_directional() -> None:
    instrument = (
        LocalInstrumentProvider(ROOT / "data/instruments/nse-cm-synthetic-2026-09-15.yaml")
        .load()
        .instruments[0]
    )
    assert instrument.round_price(Decimal("100.023")) == Decimal("100.00")
    assert instrument.round_price(Decimal("100.025")) == Decimal("100.05")
    assert instrument.round_price(Decimal("100.021"), TickRounding.CEILING) == Decimal("100.05")


def test_broker_mapping_is_point_in_time() -> None:
    mapper = LocalBrokerSymbolMapper.from_yaml(
        ROOT / "data/instruments/broker-symbols-synthetic.yaml"
    )
    assert mapper.resolve("LOCAL_PAPER_SIMULATOR", "100001", date(2026, 9, 15)) == "NSE:LIQUIDFO"
    assert mapper.resolve("LOCAL_PAPER_SIMULATOR", "100001", date(2027, 1, 1)) is None
