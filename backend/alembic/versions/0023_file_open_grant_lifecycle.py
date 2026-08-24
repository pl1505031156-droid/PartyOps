"""文件打开授权生命周期与精确诊断。

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("file_open_grants")
    }


def _indexes() -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes("file_open_grants")
    }


def upgrade() -> None:
    existing = _columns()
    definitions = (
        sa.Column("target_device_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="created"),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("result_detail", sa.Text(), nullable=False, server_default=""),
    )
    with op.batch_alter_table("file_open_grants") as batch:
        for column in definitions:
            if column.name not in existing:
                batch.add_column(column)
    indexes = _indexes()
    for column in (
        "target_device_id",
        "status",
        "redeemed_at",
        "opened_at",
        "completed_at",
    ):
        name = f"ix_file_open_grants_{column}"
        if name not in indexes:
            op.create_index(name, "file_open_grants", [column])


def downgrade() -> None:
    indexes = _indexes()
    for column in (
        "target_device_id",
        "status",
        "redeemed_at",
        "opened_at",
        "completed_at",
    ):
        name = f"ix_file_open_grants_{column}"
        if name in indexes:
            op.drop_index(name, table_name="file_open_grants")
    existing = _columns()
    with op.batch_alter_table("file_open_grants") as batch:
        for column in (
            "result_detail",
            "result_code",
            "completed_at",
            "opened_at",
            "redeemed_at",
            "status",
            "target_device_id",
        ):
            if column in existing:
                batch.drop_column(column)
