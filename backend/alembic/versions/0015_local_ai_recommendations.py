"""新增签名模型包、语义检查点和可追溯推荐。

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_invocations") as batch:
        batch.alter_column("provider_id", existing_type=sa.String(length=36), nullable=True)
    op.create_table(
        "ai_model_packs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("architecture", sa.String(length=16), nullable=False, server_default="universal"),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("install_key", sa.String(length=80), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column(
            "signature_valid",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "status",
            sa.Enum(
                "missing",
                "verifying",
                "installed",
                "active",
                "corrupt",
                name="modelpackstatus",
                native_enum=False,
            ),
            nullable=False,
            server_default="verifying",
        ),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filename"),
        sa.UniqueConstraint("install_key"),
    )
    op.create_index("ix_ai_model_packs_version", "ai_model_packs", ["version"])
    op.create_index("ix_ai_model_packs_model_id", "ai_model_packs", ["model_id"])
    op.create_index("ix_ai_model_packs_status", "ai_model_packs", ["status"])

    op.create_table(
        "semantic_index_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("object_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_pack_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_blob", sa.LargeBinary(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_pack_id"], ["ai_model_packs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_type", "object_id", "model_pack_id"),
    )
    op.create_index("ix_semantic_index_checkpoints_object_type", "semantic_index_checkpoints", ["object_type"])
    op.create_index("ix_semantic_index_checkpoints_object_id", "semantic_index_checkpoints", ["object_id"])
    op.create_index("ix_semantic_index_checkpoints_model_pack_id", "semantic_index_checkpoints", ["model_pack_id"])

    op.create_table(
        "ai_recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "generator",
            sa.Enum("rules", "embedding", "local_llm", "external_llm", name="recommendationgenerator", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "accepted", "dismissed", "expired", name="recommendationstatus", native_enum=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("object_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("route", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_ai_recommendations_user_id", "ai_recommendations", ["user_id"])
    op.create_index("ix_ai_recommendations_generator", "ai_recommendations", ["generator"])
    op.create_index("ix_ai_recommendations_status", "ai_recommendations", ["status"])
    op.create_index("ix_ai_recommendations_score", "ai_recommendations", ["score"])
    op.create_index("ix_ai_recommendations_object_type", "ai_recommendations", ["object_type"])
    op.create_index("ix_ai_recommendations_object_id", "ai_recommendations", ["object_id"])
    op.create_index("ix_ai_recommendations_expires_at", "ai_recommendations", ["expires_at"])


def downgrade() -> None:
    op.drop_table("ai_recommendations")
    op.drop_table("semantic_index_checkpoints")
    op.drop_table("ai_model_packs")
    # 旧版本要求每条调用都关联外部供应商；回退时仅移除 1.3.0 新增的
    # 本地模型调用记录，避免非空约束导致整个回退事务失败。
    op.execute("DELETE FROM ai_invocations WHERE provider_id IS NULL")
    with op.batch_alter_table("ai_invocations") as batch:
        batch.alter_column("provider_id", existing_type=sa.String(length=36), nullable=False)
