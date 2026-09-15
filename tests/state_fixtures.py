"""Synthetic feature-level scenarios; no market data or fitted thresholds."""

from datetime import datetime, timedelta

from godzilla.core.health import HealthStatus
from godzilla.features.models import FeatureSnapshot, FeatureValue, MissingReason
from godzilla.market_state.models import RoutingHealth

BASE = {
    "market.vwap_distance": 0.003,
    "market.slope_15m": 0.001,
    "market.slope_30m": 0.0005,
    "market.breadth_advancing": 0.7,
    "market.breadth_declining": 0.3,
    "market.breadth_coverage": 1,
    "market.realized_volatility": 0.001,
    "market.range_volatility": 0.002,
    "market.vix_level": 18,
    "market.opening_gap": 0.001,
}


def state_snapshot(step=0, changes=None, start="2026-09-15T11:00:00+05:30"):
    values = BASE | (changes or {})
    return FeatureSnapshot(
        entity="market-context",
        timestamp=datetime.fromisoformat(start) + timedelta(minutes=5 * step),
        feature_set_hash="1" * 64,
        context_hash="2" * 64,
        input_hash="3" * 64,
        code_commit="fixture-commit",
        config_hash="4" * 64,
        data_snapshot="state-fixtures-v1",
        values=tuple(
            FeatureValue(
                name=name, value=value, reason=MissingReason.WARMUP if value is None else None
            )
            for name, value in sorted(values.items())
        ),
    )


def healthy(snapshot):
    return RoutingHealth(
        observed_at=snapshot.timestamp,
        system_status=HealthStatus.HEALTHY,
        risk_status=HealthStatus.HEALTHY,
        blocks_new_entries=False,
    )
