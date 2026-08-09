"""1.1.3 文件夹选择接入、发布历史与设备版本门禁。"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {
        str(row[1])
        for row in bind.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
    }


def upgrade() -> None:
    root_columns = _columns("workspace_roots")
    if "selection_mode" not in root_columns:
        op.add_column(
            "workspace_roots",
            sa.Column(
                "selection_mode",
                sa.String(16),
                nullable=False,
                server_default="all",
            ),
        )
    if "included_paths" not in root_columns:
        op.add_column(
            "workspace_roots",
            sa.Column(
                "included_paths",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )

    file_columns = _columns("workspace_files")
    if "in_scope" not in file_columns:
        op.add_column(
            "workspace_files",
            sa.Column(
                "in_scope",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS release_history (
          id VARCHAR(36) PRIMARY KEY,
          version VARCHAR(32) NOT NULL UNIQUE,
          schema_revision VARCHAR(32) NOT NULL DEFAULT '',
          title VARCHAR(160) NOT NULL DEFAULT '',
          release_notes JSON NOT NULL DEFAULT '[]',
          package_id VARCHAR(36),
          status VARCHAR(24) NOT NULL DEFAULT 'installed',
          installed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(package_id) REFERENCES update_packages(id) ON DELETE SET NULL
        )
        """
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_workspace_roots_selection_mode "
        "ON workspace_roots(selection_mode)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_workspace_files_in_scope "
        "ON workspace_files(in_scope)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_release_history_version "
        "ON release_history(version)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_release_history_package_id "
        "ON release_history(package_id)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_release_history_status "
        "ON release_history(status)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_release_notes "
        "(revision VARCHAR(32) PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    bind.execute(
        sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0010')")
    )


def downgrade() -> None:
    # 单机现场数据仅支持前向迁移；保留选择配置和发布记录，防止回滚丢失审计。
    op.execute("DELETE FROM schema_release_notes WHERE revision = '0010'")
