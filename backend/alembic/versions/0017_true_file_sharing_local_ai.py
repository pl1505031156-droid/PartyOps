"""真文件共享、双通道下载和分能力本地模型。

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("allow_user_shares", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    with op.batch_alter_table("workspace_roots") as batch:
        batch.add_column(sa.Column("published_by_user_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("share_scope", sa.String(length=16), nullable=False, server_default="team")
        )
        batch.add_column(
            sa.Column("semantic_content_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_workspace_roots_published_by_users",
            "users",
            ["published_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_workspace_roots_published_by_user_id", ["published_by_user_id"])
        batch.create_index("ix_workspace_roots_share_scope", ["share_scope"])

    # 既有设备根继续沿用原授权，避免升级时意外扩大历史共享范围。
    op.execute("UPDATE workspace_roots SET share_scope='selected' WHERE lower(source)='device'")

    op.create_table(
        "workspace_root_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("root_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("can_browse", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_download", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("can_send", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["root_id"], ["workspace_roots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("root_id", "user_id", name="uq_workspace_root_members_root_user"),
    )
    op.create_index("ix_workspace_root_members_root_id", "workspace_root_members", ["root_id"])
    op.create_index("ix_workspace_root_members_user_id", "workspace_root_members", ["user_id"])

    op.create_table(
        "local_share_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_local_share_actions_token_hash", "local_share_actions", ["token_hash"])
    op.create_index("ix_local_share_actions_device_id", "local_share_actions", ["device_id"])
    op.create_index("ix_local_share_actions_user_id", "local_share_actions", ["user_id"])
    op.create_index("ix_local_share_actions_expires_at", "local_share_actions", ["expires_at"])

    with op.batch_alter_table("transfers") as batch:
        batch.add_column(
            sa.Column("delivery_mode", sa.String(length=24), nullable=False, server_default="managed_inbox")
        )
        batch.add_column(
            sa.Column("bundle_mode", sa.String(length=24), nullable=False, server_default="single")
        )
        batch.add_column(sa.Column("item_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("result_name", sa.String(length=255), nullable=False, server_default=""))
        batch.add_column(sa.Column("result_sha256", sa.String(length=64), nullable=False, server_default=""))

    with op.batch_alter_table("ai_model_packs") as batch:
        batch.add_column(sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("min_runtime_version", sa.String(length=32), nullable=False, server_default="1.4.1")
        )
        batch.add_column(sa.Column("estimated_memory_mb", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("model_source", sa.String(length=500), nullable=False, server_default=""))
        batch.add_column(sa.Column("license_name", sa.String(length=80), nullable=False, server_default=""))

    op.execute("UPDATE ai_model_packs SET capabilities='[\"embedding\", \"llm\"]'")
    op.create_table(
        "ai_model_activations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("capability", sa.String(length=24), nullable=False),
        sa.Column("model_pack_id", sa.String(length=36), nullable=False),
        sa.Column("activated_by", sa.String(length=36), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_pack_id"], ["ai_model_packs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability"),
    )
    op.create_index("ix_ai_model_activations_capability", "ai_model_activations", ["capability"])
    op.create_index("ix_ai_model_activations_model_pack_id", "ai_model_activations", ["model_pack_id"])


def downgrade() -> None:
    op.drop_table("ai_model_activations")
    with op.batch_alter_table("ai_model_packs") as batch:
        batch.drop_column("license_name")
        batch.drop_column("model_source")
        batch.drop_column("estimated_memory_mb")
        batch.drop_column("min_runtime_version")
        batch.drop_column("capabilities")
    with op.batch_alter_table("transfers") as batch:
        batch.drop_column("result_sha256")
        batch.drop_column("result_name")
        batch.drop_column("item_ids")
        batch.drop_column("bundle_mode")
        batch.drop_column("delivery_mode")
    op.drop_table("local_share_actions")
    op.drop_table("workspace_root_members")
    with op.batch_alter_table("workspace_roots") as batch:
        batch.drop_index("ix_workspace_roots_share_scope")
        batch.drop_index("ix_workspace_roots_published_by_user_id")
        batch.drop_constraint("fk_workspace_roots_published_by_users", type_="foreignkey")
        batch.drop_column("published_at")
        batch.drop_column("semantic_content_enabled")
        batch.drop_column("share_scope")
        batch.drop_column("published_by_user_id")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("allow_user_shares")
