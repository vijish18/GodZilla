"""Typed, versioned India/NSE operational compliance metadata."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from godzilla.config.modes import TradingMode


class BrokerRoute(StrEnum):
    PAPER_SIMULATOR = "PAPER_SIMULATOR"
    CLIENT_DIRECT_API = "CLIENT_DIRECT_API"
    MEMBER_FRONTEND = "MEMBER_FRONTEND"
    BROKER_HOSTED_PROVIDER = "BROKER_HOSTED_PROVIDER"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"


class SourceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    authority: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ApiTaggingRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    required: bool
    scheme: str | None = None
    verified: bool


class RateLimitRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    exchange_threshold_ops: int = Field(gt=0)
    configured_max_ops: int = Field(gt=0)
    verified: bool

    @model_validator(mode="after")
    def configured_limit_cannot_exceed_exchange_threshold(self) -> RateLimitRule:
        if self.configured_max_ops > self.exchange_threshold_ops:
            raise ValueError("configured_max_ops exceeds exchange_threshold_ops")
        return self


class ShortEligibilityRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    intraday_short_allowed: bool
    require_fo_eligibility: bool = True
    broker_permission_verified: bool
    overnight_short_allowed: bool = False

    @model_validator(mode="after")
    def v1_cannot_allow_overnight_short(self) -> ShortEligibilityRule:
        if self.overnight_short_allowed:
            raise ValueError("V1 cannot allow overnight shorts")
        return self


class ComplianceProfile(BaseModel):
    """A point-in-time operational profile; absence or ambiguity blocks entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    profile_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    jurisdiction: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    broker_name: str = Field(min_length=1)
    broker_route: BrokerRoute
    static_ip_required: bool
    static_ip_registered: bool
    api_tagging: ApiTaggingRule
    rate_limit: RateLimitRule
    allowed_order_types: frozenset[OrderType] = Field(min_length=1)
    short_eligibility: ShortEligibilityRule
    api_daily_session_reset_required: bool
    api_daily_session_reset_verified: bool
    effective_from: date
    effective_to: date
    source_references: tuple[SourceReference, ...] = Field(min_length=1)
    reviewed_at: datetime
    expires_at: datetime
    paper_allowed: bool
    live_allowed: bool

    @field_validator("reviewed_at", "expires_at")
    @classmethod
    def timestamps_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("compliance timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_ordering(self) -> ComplianceProfile:
        if self.effective_from > self.effective_to:
            raise ValueError("effective_from must not exceed effective_to")
        if self.reviewed_at >= self.expires_at:
            raise ValueError("reviewed_at must precede expires_at")
        return self

    def structural_issues(self, mode: TradingMode) -> tuple[str, ...]:
        issues: list[str] = []
        if mode is TradingMode.PAPER and not self.paper_allowed:
            issues.append("compliance.paper_allowed")
        if mode is TradingMode.LIVE:
            if not self.live_allowed:
                issues.append("compliance.live_allowed")
            if self.broker_route is BrokerRoute.PAPER_SIMULATOR:
                issues.append("compliance.broker_route")
            if self.static_ip_required and not self.static_ip_registered:
                issues.append("compliance.static_ip_registered")
            if self.api_tagging.required and not self.api_tagging.verified:
                issues.append("compliance.api_tagging.verified")
            if not self.rate_limit.verified:
                issues.append("compliance.rate_limit.verified")
            if not self.short_eligibility.broker_permission_verified:
                issues.append("compliance.short_eligibility.broker_permission_verified")
            if self.api_daily_session_reset_required and not self.api_daily_session_reset_verified:
                issues.append("compliance.api_daily_session_reset_verified")
        return tuple(issues)

    def temporal_issues(self, at: datetime) -> tuple[str, ...]:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("compliance evaluation time must be timezone-aware")
        issues: list[str] = []
        if not self.effective_from <= at.date() <= self.effective_to:
            issues.append("compliance profile outside effective date range")
        if at > self.expires_at:
            issues.append("compliance profile expired")
        return tuple(issues)
