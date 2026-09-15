from datetime import timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.relative_value_fixtures import relative_value_inputs

from godzilla.alphas.relative_value_models import RelativeValueSettings
from godzilla.alphas.sector_relative_value import SectorRelativeValue


@settings(max_examples=10, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(minutes=st.integers(min_value=5, max_value=1000))
def test_future_history_and_cross_section_do_not_change_decision(checker, minutes):
    args = list(relative_value_inputs(checker))
    engine = SectorRelativeValue(RelativeValueSettings(), checker.calendar)
    expected = engine.evaluate(*args)
    changed = []
    for item in args[0]:
        last = item.history[-1]
        later = last.model_copy(
            update={
                "stock": last.stock.model_copy(
                    update={
                        "start": last.stock.start + timedelta(minutes=minutes),
                        "end": last.stock.end + timedelta(minutes=minutes),
                        "received_at": last.stock.received_at + timedelta(minutes=minutes),
                    }
                ),
                "market": last.market.model_copy(
                    update={"end": last.market.end + timedelta(minutes=minutes)}
                ),
            }
        )
        changed.append(item.model_copy(update={"history": tuple(reversed((*item.history, later)))}))
    future = args[0][0].model_copy(
        update={
            "current": args[0][0].current.model_copy(
                update={
                    "snapshot": args[0][0].current.snapshot.model_copy(
                        update={"timestamp": args[5] + timedelta(minutes=minutes)}
                    )
                }
            )
        }
    )
    args[0] = (*reversed(changed), future)
    assert engine.evaluate(*args) == expected
