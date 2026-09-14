"""Deterministic, point-in-time long and short universe snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict

from godzilla.compliance.models import ComplianceProfile
from godzilla.config.models import UniverseSettings
from godzilla.market_data.instruments import Instrument, InstrumentMasterSnapshot, InstrumentStatus

_UNIVERSE_NAMESPACE = UUID("7bbd3d57-646f-4cb0-a642-021cd619aa16")


class ExclusionReason(StrEnum):
    NOT_EFFECTIVE = "NOT_EFFECTIVE"
    SUSPENDED = "SUSPENDED"
    UNTRADABLE = "UNTRADABLE"
    SURVEILLANCE = "SURVEILLANCE"
    DATA_AMBIGUITY = "DATA_AMBIGUITY"
    CONFIG_SYMBOL = "CONFIG_SYMBOL"
    CONFIG_SECTOR = "CONFIG_SECTOR"
    SHORT_NOT_FO_ELIGIBLE = "SHORT_NOT_FO_ELIGIBLE"
    SHORT_NOT_PERMITTED = "SHORT_NOT_PERMITTED"


class Exclusion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_token: str
    symbol: str
    reasons: tuple[ExclusionReason, ...]


class UniverseSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    snapshot_id: UUID
    as_of: date
    generated_at: datetime
    instrument_snapshot_id: str
    compliance_profile_id: str
    long_tokens: tuple[str, ...]
    short_tokens: tuple[str, ...]
    exclusions: tuple[Exclusion, ...]


class UniverseBuilder:
    def build(
        self,
        *,
        master: InstrumentMasterSnapshot,
        compliance: ComplianceProfile,
        settings: UniverseSettings,
        as_of: date,
        generated_at: datetime,
    ) -> UniverseSnapshot:
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        if master.as_of != as_of:
            raise ValueError("daily universe date must match instrument snapshot as_of")
        longs: list[str] = []
        shorts: list[str] = []
        exclusions: list[Exclusion] = []
        for instrument in sorted(master.instruments, key=lambda item: (item.symbol, item.token)):
            base_reasons = self._base_exclusions(instrument, settings, as_of)
            if base_reasons:
                exclusions.append(self._exclusion(instrument, base_reasons))
                continue
            longs.append(instrument.token)
            short_reasons: list[ExclusionReason] = []
            rule = compliance.short_eligibility
            if not rule.intraday_short_allowed or not rule.broker_permission_verified:
                short_reasons.append(ExclusionReason.SHORT_NOT_PERMITTED)
            if (
                settings.require_fo_eligibility_for_short or rule.require_fo_eligibility
            ) and not instrument.fo_eligible:
                short_reasons.append(ExclusionReason.SHORT_NOT_FO_ELIGIBLE)
            if short_reasons:
                exclusions.append(self._exclusion(instrument, short_reasons))
            else:
                shorts.append(instrument.token)
        fingerprint = json.dumps(
            {
                "master": master.snapshot_id,
                "compliance": compliance.profile_id,
                "as_of": as_of.isoformat(),
                "settings": settings.model_dump(mode="json"),
                "longs": longs,
                "shorts": shorts,
                "exclusions": [item.model_dump(mode="json") for item in exclusions],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()
        return UniverseSnapshot(
            snapshot_id=uuid5(_UNIVERSE_NAMESPACE, digest),
            as_of=as_of,
            generated_at=generated_at,
            instrument_snapshot_id=master.snapshot_id,
            compliance_profile_id=compliance.profile_id,
            long_tokens=tuple(longs),
            short_tokens=tuple(shorts),
            exclusions=tuple(exclusions),
        )

    @staticmethod
    def _base_exclusions(
        instrument: Instrument, settings: UniverseSettings, as_of: date
    ) -> list[ExclusionReason]:
        reasons: list[ExclusionReason] = []
        if not instrument.active_on(as_of):
            reasons.append(ExclusionReason.NOT_EFFECTIVE)
        if instrument.status is InstrumentStatus.SUSPENDED:
            reasons.append(ExclusionReason.SUSPENDED)
        elif instrument.status is InstrumentStatus.UNTRADABLE:
            reasons.append(ExclusionReason.UNTRADABLE)
        if settings.exclude_surveillance and instrument.surveillance:
            reasons.append(ExclusionReason.SURVEILLANCE)
        if settings.exclude_data_ambiguity and instrument.data_ambiguous:
            reasons.append(ExclusionReason.DATA_AMBIGUITY)
        if instrument.symbol in settings.excluded_symbols:
            reasons.append(ExclusionReason.CONFIG_SYMBOL)
        if instrument.sector in settings.excluded_sectors:
            reasons.append(ExclusionReason.CONFIG_SECTOR)
        return reasons

    @staticmethod
    def _exclusion(instrument: Instrument, reasons: list[ExclusionReason]) -> Exclusion:
        return Exclusion(
            instrument_token=instrument.token,
            symbol=instrument.symbol,
            reasons=tuple(reasons),
        )
