"""1.3.0 工作台与效率功能模型。"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_release_notes "
        "(revision VARCHAR(32) PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    bind.execute(sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0006')"))


def downgrade() -> None:
    op.execute("DELETE FROM schema_release_notes WHERE revision = '0006'")
