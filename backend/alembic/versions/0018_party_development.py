"""党员发展补充材料模板与审计字段。

Revision ID: 0018
Revises: 0017
"""

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index("ix_party_development_profiles_name", "party_development_profiles", ["name"])
    op.create_index("ix_party_development_profiles_active", "party_development_profiles", ["active"])
    op.create_index("ix_party_development_profiles_created_by", "party_development_profiles", ["created_by"])

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
    op.create_index("ix_party_development_materials_profile_id", "party_development_materials", ["profile_id"])
    op.create_index("ix_party_development_materials_phase", "party_development_materials", ["phase"])
    op.create_index("ix_party_development_materials_created_by", "party_development_materials", ["created_by"])


def downgrade() -> None:
    op.drop_table("party_development_materials")
    op.drop_table("party_development_profiles")
