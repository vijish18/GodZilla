"""Initial durable event, projection and version tables. Frozen schema definition."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provenance_versions",
        sa.Column("version_id", sa.String(64), primary_key=True),
        sa.Column("code_commit", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("data_snapshot", sa.Text(), nullable=False),
        sa.Column("feature_version", sa.Text(), nullable=False),
    )
    op.create_table(
        "instrument_versions",
        sa.Column("snapshot_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "instruments",
        sa.Column(
            "snapshot_id",
            sa.Text(),
            sa.ForeignKey("instrument_versions.snapshot_id"),
            primary_key=True,
        ),
        sa.Column("token", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("isin", sa.Text(), nullable=False),
        sa.Column("exchange", sa.Text(), nullable=False),
        sa.Column("segment", sa.Text(), nullable=False),
        sa.Column("tick_size", sa.Numeric(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fo_eligible", sa.Boolean(), nullable=False),
        sa.Column("sector", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "audit_events",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "provenance_id",
            sa.String(64),
            sa.ForeignKey("provenance_versions.version_id"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("chain_hash", sa.String(64), nullable=False),
        sa.Column("envelope", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    for table in ("bars_1m", "bars_5m"):
        op.create_table(
            table,
            sa.Column(
                "event_id", sa.Uuid(), sa.ForeignKey("audit_events.event_id"), primary_key=True
            ),
            sa.Column("instrument_id", sa.Text(), nullable=False),
            sa.Column("bar_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("bar_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("open", sa.Numeric(), nullable=False),
            sa.Column("high", sa.Numeric(), nullable=False),
            sa.Column("low", sa.Numeric(), nullable=False),
            sa.Column("close", sa.Numeric(), nullable=False),
            sa.Column("volume", sa.Numeric(), nullable=False),
            sa.Column("complete", sa.Boolean(), nullable=False),
            sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.UniqueConstraint("instrument_id", "bar_start", "source"),
        )
    op.create_table(
        "quote_samples",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("audit_events.event_id"), primary_key=True),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("bid", sa.Numeric(), nullable=False),
        sa.Column("ask", sa.Numeric(), nullable=False),
        sa.Column("last", sa.Numeric(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "system_state",
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("kill_switch", sa.Boolean(), nullable=False),
        sa.Column("broker_health", sa.Text(), nullable=False),
        sa.Column("data_health", sa.Text(), nullable=False),
        sa.Column("release_id", sa.Text(), nullable=False),
        sa.Column(
            "last_event_id", sa.Uuid(), sa.ForeignKey("audit_events.event_id"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "system_state",
        "quote_samples",
        "bars_5m",
        "bars_1m",
        "audit_events",
        "instruments",
        "instrument_versions",
        "provenance_versions",
    ):
        op.drop_table(table)
