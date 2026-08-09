"""补齐减负版业务字段与运维模型。

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """正式安装升级由应用的幂等迁移器执行，本版本用于记录模式契约。"""

    # SQLite 的批量变更在不同 UOS 运行库上兼容性差；应用启动时会逐列检测并
    # 执行同等的 ALTER TABLE，同时由 SQLAlchemy 创建新增表。
    bind = op.get_bind()
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_release_notes "
        "(revision VARCHAR(32) PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    bind.execute(
        sa.text(
            "INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0002')"
        )
    )


def downgrade() -> None:
    """业务数据升级不可逆；降级程序应恢复升级前自动备份。"""

    op.execute("DELETE FROM schema_release_notes WHERE revision = '0002'")
