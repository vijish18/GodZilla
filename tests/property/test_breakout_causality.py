from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.breakout_fixtures import breakout_inputs

from godzilla.alphas.breakout_models import BreakoutSettings
from godzilla.alphas.momentum_breakout import MomentumBreakout

pytestmark = pytest.mark.property


@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers(1, 100))
def test_future_events_and_permutation_cannot_change_candidates(checker, minutes):
    observations, *rest = breakout_inputs(checker)
    engine = MomentumBreakout(BreakoutSettings(), checker.calendar)
    expected = engine.evaluate(observations, *rest)
    future = tuple(
        o.model_copy(
            update={
                "current": o.current.model_copy(
                    update={
                        "snapshot": o.current.snapshot.model_copy(
                            update={
                                "timestamp": o.current.snapshot.timestamp
                                + timedelta(minutes=minutes)
                            }
                        )
                    }
                )
            }
        )
        for o in observations
    )
    assert engine.evaluate((*observations[::-1], *future), *rest) == expected
