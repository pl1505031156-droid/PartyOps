"""为原生制品覆盖升级门禁创建真实 PartyOps 0023/0025 数据目录。

该工具只允许写入调用方提供的全新空目录。它先用当前迁移链建立 0026，
写入可核验的管理员记录，再通过 Alembic 真实降级到指定旧版本，避免手写近似
表结构掩盖迁移兼容问题。输出仅包含测试标识、版本和完整性摘要。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

FIXTURE_USER_ID = "rc4-native-upgrade-admin"
FIXTURE_DISPLAY_NAME = "原生覆盖升级管理员"
FIXTURE_ATTACHMENT = "rc4 原生覆盖升级必须保留附件"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--target-revision",
        choices=("0023", "0025"),
        default="0023",
        help="建立的真实旧数据库版本；默认保持既有 0023 契约。",
    )
    args = parser.parse_args()
    data_root = args.data_root.resolve()
    repo_root = args.repo_root.resolve()
    if data_root.exists() and any(data_root.iterdir()):
        raise SystemExit("[UPGRADE_FIXTURE_NOT_EMPTY] 数据目录必须为空。")
    data_root.mkdir(parents=True, exist_ok=True)

    os.environ["PARTYOPS_DATA_DIR"] = str(data_root)
    os.environ["PARTYOPS_ENVIRONMENT"] = "test"
    sys.path.insert(0, str(repo_root / "backend"))

    from alembic import command
    from app.config import Settings
    from app.database import DatabaseRuntime
    from app.enums import UserRole
    from app.models import User

    settings = Settings(data_dir=data_root, environment="test", strict_sqlite=False)
    runtime = DatabaseRuntime(settings)
    runtime.create_schema()
    with runtime.session_factory() as db:
        db.add(
            User(
                id=FIXTURE_USER_ID,
                username="rc4-native-admin",
                display_name=FIXTURE_DISPLAY_NAME,
                password_hash="test-only",
                role=UserRole.ADMIN,
                active=True,
            )
        )
        db.commit()
    config = runtime._alembic_config()
    with runtime.engine.begin() as connection:
        config.attributes["connection"] = connection
        # create_schema 会建立当前 ORM 覆盖的表并标记为 0026，但某些纯迁移
        # 审计表不属于 ORM。先真实降到 0023，再按需前向执行 0024/0025，
        # 才能得到包含北京时间迁移审计的真实 0025，而不是近似结构。
        command.downgrade(config, "0023")
        if args.target_revision == "0025":
            command.upgrade(config, "0025")
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    attachment = settings.attachments_dir / "preserved.txt"
    attachment.write_text(FIXTURE_ATTACHMENT, encoding="utf-8")
    runtime.dispose()

    with sqlite3.connect(settings.database_path) as database:
        revision = database.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        quick_check = database.execute("PRAGMA quick_check").fetchone()[0]
        backup_columns = {
            row[1] for row in database.execute("PRAGMA table_info(backup_runs)")
        }
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    version_shape_ok = (
        args.target_revision == "0023"
        and "deleted_at" not in backup_columns
        and "timezone_migration_audits" not in tables
    ) or (
        args.target_revision == "0025"
        and "deleted_at" in backup_columns
        and "timezone_migration_audits" in tables
        and "ai_orchestration_sessions" not in tables
    )
    if revision != args.target_revision or quick_check != "ok" or not version_shape_ok:
        raise SystemExit(
            f"[UPGRADE_FIXTURE_INVALID] 未建立真实 {args.target_revision} 基线。"
        )
    print(
        json.dumps(
            {
                "schema_revision": revision,
                "quick_check": quick_check,
                "database_sha256": _sha256(settings.database_path),
                "attachment_sha256": _sha256(attachment),
                "fixture_user_id": FIXTURE_USER_ID,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
