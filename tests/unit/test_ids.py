import pytest

from godzilla.core.ids import CorrelationId, EventId, SignalId, TradeId

pytestmark = pytest.mark.unit


def test_internal_ids_are_unique_uuid_values() -> None:
    generated = {str(SignalId.new()) for _ in range(100)}
    assert len(generated) == 100


def test_id_types_remain_separate_and_round_trip() -> None:
    signal_id = SignalId.new()
    parsed = SignalId.parse(str(signal_id))
    assert parsed == signal_id
    assert signal_id != TradeId(signal_id.value)
    assert len({str(EventId.new()), str(CorrelationId.new())}) == 2
