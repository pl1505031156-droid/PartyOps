"""设备真实发行版、安装包类型与运行时能力。

Revision ID: 0019
Revises: 0018
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("devices")}
    additions = (
        sa.Column("platform_family", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("distribution", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("distribution_version", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("package_format", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("runtime_profile", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
    )
    for column in additions:
        if column.name not in existing:
            op.add_column("devices", column)
    inspector = sa.inspect(op.get_bind())
    indexes = (
        {item["name"] for item in inspector.get_indexes("update_runs")}
        if inspector.has_table("update_runs")
        else set()
    )
    if inspector.has_table("update_runs") and "uq_update_runs_one_active_host" not in indexes:
        # rc.2 没有数据库级互斥。若历史版本已因并发请求留下多个 applying，
        # 保留最早任务，其余明确终止后再建立唯一门禁。
        op.execute(
            sa.text(
                "UPDATE update_runs SET status='FAILED', progress=0, "
                "message='升级任务因历史并发冲突已安全终止，请重新发起' "
                "WHERE target_device_id IS NULL AND status='APPLYING' AND id NOT IN ("
                "SELECT id FROM update_runs WHERE target_device_id IS NULL "
                "AND status='APPLYING' ORDER BY created_at, id LIMIT 1)"
            )
        )
        op.create_index(
            "uq_update_runs_one_active_host",
            "update_runs",
            ["status"],
            unique=True,
            sqlite_where=sa.text("target_device_id IS NULL AND status = 'APPLYING'"),
        )


def downgrade() -> None:
    op.drop_index("uq_update_runs_one_active_host", table_name="update_runs")
    op.drop_column("devices", "capabilities")
    op.drop_column("devices", "runtime_profile")
    op.drop_column("devices", "package_format")
    op.drop_column("devices", "distribution_version")
    op.drop_column("devices", "distribution")
    op.drop_column("devices", "platform_family")
