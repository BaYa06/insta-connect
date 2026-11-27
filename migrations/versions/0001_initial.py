"""initial: media, media_metric_snapshots, token_events

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("media_type", sa.String(32)),
        sa.Column("media_product_type", sa.String(32)),
        sa.Column("caption", sa.Text()),
        sa.Column("permalink", sa.Text()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "media_metric_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("media_id", sa.String(32), sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(20, 3)),
        sa.Column("raw_unit", sa.String(16)),
        sa.Column("reason", sa.Text()),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_snap_media_metric_time", "media_metric_snapshots", ["media_id", "metric", "captured_at"])
    op.create_table(
        "token_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("is_valid", sa.Boolean()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("days_left", sa.Numeric(8, 2)),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("token_events")
    op.drop_index("ix_snap_media_metric_time", table_name="media_metric_snapshots")
    op.drop_table("media_metric_snapshots")
    op.drop_table("media")
