"""PostgreSQL schema. Event projections retain canonical JSON for lossless replay."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProvenanceVersionRow(Base):
    __tablename__ = "provenance_versions"
    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_commit: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64))
    data_snapshot: Mapped[str] = mapped_column(Text)
    feature_version: Mapped[str] = mapped_column(Text)


class InstrumentVersionRow(Base):
    __tablename__ = "instrument_versions"
    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[date] = mapped_column(Date)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_reference: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class InstrumentRow(Base):
    __tablename__ = "instruments"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("instrument_versions.snapshot_id"), primary_key=True
    )
    token: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text)
    isin: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str] = mapped_column(Text)
    segment: Mapped[str] = mapped_column(Text)
    tick_size: Mapped[Decimal] = mapped_column(Numeric())
    status: Mapped[str] = mapped_column(Text)
    fo_eligible: Mapped[bool] = mapped_column(Boolean)
    sector: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer)
    provenance_id: Mapped[str] = mapped_column(ForeignKey("provenance_versions.version_id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    previous_hash: Mapped[str] = mapped_column(String(64))
    chain_hash: Mapped[str] = mapped_column(String(64))
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BarColumns:
    event_id: Mapped[UUID] = mapped_column(ForeignKey("audit_events.event_id"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text)
    bar_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bar_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    open: Mapped[Decimal] = mapped_column(Numeric())
    high: Mapped[Decimal] = mapped_column(Numeric())
    low: Mapped[Decimal] = mapped_column(Numeric())
    close: Mapped[Decimal] = mapped_column(Numeric())
    volume: Mapped[Decimal] = mapped_column(Numeric())
    complete: Mapped[bool] = mapped_column(Boolean)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Bar1mRow(BarColumns, Base):
    __tablename__ = "bars_1m"
    __table_args__ = (UniqueConstraint("instrument_id", "bar_start", "source"),)


class Bar5mRow(BarColumns, Base):
    __tablename__ = "bars_5m"
    __table_args__ = (UniqueConstraint("instrument_id", "bar_start", "source"),)


class QuoteRow(Base):
    __tablename__ = "quote_samples"
    event_id: Mapped[UUID] = mapped_column(ForeignKey("audit_events.event_id"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text)
    bid: Mapped[Decimal] = mapped_column(Numeric())
    ask: Mapped[Decimal] = mapped_column(Numeric())
    last: Mapped[Decimal] = mapped_column(Numeric())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SystemStateRow(Base):
    __tablename__ = "system_state"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    kill_switch: Mapped[bool] = mapped_column(Boolean)
    broker_health: Mapped[str] = mapped_column(Text)
    data_health: Mapped[str] = mapped_column(Text)
    release_id: Mapped[str] = mapped_column(Text)
    last_event_id: Mapped[UUID] = mapped_column(ForeignKey("audit_events.event_id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class FeatureVersionRow(Base):
    __tablename__ = "feature_set_versions"
    version_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)


class FeatureSnapshotRow(Base):
    __tablename__ = "feature_snapshots"
    snapshot_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("audit_events.event_id"), unique=True)
    feature_set_hash: Mapped[str] = mapped_column(ForeignKey("feature_set_versions.version_hash"))
    entity: Mapped[str] = mapped_column(Text, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
