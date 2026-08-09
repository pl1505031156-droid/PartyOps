"""新增东方主题用户偏好。

Revision ID: 0014
Revises: 0013
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_appearance_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "art_level",
            sa.Enum("standard", "reduced", name="artlevel", native_enum=False),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("reduce_motion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "theme_override",
            sa.Enum(
                "spring",
                "summer",
                "autumn",
                "winter",
                name="seasontheme",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_appearance_preferences")
