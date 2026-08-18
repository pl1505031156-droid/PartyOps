"""运行配置与本地数据目录规则。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """返回符合操作系统习惯的用户数据目录。"""
    explicit = os.getenv("PARTYOPS_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return (base / "PartyOps").resolve()
    xdg = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (xdg / "partyops").resolve()


class Settings(BaseSettings):
    """应用配置；所有环境变量以 PARTYOPS_ 开头。"""

    model_config = SettingsConfigDict(
        env_prefix="PARTYOPS_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "党建智办"
    app_version: str = "1.4.3-rc.6"
    mode: Literal["host", "personal", "client"] = "host"
    # 未显式配置时按生产边界运行。开发脚本和自动化测试会主动设置
    # development/test，避免开源用户直接启动时意外开启调试与弱校验。
    environment: Literal["development", "test", "production"] = "production"
    data_dir: Path = Field(default_factory=default_data_dir)
    # host 保留为对协同电脑展示的兼容字段；bind_host 决定实际监听，
    # advertise_host 决定证书与下发地址。旧配置未设置新字段时行为不变。
    host: str = "127.0.0.1"
    bind_host: str = ""
    advertise_host: str = ""
    port: int = 18765
    agent_port: int = 18766
    session_hours: int = 12
    login_account_failure_limit: int = Field(default=5, ge=3, le=20)
    login_ip_failure_limit: int = Field(default=20, ge=5, le=200)
    login_window_seconds: int = Field(default=900, ge=60, le=86400)
    login_lock_seconds: int = Field(default=900, ge=60, le=86400)
    login_throttle_max_entries: int = Field(default=4096, ge=128, le=65536)
    max_upload_mb: int = 100
    frontend_dist: Path | None = None
    sqlite_min_version: str = "3.51.3"
    strict_sqlite: bool = False
    seed_demo: bool = False
    backup_hour: int = 18
    backup_minute: int = 30
    backup_daily_keep: int = 14
    backup_weekly_keep: int = 8
    # 导入包与解压后数据分别设上限，防止超大上传、ZIP 炸弹耗尽磁盘或内存。
    # 大型部署可通过环境变量显式调高，但不能依赖请求自行声明大小。
    backup_import_max_gb: int = Field(default=100, ge=1, le=2048)
    backup_restore_max_gb: int = Field(default=500, ge=1, le=4096)
    backup_max_members: int = Field(default=200_000, ge=100, le=1_000_000)
    # 旧式只读备份令牌仅用于兼容早期终端。设置固定有效期，避免令牌
    # 泄露后永久可用；新版设备证书通道不受此值影响。
    backup_pairing_ttl_days: int = Field(default=365, ge=1, le=3650)
    # 冻结运行时与生产部署均为同源，默认不信任任何跨域页面。开发脚本会
    # 显式加入本机 Vite 端口，避免把开发便利永久带进生产攻击面。
    allowed_origins: str = ""
    # Windows 配置向导通过无浏览器的本机 HTTPS 请求创建首位管理员。该随机
    # 令牌只保存在受保护控制配置中，用于区分向导与恶意网页发起的 localhost CSRF。
    bootstrap_token: str = ""
    update_public_key: str = ""
    update_catalog_url: str = "https://www.partyops.cn/releases/update-v3.json"
    update_download_hosts: str = (
        "partyops.cn,www.partyops.cn,github.com,objects.githubusercontent.com,"
        "github-releases.githubusercontent.com,"
        "bde850578a4c471bb62a0b2a5801d769.gz1.agentos-app.net"
    )
    model_pack_public_key: str = ""
    local_ai_port: int = 18767
    local_ai_max_threads: int = 4
    local_ai_memory_limit_mb: int = 3584
    max_devices: int = 20
    transfer_max_file_gb: int = 20
    transfer_quota_gb: int = 100
    minimum_free_gb: int = Field(default=2, ge=1, le=100)
    # 可再生/临时数据使用明确保留期，审计、工作日志和活动时间线不自动删除。
    notification_read_retention_days: int = Field(default=180, ge=30, le=3650)
    session_retention_days: int = Field(default=30, ge=1, le=365)
    transient_record_retention_days: int = Field(default=90, ge=7, le=730)
    event_outbox_retention_days: int = Field(default=30, ge=7, le=365)
    inbox_handled_retention_days: int = Field(default=30, ge=1, le=3650)
    inbox_unhandled_retention_days: int = Field(default=180, ge=30, le=3650)
    export_retention_days: int = Field(default=7, ge=1, le=365)
    upgrade_backup_retention_days: int = Field(default=30, ge=7, le=365)
    upgrade_backup_keep: int = Field(default=2, ge=1, le=10)
    tls_enabled: bool = False
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    tls_client_ca_file: Path | None = None
    tls_require_client_cert: bool = False

    @property
    def network_bind_host(self) -> str:
        return self.bind_host.strip() or self.host

    @property
    def network_advertise_host(self) -> str:
        return self.advertise_host.strip() or self.host

    @property
    def database_path(self) -> Path:
        return self.data_dir / "partyops.db"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def archives_dir(self) -> Path:
        return self.data_dir / "archives"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def updates_dir(self) -> Path:
        return self.data_dir / "updates"

    @property
    def transfers_dir(self) -> Path:
        return self.data_dir / "transfers"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.attachments_dir,
            self.backups_dir,
            self.exports_dir,
            self.archives_dir,
            self.logs_dir,
            self.secrets_dir,
            self.updates_dir,
            self.transfers_dir,
            self.inbox_dir,
            self.models_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings


def reset_settings_cache() -> None:
    """测试或配置向导修改环境变量后重新读取配置。"""
    get_settings.cache_clear()
