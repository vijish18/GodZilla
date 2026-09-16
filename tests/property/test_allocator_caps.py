from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.allocation_fixtures import allocation_inputs

from godzilla.portfolio.allocator import allocate, totals, within_caps
from godzilla.portfolio.models import AllocatorSettings


@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    gross=st.integers(1, 100),
    net=st.integers(0, 100),
    symbol=st.integers(1, 30),
    risk=st.integers(1, 100),
    basket=st.booleans(),
)
def test_conservation_and_every_cap(checker, gross, net, symbol, risk, basket):
    inputs = allocation_inputs(checker, basket=basket)
    cfg = AllocatorSettings(
        max_gross=Decimal(gross) / 100,
        max_net=Decimal(min(net, gross)) / 100,
        max_symbol=Decimal(symbol) / 100,
        max_open_risk=Decimal(risk) / 10000,
    )
    result = allocate(inputs.model_copy(update={"settings": cfg}), checker.calendar)
    assert within_caps(result.targets, cfg)
    assert totals(result.targets)[0] == sum(r.allocated_gross for r in result.allocations)
    for row in result.allocations:
        assert row.allocated_gross >= 0
    assert not result.execution_authorized
