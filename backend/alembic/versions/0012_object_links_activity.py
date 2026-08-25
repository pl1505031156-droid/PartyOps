"""1.2.0 统一对象关联、反向链接和活动时间线。

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "object_links" not in existing_tables:
        op.create_table(
            "object_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("source_id", sa.String(64), nullable=False),
            sa.Column("target_type", sa.String(32), nullable=False),
            sa.Column("target_id", sa.String(64), nullable=False),
            sa.Column("link_type", sa.String(32), nullable=False, server_default="RELATES_TO"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint(
                "source_type",
                "source_id",
                "target_type",
                "target_id",
                "link_type",
                name="uq_object_link",
            ),
        )
    object_indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("object_links")
    }
    for name, columns in (
        ("ix_object_links_source", ["source_type", "source_id"]),
        ("ix_object_links_target", ["target_type", "target_id"]),
        ("ix_object_links_link_type", ["link_type"]),
        ("ix_object_links_created_by", ["created_by"]),
    ):
        if name not in object_indexes:
            op.create_index(name, "object_links", columns)

    if "activity_events" not in existing_tables:
        op.create_table(
            "activity_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("object_type", sa.String(32), nullable=False),
            sa.Column("object_id", sa.String(64), nullable=False),
            sa.Column("event_code", sa.String(80), nullable=False),
            sa.Column(
                "actor_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "happened_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "recorded_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("event_data", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("correlation_id", sa.String(80), nullable=False, server_default=""),
            sa.Column("idempotency_key", sa.String(160), nullable=True, unique=True),
        )
    activity_indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("activity_events")
    }
    for name, columns in (
        ("ix_activity_object_time", ["object_type", "object_id", "happened_at"]),
        ("ix_activity_events_event_code", ["event_code"]),
        ("ix_activity_events_actor_id", ["actor_id"]),
        ("ix_activity_events_correlation_id", ["correlation_id"]),
    ):
        if name not in activity_indexes:
            op.create_index(name, "activity_events", columns)

    # 旧版专题使用 JSON 数组保存成员。迁移为统一关联时保留原字段供旧页面
    # 兼容，插入使用唯一约束和确定的方向，重复运行也不会重复关联。
    topics = bind.execute(
        sa.text(
            "SELECT id, owner_id, task_ids, file_ids, journal_ids, contact_ids "
            "FROM topic_spaces"
        )
    ).mappings()
    mapping = {
        "task_ids": "TASK",
        "file_ids": "WORKSPACE_FILE",
        "journal_ids": "JOURNAL",
        "contact_ids": "CONTACT",
    }
    for topic in topics:
        for field, target_type in mapping.items():
            try:
                identifiers = json.loads(topic[field] or "[]")
            except (TypeError, ValueError):
                identifiers = []
            for identifier in identifiers:
                if not isinstance(identifier, str) or not identifier:
                    continue
                bind.execute(
                    sa.text(
                        "INSERT OR IGNORE INTO object_links("
                        "id,source_type,source_id,target_type,target_id,link_type,"
                        "note,version,created_by,created_at"
                        ") VALUES ("
                        ":id,'TOPIC',:topic_id,:target_type,:target_id,'BELONGS_TO',"
                        "'旧版专题成员迁移',1,:owner_id,CURRENT_TIMESTAMP)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "topic_id": topic["id"],
                        "target_type": target_type,
                        "target_id": identifier,
                        "owner_id": topic["owner_id"],
                    },
                )
    bind.execute(
        sa.text("INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0012')")
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "activity_events" in tables:
        op.drop_table("activity_events")
    if "object_links" in tables:
        op.drop_table("object_links")
    if "schema_release_notes" in tables:
        op.execute("DELETE FROM schema_release_notes WHERE revision LIKE '0012%'")
