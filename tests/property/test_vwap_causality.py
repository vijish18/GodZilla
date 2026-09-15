from datetime import timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.vwap_fixtures import vwap_inputs

from godzilla.alphas.vwap_mean_reversion import VwapMeanReversion
from godzilla.alphas.vwap_models import VwapSettings


# Calendar fixture is read-only; each example constructs fresh inputs and engine.
@settings(max_examples=12, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(minutes=st.integers(min_value=1, max_value=500))
def test_future_append_and_order_do_not_change_prior_intent(checker, minutes):
    args = list(vwap_inputs(checker))
    engine = VwapMeanReversion(VwapSettings(), checker.calendar)
    expected = engine.evaluate(*args)
    current = args[0][-1]
    future = current.model_copy(
        update={
            "start": current.end + timedelta(minutes=minutes),
            "end": current.end + timedelta(minutes=minutes + 5),
            "received_at": current.end + timedelta(minutes=minutes + 5),
        }
    )
    args[0] = tuple(reversed(args[0] + (future,)))
    assert engine.evaluate(*args) == expected
