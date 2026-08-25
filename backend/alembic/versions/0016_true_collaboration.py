"""补齐档案贡献、共享目录、评论提及和收件箱处理状态。

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def columns(table: str) -> set[str]:
        return {item["name"] for item in sa.inspect(bind).get_columns(table)}

    if "can_contribute" not in columns("archive_access_grants"):
        with op.batch_alter_table("archive_access_grants") as batch:
            batch.add_column(
                sa.Column(
                    "can_contribute",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
    if "approval_note" not in columns("workspace_roots"):
        with op.batch_alter_table(
            "workspace_roots",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            batch.drop_constraint("uq_workspace_roots_name", type_="unique")
            batch.add_column(
                sa.Column("approval_note", sa.Text(), nullable=False, server_default="")
            )
            batch.create_unique_constraint(
                "uq_workspace_roots_device_remote_key",
                ["device_id", "remote_key"],
            )
    if "mentioned_user_ids" not in columns("task_comments"):
        with op.batch_alter_table("task_comments") as batch:
            batch.add_column(
                sa.Column(
                    "mentioned_user_ids",
                    sa.JSON(),
                    nullable=False,
                    server_default="[]",
                )
            )
    transfer_columns = columns("transfers")
    missing_transfer_columns = {
        "handled_by",
        "handled_at",
        "linked_entity_type",
        "linked_entity_id",
    } - transfer_columns
    if missing_transfer_columns:
        with op.batch_alter_table("transfers") as batch:
            if "handled_by" in missing_transfer_columns:
                batch.add_column(sa.Column("handled_by", sa.String(length=36), nullable=True))
            if "handled_at" in missing_transfer_columns:
                batch.add_column(sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True))
            if "linked_entity_type" in missing_transfer_columns:
                batch.add_column(
                    sa.Column(
                        "linked_entity_type",
                        sa.String(length=40),
                        nullable=False,
                        server_default="",
                    )
                )
            if "linked_entity_id" in missing_transfer_columns:
                batch.add_column(sa.Column("linked_entity_id", sa.String(length=36), nullable=True))
            if "handled_by" in missing_transfer_columns:
                batch.create_foreign_key(
                    "fk_transfers_handled_by_users",
                    "users",
                    ["handled_by"],
                    ["id"],
                )
            if "linked_entity_id" in missing_transfer_columns:
                batch.create_index("ix_transfers_linked_entity_id", ["linked_entity_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "handled_by" in {item["name"] for item in inspector.get_columns("transfers")}:
        with op.batch_alter_table("transfers") as batch:
            index_names = {
                item["name"] for item in inspector.get_indexes("transfers")
            }
            foreign_names = {
                item["name"]
                for item in inspector.get_foreign_keys("transfers")
                if item["name"]
            }
            if "ix_transfers_linked_entity_id" in index_names:
                batch.drop_index("ix_transfers_linked_entity_id")
            if "fk_transfers_handled_by_users" in foreign_names:
                batch.drop_constraint(
                    "fk_transfers_handled_by_users", type_="foreignkey"
                )
            batch.drop_column("linked_entity_id")
            batch.drop_column("linked_entity_type")
            batch.drop_column("handled_at")
            batch.drop_column("handled_by")
    if "mentioned_user_ids" in {
        item["name"] for item in sa.inspect(bind).get_columns("task_comments")
    }:
        with op.batch_alter_table("task_comments") as batch:
            batch.drop_column("mentioned_user_ids")
    inspector = sa.inspect(bind)
    if "approval_note" in {
        item["name"] for item in inspector.get_columns("workspace_roots")
    }:
        with op.batch_alter_table(
            "workspace_roots",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            unique_names = {
                item["name"]
                for item in inspector.get_unique_constraints("workspace_roots")
                if item["name"]
            }
            if "uq_workspace_roots_device_remote_key" in unique_names:
                batch.drop_constraint(
                    "uq_workspace_roots_device_remote_key", type_="unique"
                )
            batch.drop_column("approval_note")
            if "uq_workspace_roots_name" not in unique_names:
                batch.create_unique_constraint("uq_workspace_roots_name", ["name"])
    if "can_contribute" in {
        item["name"]
        for item in sa.inspect(bind).get_columns("archive_access_grants")
    }:
        with op.batch_alter_table("archive_access_grants") as batch:
            batch.drop_column("can_contribute")
