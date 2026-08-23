"""业务附件回收恢复与本地模型资源画像。

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_attachment_recovery_columns(table: str, *, include_final: bool) -> None:
    existing = _columns(table)
    additions: list[sa.Column] = []
    definitions = [
        sa.Column("client_upload_id", sa.String(80), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.String(36), nullable=True),
        sa.Column("delete_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    ]
    if include_final:
        definitions.append(
            sa.Column(
                "deleted_was_final",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    for column in definitions:
        if column.name not in existing:
            additions.append(column)
    if additions:
        with op.batch_alter_table(table) as batch:
            for column in additions:
                batch.add_column(column)
            if "deleted_by" not in existing:
                batch.create_foreign_key(
                    f"fk_{table}_deleted_by_users",
                    "users",
                    ["deleted_by"],
                    ["id"],
                    ondelete="SET NULL",
                )
    existing_indexes = _indexes(table)
    for column in ("client_upload_id", "deleted_at", "deleted_by", "purge_after"):
        name = f"ix_{table}_{column}"
        if name not in existing_indexes:
            op.create_index(
                name,
                table,
                [column],
                unique=column == "client_upload_id",
            )


def upgrade() -> None:
    _add_attachment_recovery_columns("attachment_versions", include_final=True)
    _add_attachment_recovery_columns("archive_attachments", include_final=False)


def downgrade() -> None:
    for table, include_final in (
        ("archive_attachments", False),
        ("attachment_versions", True),
    ):
        for column in ("client_upload_id", "deleted_at", "deleted_by", "purge_after"):
            name = f"ix_{table}_{column}"
            if name in _indexes(table):
                op.drop_index(name, table_name=table)
        columns = [
            "purge_after",
            "delete_reason",
            "deleted_by",
            "deleted_at",
            "client_upload_id",
        ]
        if include_final:
            columns.insert(0, "deleted_was_final")
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.drop_column(column)
