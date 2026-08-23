"""1.4.5 党务模块、会议闭环与党员发展参考计划。

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _add_columns(table: str, columns: tuple[sa.Column, ...]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    missing = [column for column in columns if column.name not in existing]
    if not missing:
        return
    # SQLite 不能通过 ALTER TABLE 单独追加外键约束。PartyOps 的正式数据
    # 库是 SQLite，因此含外键的增量列必须一次 batch 重建，不能只在全新
    # create_all 场景下“看起来可用”。
    if op.get_bind().dialect.name == "sqlite" and any(
        column.foreign_keys for column in missing
    ):
        with op.batch_alter_table(table) as batch:
            for column in missing:
                batch.add_column(column)
        return
    for column in missing:
        op.add_column(table, column)


def _create_index(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def _drop_index_if_exists(name: str, table: str) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name in existing:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    _add_columns(
        "login_sessions",
        (sa.Column("csrf_token_hash", sa.String(64), nullable=True),),
    )

    if not _table_exists("study_plans"):
        op.create_table(
            "study_plans",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("organization", sa.String(160), nullable=False),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(240), nullable=False),
            sa.Column("group_leader_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("secretary_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("organization", "year"),
        )
    for column in ("organization", "year", "group_leader_id", "secretary_id", "status", "created_by"):
        _create_index(f"ix_study_plans_{column}", "study_plans", [column])

    if not _table_exists("party_development_plan_profiles"):
        op.create_table(
            "party_development_plan_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("system_key", sa.String(80), nullable=False, unique=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("assumptions", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("system_key", "active", "created_by"):
        _create_index(
            f"ix_party_development_plan_profiles_{column}",
            "party_development_plan_profiles",
            [column],
            unique=column == "system_key",
        )

    _add_columns(
        "workflow_templates",
        (
            sa.Column("system_key", sa.String(80), nullable=False, server_default=""),
            sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("template_version", sa.String(32), nullable=False, server_default="1.0"),
        ),
    )
    _create_index("ix_workflow_templates_system_key", "workflow_templates", ["system_key"])
    _create_index("ix_workflow_templates_built_in", "workflow_templates", ["built_in"])

    _add_columns(
        "business_meetings",
        (
            sa.Column(
                "host_id",
                sa.String(36),
                sa.ForeignKey("users.id", name="fk_business_meetings_host_id_users"),
                nullable=True,
            ),
            sa.Column(
                "recorder_id",
                sa.String(36),
                sa.ForeignKey("users.id", name="fk_business_meetings_recorder_id_users"),
                nullable=True,
            ),
            sa.Column("venue", sa.String(240), nullable=False, server_default=""),
            sa.Column(
                "study_plan_id",
                sa.String(36),
                sa.ForeignKey(
                    "study_plans.id",
                    ondelete="SET NULL",
                    name="fk_business_meetings_study_plan_id_study_plans",
                ),
                nullable=True,
            ),
            sa.Column("business_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        ),
    )
    for column in ("host_id", "recorder_id", "study_plan_id"):
        _create_index(f"ix_business_meetings_{column}", "business_meetings", [column])

    if not _table_exists("meeting_attendees"):
        op.create_table(
            "meeting_attendees",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("meeting_id", sa.String(36), sa.ForeignKey("business_meetings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("display_name", sa.String(120), nullable=False),
            sa.Column("role", sa.String(48), nullable=False, server_default="member"),
            sa.Column("attendance_status", sa.String(24), nullable=False, server_default="expected"),
            sa.Column("voting_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("meeting_id", "user_id", "display_name"),
        )
    for column in ("meeting_id", "user_id", "role", "attendance_status"):
        _create_index(f"ix_meeting_attendees_{column}", "meeting_attendees", [column])

    if not _table_exists("meeting_actions"):
        op.create_table(
            "meeting_actions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("meeting_id", sa.String(36), sa.ForeignKey("business_meetings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.String(240), nullable=False),
            sa.Column("responsible_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    for column in ("meeting_id", "responsible_user_id", "due_at", "task_id", "status", "created_by"):
        _create_index(f"ix_meeting_actions_{column}", "meeting_actions", [column])

    if not _table_exists("study_plan_topics"):
        op.create_table(
            "study_plan_topics",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("plan_id", sa.String(36), sa.ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quarter", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(240), nullable=False),
            sa.Column("learning_materials", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("research_topic", sa.Text(), nullable=False, server_default=""),
            sa.Column("conversion_goal", sa.Text(), nullable=False, server_default=""),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index("ix_study_plan_topics_plan_id", "study_plan_topics", ["plan_id"])
    _create_index("ix_study_plan_topics_quarter", "study_plan_topics", ["quarter"])

    _add_columns(
        "party_development_cases",
        (
            sa.Column(
                "planning_profile_id",
                sa.String(36),
                sa.ForeignKey(
                    "party_development_plan_profiles.id",
                    ondelete="SET NULL",
                    name=(
                        "fk_party_development_cases_planning_profile_id_"
                        "party_development_plan_profiles"
                    ),
                ),
                nullable=True,
            ),
            sa.Column("planning_profile_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        ),
    )
    _create_index("ix_party_development_cases_planning_profile_id", "party_development_cases", ["planning_profile_id"])
    _add_columns(
        "party_development_milestones",
        (sa.Column("planning_basis", sa.Text(), nullable=False, server_default=""),),
    )


def downgrade() -> None:
    # SQLite 直接 DROP 带外键的列会留下无效表定义，必须使用 Alembic
    # batch 模式重建表。降级虽不是常规发布路径，仍需保证可回滚和数据保留。
    with op.batch_alter_table("party_development_milestones") as batch:
        batch.drop_column("planning_basis")
    _drop_index_if_exists(
        "ix_party_development_cases_planning_profile_id",
        "party_development_cases",
    )
    with op.batch_alter_table("party_development_cases") as batch:
        batch.drop_column("planning_profile_snapshot")
        batch.drop_column("planning_profile_id")
    for table in ("study_plan_topics", "meeting_actions", "meeting_attendees"):
        op.drop_table(table)
    for column in ("host_id", "recorder_id", "study_plan_id"):
        _drop_index_if_exists(
            f"ix_business_meetings_{column}", "business_meetings"
        )
    with op.batch_alter_table("business_meetings") as batch:
        for column in (
            "business_data",
            "study_plan_id",
            "venue",
            "recorder_id",
            "host_id",
        ):
            batch.drop_column(column)
    for column in ("system_key", "built_in"):
        _drop_index_if_exists(
            f"ix_workflow_templates_{column}", "workflow_templates"
        )
    with op.batch_alter_table("workflow_templates") as batch:
        for column in ("template_version", "built_in", "system_key"):
            batch.drop_column(column)
    op.drop_table("party_development_plan_profiles")
    op.drop_table("study_plans")
    with op.batch_alter_table("login_sessions") as batch:
        batch.drop_column("csrf_token_hash")
