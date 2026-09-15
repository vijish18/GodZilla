"""Versioned causal feature snapshots; event writes and projections commit together."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_set_versions",
        sa.Column("version_hash", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "feature_snapshots",
        sa.Column("snapshot_hash", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("audit_events.event_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "feature_set_hash",
            sa.String(64),
            sa.ForeignKey("feature_set_versions.version_hash"),
            nullable=False,
        ),
        sa.Column("entity", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_feature_snapshots_entity", "feature_snapshots", ["entity"])


def downgrade() -> None:
    op.drop_table("feature_snapshots")
    op.drop_table("feature_set_versions")
