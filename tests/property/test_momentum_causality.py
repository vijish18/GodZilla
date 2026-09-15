from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.momentum_fixtures import momentum_inputs

from godzilla.alphas.cross_sectional_momentum import CrossSectionalMomentum
from godzilla.alphas.momentum_models import MomentumSettings
from godzilla.alphas.ranking import percentiles

pytestmark = pytest.mark.property


@given(st.lists(st.integers(-1000, 1000), min_size=2, max_size=20))
def test_rank_monotonic_transform_and_ties(values):
    assert percentiles(tuple(values)) == percentiles(tuple(3 * v + 7 for v in values))


@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.integers(1, 100))
def test_future_append_and_rerun_cannot_change_intents(checker, minutes):
    obs, *rest = momentum_inputs(checker)
    engine = CrossSectionalMomentum(MomentumSettings())
    before = engine.evaluate(obs, *rest)
    future = tuple(
        o.model_copy(
            update={
                "snapshot": o.snapshot.model_copy(
                    update={"timestamp": o.snapshot.timestamp + timedelta(minutes=minutes)}
                )
            }
        )
        for o in obs
    )
    assert engine.evaluate((*obs, *future), *rest) == before
    assert engine.evaluate(obs, *rest) == before
