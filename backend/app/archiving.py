"""任务逻辑归档目录：生成索引，不重复复制附件。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    ArchiveSnapshot,
    AttachmentVersion,
    FileBlob,
    MaterialItem,
    Task,
    User,
    utcnow,
)
from .schemas import serialize_api_datetime


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return cleaned[:80] or "未命名"


def create_archive_snapshot(
    db: Session, task: Task, actor: User
) -> ArchiveSnapshot:
    """写入可重建的规范目录索引；附件仍由哈希实体统一保存。"""

    rows: list[dict[str, object]] = []
    materials = db.scalars(
        select(MaterialItem)
        .where(MaterialItem.task_id == task.id)
        .order_by(MaterialItem.category, MaterialItem.created_at)
    ).all()
    for material in materials:
        versions = db.execute(
            select(AttachmentVersion, FileBlob)
            .join(FileBlob, FileBlob.sha256 == AttachmentVersion.blob_sha256)
            .where(AttachmentVersion.material_item_id == material.id)
            .order_by(AttachmentVersion.version_no)
        ).all()
        rows.append(
            {
                "material_id": material.id,
                "category": material.category,
                "name": material.name,
                "required": material.required,
                "not_applicable": material.not_applicable,
                "not_applicable_reason": material.not_applicable_reason,
                "versions": [
                    {
                        "id": version.id,
                        "version_no": version.version_no,
                        "stage": version.stage.value,
                        "final": version.is_final,
                        "filename": version.display_name or blob.original_name,
                        "sha256": blob.sha256,
                        "size_bytes": blob.size_bytes,
                    }
                    for version, blob in versions
                ],
            }
        )
    children = db.scalars(
        select(Task)
        .where(Task.parent_task_id == task.id, Task.deleted_at.is_(None))
        .order_by(Task.created_at)
    ).all()
    manifest: dict[str, object] = {
        "format": "partyops-task-archive-index",
        "version": 1,
        "generated_at": serialize_api_datetime(utcnow()),
        "task": {
            "id": task.id,
            "title": task.title,
            "category": task.category,
            "status": task.status.value,
            "owner_id": task.owner_id,
            "source": task.source,
            "formal_due_at": serialize_api_datetime(task.formal_due_at)
            if task.formal_due_at
            else None,
            "internal_due_at": serialize_api_datetime(task.internal_due_at)
            if task.internal_due_at
            else None,
            "task_version": task.version,
        },
        "subtasks": [
            {
                "id": child.id,
                "title": child.title,
                "status": child.status.value,
                "owner_id": child.owner_id,
            }
            for child in children
        ],
        "materials": rows,
    }
    year = (task.archived_at or utcnow()).year
    directory = get_settings().archives_dir / str(year) / (
        f"{_safe_name(task.title)}-{task.id[:8]}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"目录索引-v{task.version}.json"
    fd, temp_name = tempfile.mkstemp(prefix="index-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    snapshot = ArchiveSnapshot(
        task_id=task.id,
        task_version=task.version,
        relative_index_path=target.relative_to(get_settings().archives_dir).as_posix(),
        manifest=manifest,
        created_by=actor.id,
    )
    db.add(snapshot)
    return snapshot
