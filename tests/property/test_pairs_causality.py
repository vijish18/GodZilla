from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.pairs_fixtures import pairs_inputs

from godzilla.alphas.pairs_engine import PairsEngine
from godzilla.alphas.pairs_models import PairsSettings
from godzilla.alphas.pairs_statistics import StatsmodelsDiagnostics

pytestmark = pytest.mark.property


@settings(
    max_examples=5, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.integers(1, 500))
def test_future_and_input_permutation_do_not_change_prior_evaluation(checker, minutes):
    args = list(pairs_inputs(checker))
    engine = PairsEngine(PairsSettings(), StatsmodelsDiagnostics(), checker.calendar)
    before = engine.evaluate(*args)
    last = args[1][-1]
    future = last.model_copy(
        update={
            "left": last.left.model_copy(
                update={
                    "start": last.left.start + timedelta(minutes=minutes),
                    "end": last.left.end + timedelta(minutes=minutes),
                    "received_at": last.left.received_at + timedelta(minutes=minutes),
                }
            )
        }
    )
    args[1] = (*args[1][::-1], future)
    assert engine.evaluate(*args) == before
