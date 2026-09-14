from pathlib import Path

import pytest
from pydantic import ValidationError

from godzilla.config.loader import ConfigLoadError, load_settings
from godzilla.config.models import Settings, TradingMode

pytestmark = pytest.mark.unit
CONFIG_DIR = Path(__file__).parents[2] / "config"


def test_default_is_paper_with_layered_values() -> None:
    settings = load_settings(CONFIG_DIR)
    assert settings.mode is TradingMode.PAPER
    assert settings.market.exchange == "NSE"
    assert settings.logging.level == "INFO"
    assert settings.alphas.max_alpha_capital_fraction == 0.40


def test_explicit_mode_selects_research_layer() -> None:
    settings = load_settings(CONFIG_DIR, TradingMode.RESEARCH)
    assert settings.mode is TradingMode.RESEARCH
    assert settings.logging.level == "DEBUG"


def test_environment_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODZILLA_LOGGING__LEVEL", "WARNING")
    monkeypatch.setenv("GODZILLA_RISK__MAX_POSITIONS", "7")
    settings = load_settings(CONFIG_DIR)
    assert settings.logging.level == "WARNING"
    assert settings.risk.max_positions == 7


def test_environment_mode_selects_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODZILLA_MODE", "research")
    assert load_settings(CONFIG_DIR).logging.level == "DEBUG"


def test_time_ordering_is_validated() -> None:
    payload = load_settings(CONFIG_DIR).model_dump()
    payload["market"]["flatten_start_time"] = "14:59"
    with pytest.raises(ValidationError, match="session times"):
        Settings.model_validate(payload)


def test_config_hash_is_stable_and_excludes_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODZILLA_SECRETS__BROKER_API_KEY", "first-secret")
    first = load_settings(CONFIG_DIR)
    monkeypatch.setenv("GODZILLA_SECRETS__BROKER_API_KEY", "second-secret")
    second = load_settings(CONFIG_DIR)
    assert first.config_hash() == second.config_hash()
    assert "first-secret" not in str(first.model_dump())
    assert len(first.config_hash()) == 64


def test_live_is_blocked_without_production_and_compliance_prerequisites() -> None:
    with pytest.raises(ValidationError, match="LIVE activation blocked"):
        load_settings(CONFIG_DIR, TradingMode.LIVE)


def test_live_validates_only_with_all_affirmative_prerequisites() -> None:
    payload = load_settings(CONFIG_DIR).model_dump()
    payload["mode"] = "LIVE"
    payload["production"] = {
        "deployment_approved": True,
        "live_trading_enabled": True,
        "leader_election_configured": True,
        "release_id": "release-verified",
    }
    payload["compliance"].update(
        {
            "broker_route": "CLIENT_DIRECT_API",
            "static_ip_required": True,
            "static_ip_registered": True,
            "live_allowed": True,
        }
    )
    payload["compliance"]["api_tagging"] = {
        "required": True,
        "scheme": "verified-test-scheme",
        "verified": True,
    }
    payload["secrets"] = {
        "broker_api_key": "test-only-key",
        "broker_access_token": "test-only-token",
    }
    settings = Settings.model_validate(payload)
    assert settings.mode is TradingMode.LIVE
    assert settings.production.live_trading_enabled is True


def test_invalid_environment_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GODZILLA_MODE", "danger")
    with pytest.raises(ConfigLoadError, match="invalid GODZILLA_MODE"):
        load_settings(CONFIG_DIR)


def test_missing_config_layer_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError, match="unable to load"):
        load_settings(tmp_path)
