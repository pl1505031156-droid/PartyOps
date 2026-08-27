"""rc.6 全系统智能编排器与最小上下文授权表。

Revision ID: 0026
Revises: 0025

本迁移只增加结构化计划和审计表，不写入原始提示、文档正文、凭据或模型
响应。所有 JSON 字段由服务层做白名单校验，便于后续增量扩展和安全回滚。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def _index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    tables = _tables()
    if "ai_orchestration_sessions" not in tables:
        op.create_table(
            "ai_orchestration_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("goal_summary", sa.String(500), nullable=False),
            sa.Column("input_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("state", sa.String(32), nullable=False, server_default="awaiting_confirmation"),
            sa.Column("model_id", sa.String(160), nullable=False, server_default="rules"),
            sa.Column("external_consented", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
            sa.Column("context_scope", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("plan", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("unresolved", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _index("ix_ai_orchestration_sessions_user_id", "ai_orchestration_sessions", ["user_id"])
    _index("ix_ai_orchestration_sessions_state", "ai_orchestration_sessions", ["state"])
    _index("ix_ai_orchestration_sessions_expires_at", "ai_orchestration_sessions", ["expires_at"])

    tables = _tables()
    if "ai_orchestration_steps" not in tables:
        op.create_table(
            "ai_orchestration_steps",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("ai_orchestration_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("tool_name", sa.String(120), nullable=False),
            sa.Column("arguments", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
            sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("preconditions", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("compensation", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("idempotency_key", sa.String(120), nullable=False),
            sa.Column("result_summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(80), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_ai_orchestration_step_idempotency"),
        )
    _index("ix_ai_orchestration_steps_session_id", "ai_orchestration_steps", ["session_id"])
    _index("ix_ai_orchestration_steps_tool_name", "ai_orchestration_steps", ["tool_name"])
    _index("ix_ai_orchestration_steps_status", "ai_orchestration_steps", ["status"])

    tables = _tables()
    if "ai_orchestration_approvals" not in tables:
        op.create_table(
            "ai_orchestration_approvals",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("ai_orchestration_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_id", sa.String(36), sa.ForeignKey("ai_orchestration_steps.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("scope_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("device_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("session_id", "step_id", "user_id"):
        _index(f"ix_ai_orchestration_approvals_{column}", "ai_orchestration_approvals", [column])

    tables = _tables()
    if "ai_context_grants" not in tables:
        op.create_table(
            "ai_context_grants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("ai_orchestration_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("session_id", "user_id", "expires_at"):
        _index(f"ix_ai_context_grants_{column}", "ai_context_grants", [column])
    _index("ix_ai_context_grants_revoked_at", "ai_context_grants", ["revoked_at"])

    tables = _tables()
    if "ai_orchestration_events" not in tables:
        op.create_table(
            "ai_orchestration_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("ai_orchestration_sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("step_id", sa.String(36), sa.ForeignKey("ai_orchestration_steps.id", ondelete="SET NULL"), nullable=True),
            sa.Column("event_type", sa.String(40), nullable=False),
            sa.Column("result_code", sa.String(80), nullable=False, server_default=""),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("session_id", "step_id", "event_type", "created_by"):
        _index(f"ix_ai_orchestration_events_{column}", "ai_orchestration_events", [column])

    tables = _tables()
    if "meeting_import_drafts" not in tables:
        op.create_table(
            "meeting_import_drafts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("source_sha256", sa.String(64), nullable=False),
            sa.Column("source_kind", sa.String(24), nullable=False, server_default="ooxml"),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("proposed_meeting", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("proposed_topics", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("meeting_id", sa.String(36), sa.ForeignKey("business_meetings.id", ondelete="SET NULL"), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("created_by", "source_sha256", "status", "meeting_id", "expires_at"):
        _index(f"ix_meeting_import_drafts_{column}", "meeting_import_drafts", [column])


def downgrade() -> None:
    # 0026 的数据为审计和会话状态，降级前由发布事务负责快照；这里只能
    # 按依赖逆序删除，避免 SQLite 外键约束产生半套结构。
    for table in (
        "meeting_import_drafts",
        "ai_orchestration_events",
        "ai_context_grants",
        "ai_orchestration_approvals",
        "ai_orchestration_steps",
        "ai_orchestration_sessions",
    ):
        if table in _tables():
            op.drop_table(table)
