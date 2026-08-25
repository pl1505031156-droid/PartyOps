"""初始数据库结构。

Revision ID: 0001
"""

from alembic import op
from app import models  # noqa: F401
from app.database import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# 0001 历史上直接引用了运行时最新元数据；如果新安装从空库重放，
# 新版本表会被提前创建，随后对应增量迁移会重复建表。这里冻结“后续迁移
# 明确负责创建”的表集合，使历史链可重复执行；已应用 0001 的数据库不受影响。
FUTURE_TABLES = {
    "object_links",
    "activity_events",
    "calendar_preferences",
    "recurrence_exceptions",
    "projection_checkpoints",
    "onboarding_progress",
    "user_appearance_preferences",
    "ai_model_packs",
    "semantic_index_checkpoints",
    "ai_recommendations",
    "workspace_root_members",
    "local_share_actions",
    "ai_model_activations",
    "party_development_profiles",
    "party_development_materials",
    "client_device_credentials",
    "file_open_grants",
    "workflow_templates",
    "business_meetings",
    "meeting_topics",
    "business_documents",
    "business_document_revisions",
    "party_development_cases",
    "party_development_milestones",
    "notification_sources",
    "study_plans",
    "party_development_plan_profiles",
    "meeting_attendees",
    "meeting_actions",
    "study_plan_topics",
    "ledger_import_jobs",
    "ledger_import_mapping_templates",
    "party_development_field_definitions",
    "party_development_progress_events",
    "ledger_import_changes",
}


def upgrade() -> None:
    tables = [
        table
        for name, table in Base.metadata.tables.items()
        if name not in FUTURE_TABLES
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
