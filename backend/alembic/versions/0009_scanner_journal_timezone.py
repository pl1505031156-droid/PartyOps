"""1.1.2 全类型文件索引与结构化工作日志。"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    return {
        str(row[1])
        for row in bind.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
    }


def upgrade() -> None:
    workspace_columns = _columns("workspace_files")
    workspace_additions = (
        ("content_status", sa.Column("content_status", sa.String(32), nullable=False, server_default="metadata_only")),
        ("content_error_code", sa.Column("content_error_code", sa.String(64), nullable=False, server_default="")),
        ("detected_type", sa.Column("detected_type", sa.String(160), nullable=False, server_default="application/octet-stream")),
        ("archive_member_count", sa.Column("archive_member_count", sa.Integer(), nullable=False, server_default="0")),
    )
    for name, column in workspace_additions:
        if name not in workspace_columns:
            op.add_column("workspace_files", column)

    journal_columns = _columns("work_journal_entries")
    journal_additions = (
        ("event_code", sa.Column("event_code", sa.String(64), nullable=False, server_default="")),
        ("event_data", sa.Column("event_data", sa.JSON(), nullable=False, server_default="{}")),
    )
    for name, column in journal_additions:
        if name not in journal_columns:
            op.add_column("work_journal_entries", column)

    bind = op.get_bind()
    bind.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS work_journal_revisions (
          id VARCHAR(36) PRIMARY KEY,
          entry_id VARCHAR(36) NOT NULL,
          revision_no INTEGER NOT NULL,
          snapshot JSON NOT NULL DEFAULT '{}',
          change_note TEXT NOT NULL DEFAULT '',
          created_by VARCHAR(36) NOT NULL,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(entry_id) REFERENCES work_journal_entries(id) ON DELETE CASCADE,
          FOREIGN KEY(created_by) REFERENCES users(id),
          UNIQUE(entry_id, revision_no)
        )
        """
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_work_journal_revisions_entry_id "
        "ON work_journal_revisions(entry_id)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_workspace_files_content_status "
        "ON workspace_files(content_status)"
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_work_journal_entries_event_code "
        "ON work_journal_entries(event_code)"
    )
    bind.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_release_notes "
        "(revision VARCHAR(32) PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    bind.execute(
        sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0009')")
    )


def downgrade() -> None:
    # SQLite 现场数据库只做前向升级；保留新增列，避免破坏已生成的索引与日志。
    op.execute("DELETE FROM schema_release_notes WHERE revision = '0009'")
