"""Pydantic models and cross-field safety validation."""

from __future__ import annotations

import hashlib
import json
from datetime import time
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from godzilla.compliance.models import ComplianceProfile
from godzilla.config.modes import SystemState, TradingMode

__all__ = ["Settings", "SystemState", "TradingMode"]


class AlphaAvailability(StrEnum):
    ENABLED = "ENABLED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    DISABLED = "DISABLED"


class MarketSettings(BaseModel):
    exchange: str
    timezone: str
    raw_bar: str
    decision_bar: str
    stabilization_minutes: int = Field(ge=0)
    final_entry_time: time
    flatten_start_time: time
    hard_flatten_deadline: time

    @field_validator("timezone")
    @classmethod
    def timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def validate_session_order(self) -> MarketSettings:
        if not (self.final_entry_time < self.flatten_start_time < self.hard_flatten_deadline):
            raise ValueError(
                "session times must satisfy final_entry_time < flatten_start_time "
                "< hard_flatten_deadline"
            )
        return self


class UniverseSettings(BaseModel):
    require_fo_eligibility_for_short: bool
    liquidity_percentile_min: float = Field(ge=0, le=1)
    max_spread_bps: int = Field(gt=0)
    max_adv_participation: float = Field(gt=0, le=1)
    excluded_symbols: frozenset[str] = frozenset()
    excluded_sectors: frozenset[str] = frozenset()
    exclude_surveillance: bool = True
    exclude_data_ambiguity: bool = True


class AlphaSettings(BaseModel):
    cross_sectional_momentum: AlphaAvailability
    momentum_breakout: AlphaAvailability
    pairs_stat_arb: AlphaAvailability
    vwap_mean_reversion: AlphaAvailability
    sector_relative_value: AlphaAvailability
    max_alpha_capital_fraction: float = Field(gt=0, le=1)


class RiskSettings(BaseModel):
    directional_risk_per_trade: float = Field(gt=0, le=1)
    pair_risk_per_trade: float = Field(gt=0, le=1)
    max_open_portfolio_risk: float = Field(gt=0, le=1)
    max_positions: int = Field(gt=0)
    max_positions_per_sector: int = Field(gt=0)
    max_gross_notional: float = Field(gt=0)
    max_net_directional: float = Field(ge=0)
    daily_soft_loss: float = Field(gt=0, le=1)
    daily_hard_loss: float = Field(gt=0, le=1)
    weekly_hard_loss: float = Field(gt=0, le=1)
    max_peak_drawdown: float = Field(gt=0, le=1)
    leverage_enabled: bool

    @model_validator(mode="after")
    def validate_risk_ordering(self) -> RiskSettings:
        if self.daily_soft_loss >= self.daily_hard_loss:
            raise ValueError("daily_soft_loss must be lower than daily_hard_loss")
        if self.max_net_directional > self.max_gross_notional:
            raise ValueError("max_net_directional cannot exceed max_gross_notional")
        if self.max_positions_per_sector > self.max_positions:
            raise ValueError("max_positions_per_sector cannot exceed max_positions")
        return self


class ExecutionSettings(BaseModel):
    market_orders_assumed_allowed: bool
    max_order_retries: int = Field(ge=0)
    max_slippage_bps: int = Field(gt=0)
    quote_stale_seconds: int = Field(gt=0)
    reconcile_on_unknown: bool
    single_active_executor: bool


class LoggingSettings(BaseModel):
    level: str = "INFO"
    local_path: Path | None = None

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level: {value}")
        return normalized


class ProductionSettings(BaseModel):
    deployment_approved: bool = False
    live_trading_enabled: bool = False
    leader_election_configured: bool = False
    release_id: str | None = None


class SecretSettings(BaseModel):
    broker_api_key: SecretStr | None = Field(default=None, exclude=True)
    broker_access_token: SecretStr | None = Field(default=None, exclude=True)
    database_password: SecretStr | None = Field(default=None, exclude=True)
    dashboard_secret: SecretStr | None = Field(default=None, exclude=True)

    def missing_live_requirements(self) -> list[str]:
        missing: list[str] = []
        for name in ("broker_api_key", "broker_access_token"):
            value = getattr(self, name)
            if value is None or not value.get_secret_value().strip():
                missing.append(f"secrets.{name}")
        return missing


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GODZILLA_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    mode: TradingMode = TradingMode.PAPER
    starting_values_notice: str
    market: MarketSettings
    universe: UniverseSettings
    alphas: AlphaSettings
    risk: RiskSettings
    execution: ExecutionSettings
    logging: LoggingSettings = LoggingSettings()
    production: ProductionSettings = ProductionSettings()
    compliance: ComplianceProfile
    secrets: SecretSettings = SecretSettings()

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del settings_cls, dotenv_settings, file_secret_settings
        return env_settings, init_settings

    @model_validator(mode="after")
    def validate_live_prerequisites(self) -> Settings:
        if self.mode is not TradingMode.LIVE:
            return self

        missing = list(self.compliance.structural_issues(self.mode))
        missing.extend(self.secrets.missing_live_requirements())
        for name in ("deployment_approved", "live_trading_enabled", "leader_election_configured"):
            if not getattr(self.production, name):
                missing.append(f"production.{name}")
        if not self.production.release_id:
            missing.append("production.release_id")
        if not self.execution.single_active_executor:
            missing.append("execution.single_active_executor")
        if self.execution.market_orders_assumed_allowed:
            missing.append("execution.market_orders_assumed_allowed must be false")
        if not self.execution.reconcile_on_unknown:
            missing.append("execution.reconcile_on_unknown must be true")
        if self.risk.leverage_enabled:
            missing.append("risk.leverage_enabled must be false for the V1 live pilot")
        if missing:
            raise ValueError(
                "LIVE activation blocked; missing prerequisites: " + ", ".join(missing)
            )
        return self

    def config_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"secrets"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
