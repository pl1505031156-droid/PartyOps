"""SQLite 与 SQLAlchemy 运行时。"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings
from .problems import ProblemException

try:
    import pysqlite3 as sqlite3_dbapi
except ImportError:  # 开发环境可使用 Python 自带 SQLite；生产包强制包含静态版本。
    import sqlite3 as sqlite3_dbapi


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


class DatabaseRuntime:
    """持有数据库引擎、会话工厂和进程内写锁。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_directories()
        self.write_lock = threading.RLock()
        self._activity_condition = threading.Condition()
        self._active_sessions = 0
        self._maintenance_active = False
        self._maintenance_owner: int | None = None
        self.engine = self._build_engine(self.settings.database_path)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
            class_=Session,
            info={"partyops_database_runtime": self},
        )

    def enter_session_activity(self, session: Session) -> None:
        """登记一次数据库事务，并在恢复换库期间拒绝新事务。"""

        if session.info.get("partyops_activity_state"):
            return
        current_thread = threading.get_ident()
        with self._activity_condition:
            if self._maintenance_active:
                if self._maintenance_owner == current_thread:
                    session.info["partyops_activity_state"] = "maintenance_owner"
                    return
                raise ProblemException(
                    503,
                    "DATABASE_MAINTENANCE",
                    "系统正在恢复数据",
                    "数据库处于独占维护状态，请稍后重试。",
                    headers={"Retry-After": "5"},
                )
            self._active_sessions += 1
            session.info["partyops_activity_state"] = "active"

    def leave_session_activity(self, session: Session) -> None:
        """事务结束后释放活动计数，唤醒等待恢复的维护线程。"""

        state = session.info.pop("partyops_activity_state", None)
        if state != "active":
            return
        with self._activity_condition:
            self._active_sessions = max(0, self._active_sessions - 1)
            self._activity_condition.notify_all()

    @contextmanager
    def exclusive_maintenance(self, timeout_seconds: float = 30.0):
        """拒绝新事务并等待所有在途事务结束后进入独占维护。"""

        owner = threading.get_ident()
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        with self._activity_condition:
            if self._maintenance_active:
                raise ProblemException(
                    409,
                    "DATABASE_MAINTENANCE_ACTIVE",
                    "数据维护正在进行",
                    "已有恢复或维护任务占用数据库，请等待其完成。",
                )
            self._maintenance_active = True
            self._maintenance_owner = owner
            while self._active_sessions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._maintenance_active = False
                    self._maintenance_owner = None
                    self._activity_condition.notify_all()
                    raise ProblemException(
                        503,
                        "DATABASE_MAINTENANCE_BUSY",
                        "无法进入独占维护",
                        "仍有业务事务未结束，未执行数据库恢复，请稍后重试。",
                    )
                self._activity_condition.wait(remaining)
        try:
            yield
        finally:
            with self._activity_condition:
                self._maintenance_active = False
                self._maintenance_owner = None
                self._activity_condition.notify_all()

    @staticmethod
    def _build_engine(path: Path) -> Engine:
        engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 5},
            module=sqlite3_dbapi,
            future=True,
        )

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.close()

        return engine

    def validate_capabilities(self) -> dict[str, object]:
        with self.engine.connect() as connection:
            version = str(connection.execute(text("select sqlite_version()")).scalar_one())
            options = {
                row[0]
                for row in connection.exec_driver_sql("PRAGMA compile_options").fetchall()
            }
        fts5 = any("ENABLE_FTS5" in option for option in options)
        safe_version = _version_tuple(version) >= _version_tuple(
            self.settings.sqlite_min_version
        )
        if self.settings.strict_sqlite and not safe_version:
            raise RuntimeError(
                f"SQLite {version} 低于生产最低版本 {self.settings.sqlite_min_version}"
            )
        if not fts5:
            raise RuntimeError("当前 SQLite 未启用 FTS5，无法启动全文检索")
        return {"version": version, "safe_version": safe_version, "fts5": fts5}

    def create_schema(self) -> None:
        from . import models  # noqa: F401

        tables = set(inspect(self.engine).get_table_names())
        if "tasks" not in tables:
            # 新库直接创建当前完整结构并登记到迁移链顶端，避免重复执行历史
            # 迁移；以后只通过 Alembic 前向升级。
            Base.metadata.create_all(self.engine)
            self._stamp_revision("head")
        else:
            if "alembic_version" not in tables:
                # 1.0—1.1.3 试用库没有稳定 Alembic 标记。先运行最后一次
                # 幂等兼容适配，再登记为 0010；0011 起不再新增手写补列。
                self._apply_legacy_baseline()
                self._stamp_revision("0010")
            self._upgrade_to_head()
        self._record_schema_head()
        self._create_search_schema()

    def _alembic_config(self):
        from alembic.config import Config

        if getattr(sys, "frozen", False):
            # PyInstaller 冻结运行时：迁移脚本与 alembic.ini 由
            # packaging/uos/partyops.spec 的 datas 打进 _MEIPASS
            # （默认 _internal），与前端资源同根，显式定位避免依赖
            # __file__ 相对推导在冻结环境下失效。
            bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            config = Config(str(bundle_root / "alembic.ini"))
            config.set_main_option("script_location", str(bundle_root / "alembic"))
            return config
        backend_root = Path(__file__).resolve().parents[1]
        config = Config(str(backend_root / "alembic.ini"))
        config.set_main_option("script_location", str(backend_root / "alembic"))
        return config

    def _stamp_revision(self, revision: str) -> None:
        from alembic import command

        with self.engine.begin() as connection:
            config = self._alembic_config()
            config.attributes["connection"] = connection
            command.stamp(config, revision)

    def _upgrade_to_head(self) -> None:
        from alembic import command

        with self.engine.begin() as connection:
            config = self._alembic_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def _record_schema_head(self) -> None:
        """保留旧运维页面使用的版本记录，同时以 Alembic 为唯一真值。"""

        from alembic.script import ScriptDirectory

        head = ScriptDirectory.from_config(self._alembic_config()).get_current_head()
        if not head:
            raise RuntimeError("无法确定数据库迁移链顶端")
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_release_notes (
                  revision VARCHAR(32) PRIMARY KEY,
                  applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO schema_release_notes(revision) VALUES (?)",
                (head,),
            )

    def _apply_legacy_baseline(self) -> None:
        """仅为未登记的 1.0—1.1.3 数据库执行一次幂等兼容适配。"""

        additions: dict[str, list[tuple[str, str]]] = {
            "users": [("version", "INTEGER NOT NULL DEFAULT 1")],
            "tasks": [
                ("category", "VARCHAR(80) NOT NULL DEFAULT ''"),
                ("tags", "JSON NOT NULL DEFAULT '[]'"),
                ("parent_task_id", "VARCHAR(36)"),
                ("template_id", "VARCHAR(36)"),
                ("recurrence_rule_id", "VARCHAR(36)"),
                ("experience_notes", "TEXT NOT NULL DEFAULT ''"),
                ("contact_ids", "JSON NOT NULL DEFAULT '[]'"),
                ("planned_start_at", "DATETIME"),
                ("planned_end_at", "DATETIME"),
                ("work_area", "VARCHAR(100) NOT NULL DEFAULT ''"),
                ("annual_focus", "VARCHAR(160) NOT NULL DEFAULT ''"),
                ("reporting_scope", "VARCHAR(160) NOT NULL DEFAULT ''"),
            ],
            "material_items": [("version", "INTEGER NOT NULL DEFAULT 1")],
            "attachment_versions": [
                ("display_name", "VARCHAR(255) NOT NULL DEFAULT ''")
            ],
            "task_templates": [
                ("version", "INTEGER NOT NULL DEFAULT 1"),
                ("updated_at", "DATETIME"),
            ],
            "recurrence_rules": [
                ("internal_lead_days", "INTEGER NOT NULL DEFAULT 2"),
                ("last_task_id", "VARCHAR(36)"),
                ("notes", "TEXT NOT NULL DEFAULT ''"),
                ("contact_ids", "JSON NOT NULL DEFAULT '[]'"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "knowledge_entries": [("version", "INTEGER NOT NULL DEFAULT 1")],
            "contacts": [("version", "INTEGER NOT NULL DEFAULT 1")],
            "reminder_preferences": [
                ("reminder_days", "JSON NOT NULL DEFAULT '[7,3,1,0]'"),
                ("quiet_start", "VARCHAR(5) NOT NULL DEFAULT '22:00'"),
                ("quiet_end", "VARCHAR(5) NOT NULL DEFAULT '07:30'"),
                ("desktop_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
            ],
            "workspace_roots": [
                ("source", "VARCHAR(16) NOT NULL DEFAULT 'host'"),
                ("device_id", "VARCHAR(36)"),
                ("remote_key", "VARCHAR(255) NOT NULL DEFAULT ''"),
                ("approval_status", "VARCHAR(24) NOT NULL DEFAULT 'approved'"),
                ("selection_mode", "VARCHAR(16) NOT NULL DEFAULT 'all'"),
                ("included_paths", "JSON NOT NULL DEFAULT '[]'"),
            ],
            "workspace_files": [
                ("remote_file_key", "VARCHAR(255) NOT NULL DEFAULT ''"),
                ("availability", "VARCHAR(16) NOT NULL DEFAULT 'online'"),
                ("in_scope", "BOOLEAN NOT NULL DEFAULT 1"),
                ("content_status", "VARCHAR(32) NOT NULL DEFAULT 'metadata_only'"),
                ("content_error_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
                (
                    "detected_type",
                    "VARCHAR(160) NOT NULL DEFAULT 'application/octet-stream'",
                ),
                ("archive_member_count", "INTEGER NOT NULL DEFAULT 0"),
            ],
            "work_journal_entries": [
                ("event_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("event_data", "JSON NOT NULL DEFAULT '{}'"),
            ],
            "devices": [
                ("agent_token_hash", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ],
            "device_commands": [
                ("delivery_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("delivered_at", "DATETIME"),
            ],
        }
        with self.engine.begin() as connection:
            for table, columns in additions.items():
                existing = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                }
                if not existing:
                    continue
                for name, declaration in columns:
                    if name not in existing:
                        connection.exec_driver_sql(
                            f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'
                        )
            connection.exec_driver_sql(
                """
                UPDATE attachment_versions
                SET display_name = coalesce(
                  (SELECT original_name FROM file_blobs
                   WHERE file_blobs.sha256 = attachment_versions.blob_sha256),
                  '未命名文件'
                )
                WHERE display_name = ''
                """
            )
            connection.exec_driver_sql(
                "UPDATE task_templates SET updated_at = created_at WHERE updated_at IS NULL"
            )
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_tasks_category ON tasks(category)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id ON tasks(parent_task_id)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_template_id ON tasks(template_id)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_recurrence_rule_id ON tasks(recurrence_rule_id)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_planned_start_at ON tasks(planned_start_at)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_planned_end_at ON tasks(planned_end_at)",
                "CREATE INDEX IF NOT EXISTS ix_tasks_work_area ON tasks(work_area)",
            ):
                connection.exec_driver_sql(statement)
            connection.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS schema_release_notes (
                  revision VARCHAR(32) PRIMARY KEY,
                  applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO schema_release_notes(revision) VALUES ('0003')"
            )
            for revision in ("0004", "0005", "0006", "0007", "0008", "0009", "0010"):
                connection.exec_driver_sql(
                    "INSERT OR IGNORE INTO schema_release_notes(revision) VALUES (?)",
                    (revision,),
                )

    def _create_search_schema(self) -> None:
        statements = [
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS task_search_fts USING fts5(
              task_id UNINDEXED, title, description, source, tokenize='unicode61'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_insert AFTER INSERT ON tasks BEGIN
              INSERT INTO task_search_fts(task_id,title,description,source)
              VALUES (new.id,new.title,coalesce(new.description,''),coalesce(new.source,''));
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_update AFTER UPDATE ON tasks BEGIN
              DELETE FROM task_search_fts WHERE task_id=old.id;
              INSERT INTO task_search_fts(task_id,title,description,source)
              VALUES (new.id,new.title,coalesce(new.description,''),coalesce(new.source,''));
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_delete AFTER DELETE ON tasks BEGIN
              DELETE FROM task_search_fts WHERE task_id=old.id;
            END
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS task_search_fts_v2 USING fts5(
              task_id UNINDEXED, title, description, source, category, tags,
              tokenize='unicode61'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_v2_insert AFTER INSERT ON tasks BEGIN
              INSERT INTO task_search_fts_v2(task_id,title,description,source,category,tags)
              VALUES (
                new.id,new.title,coalesce(new.description,''),coalesce(new.source,''),
                coalesce(new.category,''),coalesce(new.tags,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_v2_update AFTER UPDATE ON tasks BEGIN
              DELETE FROM task_search_fts_v2 WHERE task_id=old.id;
              INSERT INTO task_search_fts_v2(task_id,title,description,source,category,tags)
              VALUES (
                new.id,new.title,coalesce(new.description,''),coalesce(new.source,''),
                coalesce(new.category,''),coalesce(new.tags,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS task_search_v2_delete AFTER DELETE ON tasks BEGIN
              DELETE FROM task_search_fts_v2 WHERE task_id=old.id;
            END
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS workspace_search_fts USING fts5(
              file_id UNINDEXED, name, relative_path, extracted_text, ocr_text,
              tokenize='unicode61'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_insert
            AFTER INSERT ON workspace_files BEGIN
              INSERT INTO workspace_search_fts(
                file_id,name,relative_path,extracted_text,ocr_text
              ) VALUES (
                new.id,new.name,new.relative_path,
                coalesce(new.extracted_text,''),coalesce(new.ocr_text,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_update
            AFTER UPDATE ON workspace_files BEGIN
              DELETE FROM workspace_search_fts WHERE file_id=old.id;
              INSERT INTO workspace_search_fts(
                file_id,name,relative_path,extracted_text,ocr_text
              ) VALUES (
                new.id,new.name,new.relative_path,
                coalesce(new.extracted_text,''),coalesce(new.ocr_text,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_delete
            AFTER DELETE ON workspace_files BEGIN
              DELETE FROM workspace_search_fts WHERE file_id=old.id;
            END
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS workspace_search_fts_v2 USING fts5(
              file_id UNINDEXED, name, relative_path, extracted_text, ocr_text,
              tokenize='trigram'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_v2_insert
            AFTER INSERT ON workspace_files BEGIN
              INSERT INTO workspace_search_fts_v2(
                file_id,name,relative_path,extracted_text,ocr_text
              ) VALUES (
                new.id,new.name,new.relative_path,
                coalesce(new.extracted_text,''),coalesce(new.ocr_text,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_v2_update
            AFTER UPDATE ON workspace_files BEGIN
              DELETE FROM workspace_search_fts_v2 WHERE file_id=old.id;
              INSERT INTO workspace_search_fts_v2(
                file_id,name,relative_path,extracted_text,ocr_text
              ) VALUES (
                new.id,new.name,new.relative_path,
                coalesce(new.extracted_text,''),coalesce(new.ocr_text,'')
              );
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_search_v2_delete
            AFTER DELETE ON workspace_files BEGIN
              DELETE FROM workspace_search_fts_v2 WHERE file_id=old.id;
            END
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS workspace_name_fts USING fts5(
              file_id UNINDEXED, name, relative_path,
              tokenize='trigram'
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_name_insert
            AFTER INSERT ON workspace_files BEGIN
              INSERT INTO workspace_name_fts(file_id,name,relative_path)
              VALUES (new.id,new.name,new.relative_path);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_name_update
            AFTER UPDATE ON workspace_files BEGIN
              DELETE FROM workspace_name_fts WHERE file_id=old.id;
              INSERT INTO workspace_name_fts(file_id,name,relative_path)
              VALUES (new.id,new.name,new.relative_path);
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS workspace_name_delete
            AFTER DELETE ON workspace_files BEGIN
              DELETE FROM workspace_name_fts WHERE file_id=old.id;
            END
            """,
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS archive_search_fts USING fts5(
              record_id UNINDEXED, title, document_no, body,
              tokenize='unicode61'
            )
            """,
        ]
        with self.engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
            connection.exec_driver_sql(
                """
                INSERT INTO task_search_fts(task_id,title,description,source)
                SELECT t.id,t.title,coalesce(t.description,''),coalesce(t.source,'')
                FROM tasks t
                WHERE NOT EXISTS (
                  SELECT 1 FROM task_search_fts f WHERE f.task_id=t.id
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workspace_search_fts(
                  file_id,name,relative_path,extracted_text,ocr_text
                )
                SELECT
                  w.id,w.name,w.relative_path,
                  coalesce(w.extracted_text,''),coalesce(w.ocr_text,'')
                FROM workspace_files w
                WHERE NOT EXISTS (
                  SELECT 1 FROM workspace_search_fts f WHERE f.file_id=w.id
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workspace_search_fts_v2(
                  file_id,name,relative_path,extracted_text,ocr_text
                )
                SELECT
                  w.id,w.name,w.relative_path,
                  coalesce(w.extracted_text,''),coalesce(w.ocr_text,'')
                FROM workspace_files w
                WHERE NOT EXISTS (
                  SELECT 1 FROM workspace_search_fts_v2 f WHERE f.file_id=w.id
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workspace_name_fts(file_id,name,relative_path)
                SELECT w.id,w.name,w.relative_path
                FROM workspace_files w
                WHERE NOT EXISTS (
                  SELECT 1 FROM workspace_name_fts f WHERE f.file_id=w.id
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO task_search_fts_v2(task_id,title,description,source,category,tags)
                SELECT
                  t.id,t.title,coalesce(t.description,''),coalesce(t.source,''),
                  coalesce(t.category,''),coalesce(t.tags,'')
                FROM tasks t
                WHERE NOT EXISTS (
                  SELECT 1 FROM task_search_fts_v2 f WHERE f.task_id=t.id
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO archive_search_fts(record_id,title,document_no,body)
                SELECT id,title,coalesce(document_no,''),coalesce(search_text,'')
                FROM archive_records
                WHERE NOT EXISTS (
                  SELECT 1 FROM archive_search_fts f WHERE f.record_id=archive_records.id
                )
                """
            )

    def dispose(self) -> None:
        self.engine.dispose()

    def rebuild(self) -> None:
        self.dispose()
        self.engine = self._build_engine(self.settings.database_path)
        self.session_factory.configure(bind=self.engine)


@event.listens_for(Session, "do_orm_execute")
def register_orm_activity(orm_execute_state) -> None:
    """所有 ORM 查询和写入都参与恢复维护闸门。"""

    session = orm_execute_state.session
    runtime = session.info.get("partyops_database_runtime")
    if runtime:
        runtime.enter_session_activity(session)
        if (
            orm_execute_state.is_insert
            or orm_execute_state.is_update
            or orm_execute_state.is_delete
        ):
            _acquire_session_write_lock(session, runtime)


def _acquire_session_write_lock(session: Session, runtime: DatabaseRuntime) -> None:
    """让同一进程的 SQLite 写事务排队，避免后台任务与请求互相锁死。"""

    if session.info.get("partyops_write_lock_held"):
        return
    runtime.write_lock.acquire()
    session.info["partyops_write_lock_held"] = True


@event.listens_for(Session, "before_flush")
def register_flush_activity(session: Session, _flush_context, _instances) -> None:
    """覆盖未先查询、直接 add/delete 后提交的写事务。"""

    runtime = session.info.get("partyops_database_runtime")
    if runtime:
        runtime.enter_session_activity(session)
        _acquire_session_write_lock(session, runtime)


@event.listens_for(Session, "after_transaction_end")
def release_orm_activity(session: Session, transaction) -> None:
    """仅在最外层事务结束时释放，嵌套保存点不提前放行恢复。"""

    if transaction.parent is not None:
        return
    runtime = session.info.get("partyops_database_runtime")
    if runtime:
        runtime.leave_session_activity(session)
        if session.info.pop("partyops_write_lock_held", False):
            runtime.write_lock.release()


db_runtime = DatabaseRuntime()


def get_session() -> Generator[Session, None, None]:
    session = db_runtime.session_factory()
    try:
        yield session
    finally:
        session.close()
