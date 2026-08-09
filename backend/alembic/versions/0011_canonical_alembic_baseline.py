"""1.2.0 统一 Alembic 基线。

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "CREATE TABLE IF NOT EXISTS schema_release_notes ("
            "revision VARCHAR(32) PRIMARY KEY, "
            "applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    bind.execute(
        sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0011')")
    )


def downgrade() -> None:
    op.execute("DELETE FROM schema_release_notes WHERE revision = '0011'")
