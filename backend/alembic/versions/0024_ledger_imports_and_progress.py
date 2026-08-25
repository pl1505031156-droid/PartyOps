"""通用台账导入与发展党员真实进度。

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_lifecycle_columns() -> None:
    definitions: dict[str, tuple[sa.Column, ...]] = {
        "workflow_templates": (
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "business_meetings": (
            sa.Column("status_before_archive", sa.String(24), nullable=False, server_default="planned"),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "study_plans": (
            sa.Column("status_before_archive", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "business_documents": (
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "meeting_topics": (
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "meeting_attendees": (
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "meeting_actions": (
            sa.Column("task_status_before_archive", sa.String(24), nullable=False, server_default=""),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "work_journal_entries": (
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_by", sa.String(36), nullable=True),
            sa.Column("archive_reason", sa.Text(), nullable=False, server_default=""),
        ),
        "backup_runs": (
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_by", sa.String(36), nullable=True),
            sa.Column("delete_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ),
    }
    for table, columns in definitions.items():
        existing = _columns(table)
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column.name not in existing:
                    batch.add_column(column)
        existing = _columns(table)
        for indexed_column in ("archived_at", "deleted_at", "purge_after"):
            if indexed_column in existing:
                index_name = f"ix_{table}_{indexed_column}"
                if index_name not in _indexes(table):
                    op.create_index(index_name, table, [indexed_column])


def upgrade() -> None:
    _add_lifecycle_columns()
    op.create_table(
        "ledger_import_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=True),
        sa.Column("source_format", sa.String(16), nullable=False, server_default=""),
        sa.Column("sheet_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("header_row", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="inspected"),
        sa.Column("mapping", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("profile", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("validation", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "target_type", "target_id", "status", "expires_at", "committed_at",
        "undone_at", "created_by",
    ):
        op.create_index(f"ix_ledger_import_jobs_{column}", "ledger_import_jobs", [column])

    op.create_table(
        "ledger_import_mapping_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("header_signature", sa.String(64), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "target_type", "target_id", "header_signature", "created_by",
            name="uq_ledger_import_mapping_target_headers_user",
        ),
    )
    for column in ("target_type", "target_id", "header_signature", "active", "created_by"):
        op.create_index(
            f"ix_ledger_import_mapping_templates_{column}",
            "ledger_import_mapping_templates",
            [column],
        )

    op.create_table(
        "party_development_field_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("field_type", sa.String(24), nullable=False, server_default="text"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("options", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("key", "active", "created_by"):
        op.create_index(
            f"ix_party_development_field_definitions_{column}",
            "party_development_field_definitions",
            [column],
            unique=column == "key",
        )

    case_columns = _columns("party_development_cases")
    with op.batch_alter_table("party_development_cases") as batch:
        if "extra_fields" not in case_columns:
            batch.add_column(sa.Column("extra_fields", sa.JSON(), nullable=False, server_default="{}"))
        if "import_batch_id" not in case_columns:
            batch.add_column(sa.Column("import_batch_id", sa.String(36), nullable=True))
    if "ix_party_development_cases_import_batch_id" not in _indexes("party_development_cases"):
        op.create_index(
            "ix_party_development_cases_import_batch_id",
            "party_development_cases",
            ["import_batch_id"],
        )

    archive_columns = _columns("archive_records")
    with op.batch_alter_table("archive_records") as batch:
        if "import_batch_id" not in archive_columns:
            batch.add_column(sa.Column("import_batch_id", sa.String(36), nullable=True))
    if "ix_archive_records_import_batch_id" not in _indexes("archive_records"):
        op.create_index(
            "ix_archive_records_import_batch_id", "archive_records", ["import_batch_id"]
        )

    op.create_table(
        "party_development_progress_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "case_id", sa.String(36),
            sa.ForeignKey("party_development_cases.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("milestone_type", sa.String(48), nullable=False),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_entity_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("source_entity_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="confirmed"),
        sa.Column(
            "supersedes_event_id", sa.String(36),
            sa.ForeignKey("party_development_progress_events.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "import_job_id", sa.String(36),
            sa.ForeignKey("ledger_import_jobs.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in (
        "case_id", "milestone_type", "actual_at", "source_entity_id", "status",
        "supersedes_event_id", "import_job_id", "created_by", "voided_at",
    ):
        op.create_index(
            f"ix_party_development_progress_events_{column}",
            "party_development_progress_events",
            [column],
        )
    op.create_index(
        "ix_party_development_progress_case_type_status",
        "party_development_progress_events",
        ["case_id", "milestone_type", "status"],
    )

    op.create_table(
        "ledger_import_changes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id", sa.String(36),
            sa.ForeignKey("ledger_import_jobs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("after_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("new_field_keys", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id", "row_number", "entity_type", "entity_id",
            name="uq_ledger_import_change_job_row_entity",
        ),
    )
    for column in ("job_id", "entity_type", "entity_id", "status"):
        op.create_index(f"ix_ledger_import_changes_{column}", "ledger_import_changes", [column])
    op.create_index(
        "ix_ledger_import_change_entity",
        "ledger_import_changes",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_table("ledger_import_changes")
    op.drop_table("party_development_progress_events")
    if "ix_archive_records_import_batch_id" in _indexes("archive_records"):
        op.drop_index("ix_archive_records_import_batch_id", table_name="archive_records")
    with op.batch_alter_table("archive_records") as batch:
        if "import_batch_id" in _columns("archive_records"):
            batch.drop_column("import_batch_id")
    if "ix_party_development_cases_import_batch_id" in _indexes("party_development_cases"):
        op.drop_index(
            "ix_party_development_cases_import_batch_id",
            table_name="party_development_cases",
        )
    with op.batch_alter_table("party_development_cases") as batch:
        existing = _columns("party_development_cases")
        if "import_batch_id" in existing:
            batch.drop_column("import_batch_id")
        if "extra_fields" in existing:
            batch.drop_column("extra_fields")
    op.drop_table("party_development_field_definitions")
    op.drop_table("ledger_import_mapping_templates")
    op.drop_table("ledger_import_jobs")
    lifecycle_columns = {
        "backup_runs": (
            "version", "purge_after", "delete_reason", "deleted_by", "deleted_at",
        ),
        "work_journal_entries": ("archive_reason", "archived_by", "archived_at"),
        "meeting_actions": (
            "archive_reason", "archived_by", "archived_at", "task_status_before_archive",
        ),
        "meeting_attendees": ("archive_reason", "archived_by", "archived_at"),
        "meeting_topics": ("archive_reason", "archived_by", "archived_at"),
        "business_documents": ("archive_reason", "archived_by"),
        "study_plans": ("archive_reason", "archived_by", "archived_at", "status_before_archive"),
        "business_meetings": ("archive_reason", "archived_by", "archived_at", "status_before_archive"),
        "workflow_templates": ("archive_reason", "archived_by", "archived_at"),
    }
    for table, columns in lifecycle_columns.items():
        indexes = _indexes(table)
        for indexed_column in ("archived_at", "deleted_at", "purge_after"):
            index_name = f"ix_{table}_{indexed_column}"
            if index_name in indexes:
                op.drop_index(index_name, table_name=table)
        existing = _columns(table)
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column in existing:
                    batch.drop_column(column)
