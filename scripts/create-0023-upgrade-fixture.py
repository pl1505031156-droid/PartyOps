"""为原生制品覆盖升级门禁创建真实 PartyOps 0023 数据目录。

该工具只允许写入调用方提供的全新空目录。它先用当前迁移链建立 0024，
写入可核验的管理员记录，再通过 Alembic 真实降级到 0023，避免手写近似
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
        command.downgrade(config, "0023")
    settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    attachment = settings.attachments_dir / "preserved.txt"
    attachment.write_text(FIXTURE_ATTACHMENT, encoding="utf-8")
    runtime.dispose()

    with sqlite3.connect(settings.database_path) as database:
        revision = database.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        quick_check = database.execute("PRAGMA quick_check").fetchone()[0]
        columns = {
            row[1] for row in database.execute("PRAGMA table_info(backup_runs)")
        }
    if revision != "0023" or quick_check != "ok" or "deleted_at" in columns:
        raise SystemExit("[UPGRADE_FIXTURE_INVALID] 未建立真实 0023 基线。")
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
