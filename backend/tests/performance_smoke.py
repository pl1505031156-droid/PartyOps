"""10,000 条事项下的本地性能冒烟测试；不属于日常单元测试。"""

from __future__ import annotations

import math
import statistics
import time
from datetime import timedelta

from sqlalchemy import text

from app.database import db_runtime
from app.enums import Priority, Sensitivity, TaskStatus, TaskType, UserRole
from app.models import Task, User, utcnow
from app.security import hash_password
from app.task_service import dashboard


def percentile(values: list[float], ratio: float = 0.95) -> float:
    ordered = sorted(values)
    # 最近秩定义：p95 对 20 个样本取第 19 个值，而不是把最大值误当成 p95。
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * ratio) - 1))
    return ordered[index]


def main() -> None:
    db_runtime.create_schema()
    with db_runtime.session_factory() as db:
        admin = User(
            username="perf-admin",
            display_name="性能测试管理员",
            password_hash=hash_password("PartyOps@2026"),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        now = utcnow()
        batch: list[Task] = []
        for index in range(10_000):
            batch.append(
                Task(
                    title=f"性能测试事项 {index:05d} 党建台账",
                    description="用于本地容量与查询延迟验证。",
                    task_type=TaskType.QUICK,
                    status=TaskStatus.IN_PROGRESS,
                    sensitivity=Sensitivity.NORMAL,
                    priority=Priority.NORMAL,
                    source="性能测试",
                    internal_due_at=now + timedelta(days=index % 30),
                    owner_id=admin.id,
                    created_by=admin.id,
                    updated_by=admin.id,
                )
            )
            if len(batch) == 500:
                db.add_all(batch)
                db.commit()
                batch.clear()

        # 首次查询包含 SQLite 页缓存预热，不属于稳定态 SLI；先显式预热一次，
        # 再保留足够样本计算 p95。
        dashboard(db, admin)
        db.execute(
            text(
                "SELECT task_id FROM task_search_fts "
                "WHERE task_search_fts MATCH :query LIMIT 100"
            ),
            {"query": '"党建台账"'},
        ).all()

        dashboard_latencies: list[float] = []
        search_latencies: list[float] = []
        for _ in range(30):
            started = time.perf_counter()
            result = dashboard(db, admin)
            dashboard_latencies.append((time.perf_counter() - started) * 1000)
            assert len(result.buckets) == 8

            started = time.perf_counter()
            rows = db.execute(
                text(
                    "SELECT task_id FROM task_search_fts "
                    "WHERE task_search_fts MATCH :query LIMIT 100"
                ),
                {"query": '"党建台账"'},
            ).all()
            search_latencies.append((time.perf_counter() - started) * 1000)
            assert rows

    print(
        {
            "task_count": 10_000,
            "dashboard_p95_ms": round(percentile(dashboard_latencies), 2),
            "search_p95_ms": round(percentile(search_latencies), 2),
            "dashboard_mean_ms": round(statistics.mean(dashboard_latencies), 2),
            "search_mean_ms": round(statistics.mean(search_latencies), 2),
        }
    )


if __name__ == "__main__":
    main()
