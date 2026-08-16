"""1.2.0 统一日历、周期例外、投影检查点和用户引导。

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    recurrence_columns = _columns("recurrence_rules")
    additions = (
        ("schedule_config", sa.Column("schedule_config", sa.JSON(), nullable=False, server_default="{}")),
        ("paused_until", sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True)),
        ("end_at", sa.Column("end_at", sa.DateTime(timezone=True), nullable=True)),
        ("max_occurrences", sa.Column("max_occurrences", sa.Integer(), nullable=True)),
        ("occurrence_count", sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0")),
        ("last_error", sa.Column("last_error", sa.Text(), nullable=False, server_default="")),
    )
    for name, column in additions:
        if name not in recurrence_columns:
            op.add_column("recurrence_rules", column)

    op.create_table(
        "calendar_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("default_view", sa.String(16), nullable=False, server_default="week"),
        sa.Column("week_starts_on", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("visible_event_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("compact_weekends", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_calendar_preferences_user_id",
        "calendar_preferences",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "recurrence_exceptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(36),
            sa.ForeignKey("recurrence_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("rescheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("rule_id", "occurrence_at"),
    )
    op.create_index(
        "ix_recurrence_exceptions_rule_id",
        "recurrence_exceptions",
        ["rule_id"],
    )
    op.create_index(
        "ix_recurrence_exceptions_occurrence_at",
        "recurrence_exceptions",
        ["occurrence_at"],
    )

    op.create_table(
        "projection_checkpoints",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("last_event_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="idle"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_projection_checkpoints_status",
        "projection_checkpoints",
        ["status"],
    )

    op.create_table(
        "onboarding_progress",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("completed_steps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_onboarding_progress_user_id",
        "onboarding_progress",
        ["user_id"],
        unique=True,
    )
    op.execute(
        "INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0013')"
    )


def downgrade() -> None:
    op.drop_table("onboarding_progress")
    op.drop_table("projection_checkpoints")
    op.drop_table("recurrence_exceptions")
    op.drop_table("calendar_preferences")
    op.execute("DELETE FROM schema_release_notes WHERE revision = '0013'")
