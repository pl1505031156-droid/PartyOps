"""补齐档案贡献、共享目录、评论提及和收件箱处理状态。

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("archive_access_grants") as batch:
        batch.add_column(
            sa.Column(
                "can_contribute",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
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
    with op.batch_alter_table("task_comments") as batch:
        batch.add_column(
            sa.Column("mentioned_user_ids", sa.JSON(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("transfers") as batch:
        batch.add_column(sa.Column("handled_by", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("linked_entity_type", sa.String(length=40), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("linked_entity_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_transfers_handled_by_users",
            "users",
            ["handled_by"],
            ["id"],
        )
        batch.create_index("ix_transfers_linked_entity_id", ["linked_entity_id"])


def downgrade() -> None:
    with op.batch_alter_table("transfers") as batch:
        batch.drop_index("ix_transfers_linked_entity_id")
        batch.drop_constraint("fk_transfers_handled_by_users", type_="foreignkey")
        batch.drop_column("linked_entity_id")
        batch.drop_column("linked_entity_type")
        batch.drop_column("handled_at")
        batch.drop_column("handled_by")
    with op.batch_alter_table("task_comments") as batch:
        batch.drop_column("mentioned_user_ids")
    with op.batch_alter_table(
        "workspace_roots",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.drop_constraint("uq_workspace_roots_device_remote_key", type_="unique")
        batch.drop_column("approval_note")
        batch.create_unique_constraint("uq_workspace_roots_name", ["name"])
    with op.batch_alter_table("archive_access_grants") as batch:
        batch.drop_column("can_contribute")
