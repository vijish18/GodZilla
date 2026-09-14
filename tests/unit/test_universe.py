from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from godzilla.config.loader import load_settings
from godzilla.market_data.instruments import LocalInstrumentProvider
from godzilla.universe.builder import ExclusionReason, UniverseBuilder

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def test_universe_is_deterministic_point_in_time_and_fail_closed() -> None:
    settings = load_settings(ROOT / "config")
    master = LocalInstrumentProvider(
        ROOT / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()
    arguments = {
        "master": master,
        "compliance": settings.compliance,
        "settings": settings.universe,
        "as_of": date(2026, 9, 15),
        "generated_at": datetime(2026, 9, 15, tzinfo=UTC),
    }
    first = UniverseBuilder().build(**arguments)
    second = UniverseBuilder().build(**arguments)
    assert first.snapshot_id == second.snapshot_id
    assert first.long_tokens == ("100002", "100001")
    assert first.short_tokens == ("100001",)
    reasons = {item.symbol: item.reasons for item in first.exclusions}
    assert reasons["CASHONLY"] == (ExclusionReason.SHORT_NOT_FO_ELIGIBLE,)
    assert reasons["SUSPENDED"] == (ExclusionReason.SUSPENDED,)
    assert reasons["WATCH"] == (ExclusionReason.SURVEILLANCE,)
    assert reasons["AMBIGUOUS"] == (ExclusionReason.DATA_AMBIGUITY,)
    assert reasons["FUTURE"] == (ExclusionReason.NOT_EFFECTIVE,)
    assert reasons["UNTRADABLE"] == (ExclusionReason.UNTRADABLE,)


def test_compliance_permission_is_required_for_shorts() -> None:
    settings = load_settings(ROOT / "config")
    master = LocalInstrumentProvider(
        ROOT / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()
    rule = settings.compliance.short_eligibility.model_copy(
        update={"broker_permission_verified": False}
    )
    compliance = settings.compliance.model_copy(update={"short_eligibility": rule})
    snapshot = UniverseBuilder().build(
        master=master,
        compliance=compliance,
        settings=settings.universe,
        as_of=date(2026, 9, 15),
        generated_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    assert snapshot.short_tokens == ()


def test_daily_snapshot_rejects_master_from_another_date() -> None:
    settings = load_settings(ROOT / "config")
    master = LocalInstrumentProvider(
        ROOT / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()
    with pytest.raises(ValueError, match="must match"):
        UniverseBuilder().build(
            master=master,
            compliance=settings.compliance,
            settings=settings.universe,
            as_of=date(2026, 9, 16),
            generated_at=datetime(2026, 9, 16, tzinfo=UTC),
        )


def test_configured_symbol_exclusion_carries_reason_code() -> None:
    settings = load_settings(ROOT / "config")
    master = LocalInstrumentProvider(
        ROOT / "data/instruments/nse-cm-synthetic-2026-09-15.yaml"
    ).load()
    universe_settings = settings.universe.model_copy(
        update={"excluded_symbols": frozenset({"LIQUIDFO"})}
    )
    snapshot = UniverseBuilder().build(
        master=master,
        compliance=settings.compliance,
        settings=universe_settings,
        as_of=date(2026, 9, 15),
        generated_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    exclusion = next(item for item in snapshot.exclusions if item.symbol == "LIQUIDFO")
    assert exclusion.reasons == (ExclusionReason.CONFIG_SYMBOL,)
