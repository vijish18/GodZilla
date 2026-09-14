from datetime import UTC, datetime
from pathlib import Path

import pytest

from godzilla.compliance.service import ComplianceGate, ComplianceViolation
from godzilla.config.loader import load_settings
from godzilla.config.modes import TradingMode
from godzilla.core.clock import FixedClock

pytestmark = pytest.mark.unit
CONFIG = Path(__file__).parents[2] / "config"


def test_paper_profile_is_current_at_review_date() -> None:
    profile = load_settings(CONFIG).compliance
    ComplianceGate(FixedClock(datetime(2026, 9, 15, tzinfo=UTC))).validate(
        profile, TradingMode.PAPER
    )


def test_expired_profile_blocks_paper_startup() -> None:
    profile = load_settings(CONFIG).compliance
    gate = ComplianceGate(FixedClock(datetime(2026, 10, 16, tzinfo=UTC)))
    with pytest.raises(ComplianceViolation, match="expired"):
        gate.validate(profile, TradingMode.PAPER)


def test_local_paper_profile_cannot_qualify_live() -> None:
    profile = load_settings(CONFIG).compliance
    issues = profile.structural_issues(TradingMode.LIVE)
    assert "compliance.live_allowed" in issues
    assert "compliance.broker_route" in issues
