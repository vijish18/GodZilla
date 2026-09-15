import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.state_fixtures import healthy, state_snapshot

from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_state.models import MarketState, RouterSettings
from godzilla.market_state.router import MarketStateRouter

pytestmark = pytest.mark.property


@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    st.lists(st.floats(min_value=-0.02, max_value=0.02, allow_nan=False), min_size=1, max_size=12)
)
def test_future_prefix_replay_and_safety_invariant(checker, distances):
    observations = tuple(
        state_snapshot(i, {"market.vwap_distance": value}) for i, value in enumerate(distances)
    )

    def replay(items):
        router = MarketStateRouter(
            RouterSettings(), checker.calendar, checker.timezone, MemoryMetrics()
        )
        return tuple(router.route(item, healthy(item), item.timestamp) for item in items)

    complete = replay(observations)
    assert replay(observations[:1]) == complete[:1]
    assert replay(observations) == complete
    for decision in complete:
        if decision.candidate is MarketState.RISK_OFF:
            assert decision.state is MarketState.RISK_OFF
            assert not decision.allowed_alpha_families
            assert decision.gross_risk_multiplier == 0
