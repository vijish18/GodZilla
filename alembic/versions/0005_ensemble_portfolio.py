"""Separate alpha health and proposed allocation projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alpha_health",
        sa.Column("snapshot_hash", sa.String(64), primary_key=True),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("audit_events.event_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("alpha_id", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )
    for table in ("ensemble_decisions", "portfolio_decisions"):
        op.create_table(
            table,
            sa.Column("decision_id", sa.Uuid(), primary_key=True),
            sa.Column(
                "event_id",
                sa.Uuid(),
                sa.ForeignKey("audit_events.event_id"),
                nullable=False,
                unique=True,
            ),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
        )


def downgrade() -> None:
    for table in ("portfolio_decisions", "ensemble_decisions", "alpha_health"):
        op.drop_table(table)
