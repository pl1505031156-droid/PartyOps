"""综合工作管理、文件索引与 AI 权限模型。

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """应用幂等迁移器负责 SQLite 逐列升级，此处登记正式模式版本。"""

    bind = op.get_bind()
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_release_notes "
        "(revision VARCHAR(32) PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    bind.execute(
        sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0003')")
    )


def downgrade() -> None:
    """新增业务表包含不可逆数据，降级应恢复升级前自动备份。"""

    op.execute("DELETE FROM schema_release_notes WHERE revision = '0003'")
