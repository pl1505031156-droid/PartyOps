"""1.4.4 协同凭据、文件授权与党建业务增量模型。

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _add_columns(table: str, columns: tuple[sa.Column, ...]) -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def _create_index_if_missing(table: str, name: str, columns: list[str]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns)


def upgrade() -> None:
    _add_columns(
        "users",
        (
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(length=36), nullable=True),
        ),
    )
    _add_columns(
        "devices",
        (
            sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("credential_state", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("credential_rotated_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    _add_columns(
        "notifications",
        (
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        ),
    )
    op.execute(sa.text("UPDATE notifications SET updated_at = created_at WHERE updated_at IS NULL"))

    op.create_table(
        "client_device_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("state", sa.String(24), nullable=False, server_default="active"),
        sa.Column("replaced_by_id", sa.String(36), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_client_device_credentials_device_id", "client_device_credentials", ["device_id"])
    op.create_index("ix_client_device_credentials_token_hash", "client_device_credentials", ["token_hash"], unique=True)
    op.create_index("ix_client_device_credentials_state", "client_device_credentials", ["state"])

    op.create_table(
        "file_open_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("file_id", sa.String(36), sa.ForeignKey("workspace_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("open_method", sa.String(24), nullable=False, server_default="local_helper"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in (
        ("ix_file_open_grants_token_hash", ["token_hash"]),
        ("ix_file_open_grants_file_id", ["file_id"]),
        ("ix_file_open_grants_created_by", ["created_by"]),
        ("ix_file_open_grants_expires_at", ["expires_at"]),
        ("ix_file_open_grants_used_at", ["used_at"]),
        ("ix_file_open_grants_revoked_at", ["revoked_at"]),
    ):
        op.create_index(name, "file_open_grants", columns, unique=name.endswith("token_hash"))

    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False, unique=True),
        sa.Column("business_type", sa.String(48), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("recurrence", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_templates_name", "workflow_templates", ["name"], unique=True)
    op.create_index("ix_workflow_templates_business_type", "workflow_templates", ["business_type"])
    op.create_index("ix_workflow_templates_active", "workflow_templates", ["active"])
    op.create_index("ix_workflow_templates_created_by", "workflow_templates", ["created_by"])

    op.create_table(
        "business_meetings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_type", sa.String(48), nullable=False),
        sa.Column("organization", sa.String(160), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="planned"),
        sa.Column("workflow_template_id", sa.String(36), sa.ForeignKey("workflow_templates.id"), nullable=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("recurrence_key", sa.String(120), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("meeting_type", "organization", "scheduled_at", "completed_at", "status", "workflow_template_id", "task_id", "recurrence_key", "created_by"):
        op.create_index(f"ix_business_meetings_{column}", "business_meetings", [column])
    op.create_index(
        "uq_business_meetings_recurrence_key",
        "business_meetings",
        ["recurrence_key"],
        unique=True,
        sqlite_where=sa.text("recurrence_key <> ''"),
    )

    op.create_table(
        "meeting_topics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("business_meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("review_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("amount_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_meeting_topics_meeting_id", "meeting_topics", ["meeting_id"])
    op.create_index("ix_meeting_topics_reviewed", "meeting_topics", ["reviewed"])

    op.create_table(
        "business_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meeting_id", sa.String(36), sa.ForeignKey("business_meetings.id", ondelete="CASCADE"), nullable=True),
        sa.Column("task_step_id", sa.String(36), sa.ForeignKey("task_steps.id", ondelete="SET NULL"), nullable=True),
        sa.Column("document_type", sa.String(48), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("meeting_id", "task_step_id", "document_type", "archived_at", "created_by"):
        op.create_index(f"ix_business_documents_{column}", "business_documents", [column])

    op.create_table(
        "business_document_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("business_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "revision_no"),
    )
    op.create_index("ix_business_document_revisions_document_id", "business_document_revisions", ["document_id"])
    op.create_index("ix_business_document_revisions_created_by", "business_document_revisions", ["created_by"])

    op.create_table(
        "party_development_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("party_committee", sa.String(160), nullable=False),
        sa.Column("party_branch", sa.String(160), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("gender", sa.String(16), nullable=False, server_default=""),
        sa.Column("ethnicity", sa.String(40), nullable=False, server_default=""),
        sa.Column("birth_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("education", sa.String(80), nullable=False, server_default=""),
        sa.Column("application_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activist_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_contacts", sa.JSON(), nullable=False),
        sa.Column("introducers", sa.JSON(), nullable=False),
        sa.Column("development_object_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probationary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.String(48), nullable=False, server_default="application"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("rule_version", sa.String(32), nullable=False, server_default="2026.05"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("party_committee", "party_branch", "name", "application_at", "stage", "status", "created_by"):
        op.create_index(f"ix_party_development_cases_{column}", "party_development_cases", [column])

    op.create_table(
        "party_development_milestones",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("party_development_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("milestone_type", sa.String(48), nullable=False),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_earliest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_version", sa.String(32), nullable=False, server_default="2026.05"),
        sa.Column("legal_basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan_kind", sa.String(24), nullable=False, server_default="reference"),
        sa.Column("reminder_days", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "milestone_type"),
    )
    op.create_index("ix_party_development_milestones_case_id", "party_development_milestones", ["case_id"])
    op.create_index("ix_party_development_milestones_milestone_type", "party_development_milestones", ["milestone_type"])
    op.create_index("ix_party_development_milestones_planned_at", "party_development_milestones", ["planned_at"])

    op.create_table(
        "notification_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("notification_id", sa.String(36), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("rule_type", sa.String(48), nullable=False),
        sa.Column("window_key", sa.String(48), nullable=False, server_default=""),
        sa.Column("source_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("notification_id", "source_type", "source_id", "rule_type"):
        op.create_index(f"ix_notification_sources_{column}", "notification_sources", [column], unique=column == "notification_id")

    _create_index_if_missing("users", "ix_users_archived_at", ["archived_at"])
    _create_index_if_missing(
        "devices", "ix_devices_credential_state", ["credential_state"]
    )
    _create_index_if_missing(
        "notifications", "ix_notifications_revoked_at", ["revoked_at"]
    )


def downgrade() -> None:
    for table in (
        "notification_sources",
        "party_development_milestones",
        "party_development_cases",
        "business_document_revisions",
        "business_documents",
        "meeting_topics",
        "business_meetings",
        "workflow_templates",
        "file_open_grants",
        "client_device_credentials",
    ):
        op.drop_table(table)
    op.drop_index("ix_notifications_revoked_at", table_name="notifications")
    op.drop_index("ix_devices_credential_state", table_name="devices")
    op.drop_index("ix_users_archived_at", table_name="users")
    op.drop_column("notifications", "updated_at")
    op.drop_column("notifications", "revoked_at")
    op.drop_column("devices", "credential_rotated_at")
    op.drop_column("devices", "credential_state")
    op.drop_column("devices", "protocol_version")
    op.drop_column("users", "archived_by")
    op.drop_column("users", "archived_at")
