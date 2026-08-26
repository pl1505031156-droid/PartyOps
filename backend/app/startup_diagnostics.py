"""把启动期底层异常收敛为稳定、可操作且不泄密的诊断。"""

from __future__ import annotations

from collections.abc import Iterator

DATABASE_LOCKED = "DATABASE_LOCKED"
DATABASE_CORRUPT = "DATABASE_CORRUPT"
DATABASE_SCHEMA_FAILED = "DATABASE_SCHEMA_FAILED"
DATABASE_IO_FAILED = "DATABASE_IO_FAILED"
DATA_DIR_FULL = "DATA_DIR_FULL"
DATABASE_STARTUP_FAILED = "DATABASE_STARTUP_FAILED"
UPGRADE_BACKUP_FAILED = "UPGRADE_BACKUP_FAILED"
SQLITE_RUNTIME_FAILED = "SQLITE_RUNTIME_FAILED"


PUBLIC_STARTUP_MESSAGES = {
    DATABASE_LOCKED: "数据库正被其他 PartyOps 进程占用。请关闭旧程序后重试；系统没有改动原数据。",
    DATABASE_CORRUPT: "数据库完整性检查未通过。系统已保留原文件和升级前备份，请打开日志目录获取诊断。",
    DATABASE_SCHEMA_FAILED: "数据库结构升级未完成。系统已回滚到升级前数据，请勿删除数据目录，重新安装后再试。",
    DATABASE_IO_FAILED: "数据库所在磁盘无法可靠读写。请检查磁盘、目录权限和安全软件拦截后重试。",
    DATA_DIR_FULL: "数据目录所在磁盘空间不足。请释放空间后重试。",
    DATABASE_STARTUP_FAILED: "数据库初始化未完成。系统已保留原数据，请打开日志目录并提供诊断编号。",
    UPGRADE_BACKUP_FAILED: "升级前安全备份未完成，系统没有迁移或改动原数据库。请检查磁盘空间、数据目录权限和安全软件拦截后重试。",
    SQLITE_RUNTIME_FAILED: "SQLite 运行时、版本或 FTS5 能力不满足启动要求。系统已保留原数据，请重新安装与当前系统匹配的完整安装包。",
}


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def classify_database_startup_error(exc: BaseException) -> tuple[str, str]:
    """分类 SQLAlchemy/SQLite 启动异常；对外只返回固定中文说明。"""

    combined = " | ".join(str(item) for item in _exception_chain(exc)).lower()
    if "database is locked" in combined or "database table is locked" in combined:
        code = DATABASE_LOCKED
    elif any(marker in combined for marker in ("database disk image is malformed", "file is not a database", "database corrupt")):
        code = DATABASE_CORRUPT
    elif any(
        marker in combined
        for marker in (
            "no such table",
            "no such column",
            "has no column named",
            "duplicate column",
            "already exists",
            "administrator invariant",
        )
    ):
        code = DATABASE_SCHEMA_FAILED
    elif any(marker in combined for marker in ("database or disk is full", "disk full", "no space left")):
        code = DATA_DIR_FULL
    elif any(marker in combined for marker in ("disk i/o error", "readonly database", "unable to open database file", "access is denied", "permission denied")):
        code = DATABASE_IO_FAILED
    elif any(
        marker in combined
        for marker in (
            "no such module: fts5",
            "未启用 fts5",
            "低于生产最低版本",
            "pysqlite",
            "sqlite runtime",
        )
    ):
        code = SQLITE_RUNTIME_FAILED
    else:
        code = DATABASE_STARTUP_FAILED
    return code, PUBLIC_STARTUP_MESSAGES[code]


def public_startup_message(code: str, fallback: str = "") -> str:
    """返回向导可直接展示的简洁说明，未知码不回显原始 traceback。"""

    return PUBLIC_STARTUP_MESSAGES.get(code, fallback or "主机进程启动失败，请打开日志目录查看诊断。")
