"""100,000 个文件索引下的搜索与目录首屏性能冒烟测试。"""

from __future__ import annotations

import math
import statistics
import time

from sqlalchemy import select

from app.database import db_runtime
from app.enums import FileIndexStatus, UserRole
from app.models import User, WorkspaceFile, WorkspaceRoot
from app.security import hash_password
from app.workspace import search_workspace_files


def percentile(values: list[float], ratio: float = 0.95) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * ratio) - 1))
    return ordered[index]


def main() -> None:
    db_runtime.create_schema()
    with db_runtime.session_factory() as db:
        admin = User(
            username="workspace-perf-admin",
            display_name="文件性能测试管理员",
            password_hash=hash_password("PartyOps@2026"),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.flush()
        root = WorkspaceRoot(
            name="十万文件性能目录",
            absolute_path="/partyops/performance-only",
            scan_status="completed",
            file_count=100_000,
            created_by=admin.id,
        )
        db.add(root)
        db.commit()
        db.refresh(root)

        for offset in range(0, 100_000, 2_000):
            rows = [
                {
                    "root_id": root.id,
                    "relative_path": f"{index // 1000:03d}/年度党建材料-{index:06d}.txt",
                    "name": f"年度党建材料-{index:06d}.txt",
                    "is_directory": False,
                    "extension": ".txt",
                    "size_bytes": 1024 + index,
                    "device_id": "perf",
                    "inode": str(index),
                    "mime_type": "text/plain",
                    "status": FileIndexStatus.INDEXED,
                    "extracted_text": f"第 {index} 号基层党建闭环协同工作材料 专项关键词{index:06d}",
                    "ocr_text": "",
                    "version": 1,
                }
                for index in range(offset, offset + 2_000)
            ]
            db.bulk_insert_mappings(WorkspaceFile, rows)
            db.commit()

        # 预热数据库页缓存后再衡量稳定态 p95。
        search_workspace_files(db, "050000", root.id, 100)
        db.scalars(
            select(WorkspaceFile)
            .where(WorkspaceFile.root_id == root.id, WorkspaceFile.parent_id.is_(None))
            .order_by(WorkspaceFile.name)
            .limit(100)
        ).all()

        search_latencies: list[float] = []
        first_page_latencies: list[float] = []
        for _ in range(50):
            started = time.perf_counter()
            results = search_workspace_files(db, "050000", root.id, 100)
            search_latencies.append((time.perf_counter() - started) * 1000)
            assert results and results[0].name == "年度党建材料-050000.txt"

            started = time.perf_counter()
            first_page = db.scalars(
                select(WorkspaceFile)
                .where(
                    WorkspaceFile.root_id == root.id,
                    WorkspaceFile.parent_id.is_(None),
                )
                .order_by(WorkspaceFile.name)
                .limit(100)
            ).all()
            first_page_latencies.append((time.perf_counter() - started) * 1000)
            assert len(first_page) == 100

    print(
        {
            "file_count": 100_000,
            "search_p95_ms": round(percentile(search_latencies), 2),
            "search_mean_ms": round(statistics.mean(search_latencies), 2),
            "first_page_p95_ms": round(percentile(first_page_latencies), 2),
            "first_page_mean_ms": round(statistics.mean(first_page_latencies), 2),
        }
    )


if __name__ == "__main__":
    main()
