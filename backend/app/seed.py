"""首批高频模板与开发演示数据。"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .enums import ArchiveAccessMode, ArchiveRecordMode, TaskType, UserRole
from .models import (
    ArchiveCategory,
    Task,
    TaskTemplate,
    TemplateMaterial,
    TemplateStep,
    User,
    utcnow,
)
from .schemas import MaterialInput, TaskCreate
from .security import hash_password
from .task_service import create_task


TEMPLATE_DEFINITIONS = [
    (
        "主题党日或集中学习",
        ["明确主题与时间", "组织签到和学习", "整理记录与照片"],
        [("notice", "活动通知"), ("signin", "签到表"), ("record", "活动记录"), ("photo", "活动照片")],
    ),
    (
        "党员教育培训",
        ["制定培训安排", "组织培训", "整理培训档案"],
        [("plan", "培训方案"), ("signin", "签到表"), ("record", "培训记录")],
    ),
    (
        "基层党组织换届",
        ["核定工作安排", "准备会议材料", "形成结果材料"],
        [("plan", "换届方案"), ("list", "人员名单"), ("record", "会议记录"), ("final", "报送终稿")],
    ),
    (
        "党代会筹备",
        ["拟定筹备方案", "核验代表与会议材料", "组织会议并形成结果"],
        [("plan", "筹备方案"), ("list", "代表名单"), ("record", "会议记录"), ("final", "结果报告")],
    ),
    (
        "党建检查或年度考核",
        ["对照检查清单", "归集佐证材料", "核验并报送"],
        [("notice", "检查通知"), ("checklist", "检查清单"), ("final", "报送终稿"), ("receipt", "报送回执")],
    ),
    (
        "意识形态分析研判",
        ["归集情况", "组织分析研判", "形成审定材料"],
        [("record", "研判记录"), ("draft", "分析材料"), ("final", "审定稿")],
    ),
    (
        "党务公开",
        ["确认公开事项", "履行审核程序", "公开并留存佐证"],
        [("notice", "公开内容"), ("record", "审核记录"), ("photo", "公开佐证")],
    ),
    (
        "驻村帮扶材料报送",
        ["确认报送要求", "汇总驻村材料", "审核并报送"],
        [("notice", "原始通知"), ("list", "汇总清单"), ("final", "实际报送稿"), ("receipt", "回执")],
    ),
    (
        "一般报表或信息报送",
        ["确认口径和时限", "汇总填报", "审核报送"],
        [("notice", "原始通知"), ("draft", "填报初稿"), ("final", "实际报送稿"), ("receipt", "回执")],
    ),
]


ARCHIVE_CATEGORY_DEFINITIONS = [
    {
        "name": "人事调动文件",
        "code": "personnel_transfer",
        "description": "组织人事调动、任免和岗位变动文件。",
        "record_mode": ArchiveRecordMode.DOCUMENT,
        "field_schema": [],
    },
    {
        "name": "事业编年度考核",
        "code": "public_institution_assessment",
        "description": "事业编人员年度考核一人一档。",
        "record_mode": ArchiveRecordMode.PERSON_YEAR,
        "field_schema": [
            {"key": "assessment_result", "label": "考核等次", "type": "select", "required": False,
             "options": ["优秀", "合格", "基本合格", "不合格", "未定等次"]},
        ],
    },
    {
        "name": "公务员年度考核",
        "code": "civil_servant_assessment",
        "description": "公务员年度考核一人一档。",
        "record_mode": ArchiveRecordMode.PERSON_YEAR,
        "field_schema": [
            {"key": "assessment_result", "label": "考核等次", "type": "select", "required": False,
             "options": ["优秀", "称职", "基本称职", "不称职", "未定等次"]},
        ],
    },
    {
        "name": "其他重要文件",
        "code": "other_important",
        "description": "管理员可按需要补充字段和目录规则。",
        "record_mode": ArchiveRecordMode.DOCUMENT,
        "field_schema": [],
    },
]


def seed_archive_categories(db: Session, actor: User) -> None:
    """为新主机和旧版升级库补齐首批重要档案类别。"""

    for definition in ARCHIVE_CATEGORY_DEFINITIONS:
        category = db.scalar(
            select(ArchiveCategory).where(ArchiveCategory.code == definition["code"])
        )
        if category:
            continue
        db.add(
            ArchiveCategory(
                **definition,
                access_mode=ArchiveAccessMode.ALL_USERS,
                allow_device_access=True,
                built_in=True,
                created_by=actor.id,
            )
        )
    db.commit()


def seed_templates(db: Session, actor: User) -> None:
    for name, steps, materials in TEMPLATE_DEFINITIONS:
        if db.scalar(select(TaskTemplate).where(TaskTemplate.name == name)):
            continue
        template = TaskTemplate(
            name=name,
            category="党建工作",
            task_type=TaskType.STANDARD,
            description=f"{name}高频工作模板，可按实际情况调整。",
            created_by=actor.id,
        )
        db.add(template)
        db.flush()
        for index, title in enumerate(steps):
            db.add(TemplateStep(template_id=template.id, title=title, sort_order=index))
        for category, material_name in materials:
            db.add(
                TemplateMaterial(
                    template_id=template.id,
                    category=category,
                    name=material_name,
                    required=True,
                )
            )
    db.commit()
    seed_archive_categories(db, actor)


def seed_demo_data(db: Session, admin: User) -> None:
    staff = db.scalar(select(User).where(User.username == "xietong"))
    if not staff:
        staff = User(
            username="xietong",
            display_name="协同人员",
            # 演示账号仅用于承载示例协作关系，不发布通用口令。管理员如需
            # 实际登录该账号，必须从用户管理中主动重置密码。
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=UserRole.STAFF,
        )
        db.add(staff)
        db.commit()
        db.refresh(staff)
    seed_templates(db, admin)
    existing = db.scalar(select(func.count()).select_from(Task))
    if existing:
        return
    create_task(
        db,
        TaskCreate(
            title="七月主题党日材料归档",
            source="党建办月度工作安排",
            owner_id=admin.id,
            collaborator_ids=[staff.id],
            internal_due_at=utcnow() + timedelta(days=1),
            formal_due_at=utcnow() + timedelta(days=3),
            materials=[
                MaterialInput(category="notice", name="活动通知", required=True),
                MaterialInput(category="signin", name="签到表", required=True),
                MaterialInput(category="photo", name="活动照片", required=True),
            ],
        ),
        admin,
    )
    create_task(
        db,
        TaskCreate(
            title="季度党建工作台账报送",
            source="上级工作群通知",
            owner_id=staff.id,
            reviewer_id=admin.id,
            internal_due_at=utcnow() + timedelta(days=2),
            formal_due_at=utcnow() + timedelta(days=5),
        ),
        admin,
    )
