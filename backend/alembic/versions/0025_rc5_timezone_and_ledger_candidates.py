"""rc.5 北京时间迁移审计与台账候选字段。

迁移只增加可回滚的元数据，不改写服务器生成时间。旧的用户录入无时区值
由应用启动事务按 Asia/Shanghai 解释，并把每次转换写入审计表。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

USER_WALL_TIME_FIELDS: dict[str, tuple[str, ...]] = {
    "tasks": ("formal_due_at", "internal_due_at", "planned_start_at", "planned_end_at"),
    "task_steps": ("due_at",),
    "business_meetings": ("scheduled_at",),
    "meeting_actions": ("due_at",),
    # next_run_at 由调度器生成，已经是 UTC，绝不能按用户墙上时间迁移。
    "recurrence_rules": ("paused_until", "end_at"),
    "recurrence_exceptions": ("rescheduled_at",),
}


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    op.create_table(
        "timezone_migration_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("field_name", sa.String(96), nullable=False),
        sa.Column("before_value", sa.String(64), nullable=True),
        sa.Column("after_value", sa.String(64), nullable=True),
        sa.Column("source_timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("rule_version", sa.String(32), nullable=False, server_default="0025"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("entity_type", "entity_id", "created_at"):
        op.create_index(f"ix_timezone_migration_audits_{column}", "timezone_migration_audits", [column])

    columns = _columns("ledger_import_jobs")
    with op.batch_alter_table("ledger_import_jobs") as batch:
        if "derived_candidates" not in columns:
            batch.add_column(sa.Column("derived_candidates", sa.JSON(), nullable=False, server_default="[]"))
        if "manual_edits" not in columns:
            batch.add_column(sa.Column("manual_edits", sa.JSON(), nullable=False, server_default="{}"))
    columns = _columns("ledger_import_jobs")
    for column in ("derived_candidates",):
        index_name = f"ix_ledger_import_jobs_{column}"
        if column in columns and index_name not in _indexes("ledger_import_jobs"):
            # SQLite 不支持 JSON 语义索引，这里仅保留列；分支留给兼容数据库。
            continue

    # rc.4 日期控件曾把北京时间墙上值直接标成 Z，导致展示时多 8 小时。
    # 仅迁移明确由用户填写的截止/排期字段；服务器生成的 created_at、日志和
    # 心跳不触碰。每个值先写可回滚审计，再按 SQLite 原生 datetime 减 8 小时。
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        existing_tables = {row[0] for row in bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        for table, fields in USER_WALL_TIME_FIELDS.items():
            if table not in existing_tables:
                continue
            table_columns = _columns(table)
            for field in fields:
                if field not in table_columns:
                    continue
                rows = bind.execute(sa.text(f"SELECT id, {field} FROM {table} WHERE {field} IS NOT NULL")).fetchall()
                for entity_id, before in rows:
                    before_text = str(before)
                    # 已带偏移的值不是旧控件产生的值，避免二次转换。
                    if before_text.endswith("Z") or "+" in before_text[10:]:
                        continue
                    after = bind.execute(sa.text("SELECT datetime(:value, '-8 hours')"), {"value": before_text}).scalar()
                    if not after:
                        continue
                    bind.execute(sa.text(f"UPDATE {table} SET {field} = :after WHERE id = :id"), {"after": after, "id": entity_id})
                    bind.execute(
                        sa.text("INSERT INTO timezone_migration_audits (id, entity_type, entity_id, field_name, before_value, after_value, source_timezone, rule_version, created_at) VALUES (:id, :entity_type, :entity_id, :field_name, :before_value, :after_value, :source_timezone, :rule_version, :created_at)"),
                        {"id": str(uuid.uuid4()), "entity_type": table, "entity_id": str(entity_id), "field_name": field, "before_value": before_text, "after_value": str(after), "source_timezone": "Asia/Shanghai", "rule_version": "0025", "created_at": now},
                    )


def downgrade() -> None:
    tables = _tables()
    bind = op.get_bind()
    if bind.dialect.name == "sqlite" and "timezone_migration_audits" in tables:
        # 只回滚仍等于本迁移写入值的字段；若升级后用户已经重新修改时间，
        # 保留新值，避免降级过程静默覆盖真实业务操作。表名和字段名必须来自
        # 静态白名单，绝不执行审计表中可被篡改的标识符。
        audits = bind.execute(
            sa.text(
                "SELECT entity_type, entity_id, field_name, before_value, after_value "
                "FROM timezone_migration_audits WHERE rule_version = '0025'"
            )
        ).fetchall()
        for table, entity_id, field, before, after in audits:
            if table not in USER_WALL_TIME_FIELDS or field not in USER_WALL_TIME_FIELDS[table]:
                continue
            if table not in tables or field not in _columns(table):
                continue
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET {field} = :before "
                    f"WHERE id = :id AND {field} = :after"
                ),
                {"before": before, "after": after, "id": entity_id},
            )
    if "ledger_import_jobs" in tables:
        columns = _columns("ledger_import_jobs")
        with op.batch_alter_table("ledger_import_jobs") as batch:
            for column in ("manual_edits", "derived_candidates"):
                if column in columns:
                    batch.drop_column(column)
    if "timezone_migration_audits" in tables:
        for column in ("created_at", "entity_id", "entity_type"):
            name = f"ix_timezone_migration_audits_{column}"
            if name in _indexes("timezone_migration_audits"):
                op.drop_index(name, table_name="timezone_migration_audits")
        op.drop_table("timezone_migration_audits")
