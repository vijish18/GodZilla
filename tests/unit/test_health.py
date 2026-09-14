from datetime import UTC, datetime

import pytest

from godzilla.core.health import ComponentHealth, HealthStatus, SystemHealth

pytestmark = pytest.mark.unit


def test_health_aggregation_uses_worst_status_and_blocking_flag() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    health = SystemHealth.aggregate(
        [
            ComponentHealth("clock", HealthStatus.HEALTHY, now),
            ComponentHealth("persistence", HealthStatus.UNHEALTHY, now, blocking=True),
            ComponentHealth("optional", HealthStatus.DEGRADED, now),
        ]
    )
    assert health.status is HealthStatus.UNHEALTHY
    assert health.blocks_new_entries is True


def test_empty_health_is_unknown() -> None:
    health = SystemHealth.aggregate([])
    assert health.status is HealthStatus.UNKNOWN
    assert health.blocks_new_entries is False


def test_health_details_are_immutable_and_time_must_be_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ComponentHealth("clock", HealthStatus.HEALTHY, datetime(2026, 9, 14))

    health = ComponentHealth(
        "clock", HealthStatus.HEALTHY, datetime(2026, 9, 14, tzinfo=UTC), details={"drift": 0}
    )
    with pytest.raises(TypeError):
        health.details["drift"] = 1  # type: ignore[index]
