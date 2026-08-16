"""党员发展补充材料模板与审计字段。

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _table_complete(table: str, expected_columns: set[str]) -> bool:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if table not in tables:
        return False
    actual = {column["name"] for column in inspector.get_columns(table)}
    missing = sorted(expected_columns - actual)
    if missing:
        raise RuntimeError(f"数据库迁移 0018 检测到不完整表 {table}，缺少字段：{', '.join(missing)}")
    return True


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def upgrade() -> None:
    if not _table_complete(
        "party_development_profiles",
        {"id", "name", "description", "source_label", "active", "version", "created_by", "created_at", "updated_at"},
    ):
        op.create_table(
            "party_development_profiles",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_label", sa.String(length=255), nullable=False, server_default="本单位补充"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
    _create_index_if_missing("ix_party_development_profiles_name", "party_development_profiles", ["name"])
    _create_index_if_missing("ix_party_development_profiles_active", "party_development_profiles", ["active"])
    _create_index_if_missing("ix_party_development_profiles_created_by", "party_development_profiles", ["created_by"])

    if not _table_complete(
        "party_development_materials",
        {"id", "profile_id", "phase", "name", "responsible_party", "guidance", "required", "enabled", "sort_order", "version", "created_by", "created_at", "updated_at"},
    ):
        op.create_table(
            "party_development_materials",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("profile_id", sa.String(length=36), nullable=False),
            sa.Column("phase", sa.String(length=48), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("responsible_party", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("guidance", sa.Text(), nullable=False, server_default=""),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["profile_id"], ["party_development_profiles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("profile_id", "phase", "name", name="uq_party_development_material"),
        )
    _create_index_if_missing("ix_party_development_materials_profile_id", "party_development_materials", ["profile_id"])
    _create_index_if_missing("ix_party_development_materials_phase", "party_development_materials", ["phase"])
    _create_index_if_missing("ix_party_development_materials_created_by", "party_development_materials", ["created_by"])


def downgrade() -> None:
    op.drop_table("party_development_materials")
    op.drop_table("party_development_profiles")
