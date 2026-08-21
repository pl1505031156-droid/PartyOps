"""1.4.3-rc.3 平台识别、候选版本与更新清单 v3 回归。"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import asyncio

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.platform_info import detect_platform_info, read_os_release, update_platform_key
from app import compat as app_compat
from app.compat import StrEnum, strict_zip, to_thread
from app.problems import ProblemException
from app.routers import updates
from app.setup_wizard import HostStartupError
from app.startup_diagnostics import (
    DATABASE_CORRUPT,
    DATABASE_LOCKED,
    DATABASE_SCHEMA_FAILED,
    classify_database_startup_error,
)
from app.versioning import parse_release_version
from app.windows_host_status import tail_service_log


def test_linux_distribution_and_package_detection(monkeypatch, tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text(
        'ID="openEuler"\nVERSION_ID="24.03"\nID_LIKE="rhel fedora"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.platform_info.sys.platform", "linux")
    monkeypatch.setattr("app.platform_info.platform.machine", lambda: "aarch64")
    info = detect_platform_info(os_release_path=release)
    assert info == {
        "platform_family": "linux",
        "distribution": "openeuler",
        "distribution_version": "24.03",
        "package_format": "rpm",
        "architecture": "arm64",
        "runtime_profile": "full",
        "capabilities": [
            "host",
            "collaboration",
            "database",
            "files",
            "archives",
            "backup",
            "ocr",
            "semantic_rerank",
            "local_llm",
        ],
        "platform": "uos",
    }
    assert update_platform_key(info) == "linux-rpm"


@pytest.mark.parametrize("release_track", ["2107", "2203", "2303", "2403", "2503"])
def test_kylin_v10_sp1_series_selects_arm64_deb(
    monkeypatch, tmp_path: Path, release_track: str
) -> None:
    """银河麒麟 V10 SP1 同系列 ARM 设备共用 ARM64 DEB，不按 CPU 型号拆包。"""

    release = tmp_path / "os-release"
    release.write_text(
        'ID="kylin"\n'
        f'VERSION_ID="V10-SP1-{release_track}"\n'
        'ID_LIKE="debian"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.platform_info.sys.platform", "linux")
    monkeypatch.setattr("app.platform_info.platform.machine", lambda: "aarch64")

    info = detect_platform_info(os_release_path=release)

    assert info["distribution"] == "kylin"
    assert info["distribution_version"] == f"V10-SP1-{release_track}"
    assert info["package_format"] == "deb"
    assert info["architecture"] == "arm64"
    assert update_platform_key(info) == "linux-deb"


def test_windows7_x86_reports_legacy_core_without_local_ai(monkeypatch) -> None:
    monkeypatch.setattr("app.platform_info.sys.platform", "win32")
    monkeypatch.setattr("app.platform_info.platform.machine", lambda: "i686")
    monkeypatch.setattr(
        "app.platform_info.platform.win32_ver",
        lambda: ("7", "6.1.7601", "SP1", "Multiprocessor Free"),
    )
    info = detect_platform_info()
    assert info["architecture"] == "x86"
    assert info["distribution"] == "windows7"
    assert info["runtime_profile"] == "legacy-core"
    assert "ocr" in info["capabilities"]
    assert "semantic_rerank" not in info["capabilities"]
    assert "local_llm" not in info["capabilities"]
    assert update_platform_key(info) == "windows7"


def test_os_release_parser_ignores_invalid_lines(tmp_path: Path) -> None:
    release = tmp_path / "os-release"
    release.write_text("# comment\nID=deepin\nINVALID LINE\nVERSION_ID='25'\n", encoding="utf-8")
    assert read_os_release(release) == {"ID": "deepin", "VERSION_ID": "25"}


def _v3_manifest() -> dict:
    names = {
        "windows": {"amd64": "PartyOps_1.4.3-rc.3_windows_amd64.exe"},
        "windows7": {
            "amd64": "PartyOps_1.4.3-rc.3_windows7_amd64.exe",
            "x86": "PartyOps_1.4.3-rc.3_windows7_x86.exe",
        },
        "linux-deb": {
            "amd64": "PartyOps_1.4.3-rc.3_linux_amd64.deb",
            "arm64": "PartyOps_1.4.3-rc.3_linux_arm64.deb",
        },
        "linux-rpm": {
            "amd64": "PartyOps-1.4.3-0.rc.3.1.x86_64.rpm",
            "arm64": "PartyOps-1.4.3-0.rc.3.1.aarch64.rpm",
        },
    }
    artifacts = {
        filename: {"size": 1, "sha256": "0" * 64}
        for mapping in names.values()
        for filename in mapping.values()
    }
    return {
        "format": "partyops-update",
        "format_version": 3,
        "version": "1.4.3-rc.3",
        "min_version": "1.4.3-rc.2",
        "schema_revision": "0019",
        "release_notes": ["多系统适配与安装器修复"],
        "platform_artifacts": names,
        "artifacts": artifacts,
    }


def test_v3_manifest_and_candidate_version_order(monkeypatch) -> None:
    monkeypatch.setattr(updates, "__version__", "1.4.3-rc.2")
    # 本用例验证历史 v3 清单契约，不应被当前 1.4.4 的数据库防降级门禁遮蔽。
    monkeypatch.setattr(updates, "SCHEMA_VERSION", "0019")
    updates._validate_manifest_contract(_v3_manifest())
    assert parse_release_version("1.4.3-rc.2") < parse_release_version("1.4.3-rc.3")
    assert parse_release_version("1.4.3-rc.3") < parse_release_version("1.4.3")


def test_v3_manifest_rejects_missing_platform_and_wrong_suffix(monkeypatch) -> None:
    monkeypatch.setattr(updates, "__version__", "1.4.3-rc.2")
    missing = _v3_manifest()
    missing["platform_artifacts"].pop("linux-rpm")
    with pytest.raises(ProblemException) as error:
        updates._validate_manifest_contract(missing)
    assert error.value.code == "UPDATE_PLATFORM_ARTIFACTS_INVALID"

    wrong = _v3_manifest()
    wrong["platform_artifacts"]["windows7"]["x86"] = wrong["platform_artifacts"]["windows7"]["amd64"]
    with pytest.raises(ProblemException) as error:
        updates._validate_manifest_contract(wrong)
    assert error.value.code == "UPDATE_PLATFORM_ARTIFACT_INVALID"


@pytest.mark.parametrize("value", ["1.4", "v1.4.3", "1.4.3+local", ""])
def test_release_version_rejects_non_manifest_versions(value: str) -> None:
    with pytest.raises(ProblemException):
        parse_release_version(value)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RuntimeError("(sqlite3.OperationalError) database is locked"), DATABASE_LOCKED),
        (RuntimeError("database disk image is malformed"), DATABASE_CORRUPT),
        (RuntimeError("duplicate column name: capabilities"), DATABASE_SCHEMA_FAILED),
    ],
)
def test_database_startup_error_is_stable_and_hides_traceback(error: Exception, code: str) -> None:
    actual, message = classify_database_startup_error(error)
    assert actual == code
    assert message and "sqlalche.me" not in message
    public_error = HostStartupError(code, "启动失败", detail="secret traceback sqlalche.me/e/20/e3q8")
    assert code in str(public_error)
    assert "secret traceback" not in str(public_error)
    assert "secret traceback" in public_error.detail


def test_service_log_tail_drops_partial_utf8_line(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "partyops-host-service.log"
    log.parent.mkdir()
    log.write_text("被截断的中文首行\n完整诊断行\n", encoding="utf-8")
    tail = tail_service_log(tmp_path, max_bytes=24)
    assert "完整诊断行" in tail
    assert "�" not in tail


def _load_migration(name: str):
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0018_and_0019_migrations_are_safe_to_retry(monkeypatch, tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'retry.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE devices (id VARCHAR(36) PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))

        migration_18 = _load_migration("0018_party_development.py")
        monkeypatch.setattr(migration_18, "op", operations)
        migration_18.upgrade()
        migration_18.upgrade()

        migration_19 = _load_migration("0019_platform_packages.py")
        monkeypatch.setattr(migration_19, "op", operations)
        migration_19.upgrade()
        migration_19.upgrade()

        columns = {column["name"] for column in sa.inspect(connection).get_columns("devices")}
        assert {
            "platform_family",
            "distribution",
            "distribution_version",
            "package_format",
            "runtime_profile",
            "capabilities",
        } <= columns


def test_windows_service_forces_utf8_child_output() -> None:
    service = (
        Path(__file__).resolve().parents[2] / "packaging" / "windows" / "windows_service.py"
    ).read_text(encoding="utf-8")
    assert 'environment["PYTHONUTF8"] = "1"' in service
    assert 'environment["PYTHONIOENCODING"] = "utf-8"' in service


def test_python38_compatibility_layer_behaves_like_mainline() -> None:
    class Value(StrEnum):
        READY = "ready"

    assert str(Value.READY) == "ready"
    assert list(strict_zip([1, 2], [3, 4])) == [(1, 3), (2, 4)]
    with pytest.raises(ValueError):
        list(strict_zip([1], [2, 3]))
    assert asyncio.run(to_thread(lambda left, right: left + right, 2, 3)) == 5


def test_python38_hashlib_compat_accepts_starlette_etag_keyword(monkeypatch) -> None:
    """Win7 首页静态文件不能因 Starlette 的 ETag 调用返回 500。"""

    calls: list[bytes] = []

    class Digest:
        def hexdigest(self) -> str:
            return "etag"

    def legacy_md5(data: bytes = b"") -> Digest:
        calls.append(data)
        return Digest()

    monkeypatch.setattr(app_compat.sys, "version_info", (3, 8, 10))
    monkeypatch.setattr(app_compat.hashlib, "md5", legacy_md5)

    app_compat.install_legacy_hashlib_compat()

    assert (
        app_compat.hashlib.md5(b"asset", usedforsecurity=False).hexdigest()
        == "etag"
    )
    # 旧函数会在进入函数体前拒绝未知关键字，因此探测调用不会记录 data。
    assert calls == [b"asset"]


def test_win7_installer_probes_loader_capability_instead_of_exact_kb_name() -> None:
    """汇总更新已提供 Loader API 时，不得因 CBS 中没有旧 KB 名称误拦截。"""

    installer = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "windows"
        / "PartyOps.iss"
    ).read_text(encoding="utf-8-sig")

    assert "GetProcAddress" in installer
    assert "AddDllDirectory" in installer
    assert "IsInstalledUpdateInRoot" not in installer


def test_protocol_registration_is_per_user_and_never_blocks_installer() -> None:
    script = (
        Path(__file__).resolve().parents[2] / "packaging" / "windows" / "PartyOps.iss"
    ).read_text(encoding="utf-8")
    launcher = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "windows"
        / "windows_launcher.py"
    ).read_text(encoding="utf-8")
    assert "[Registry]" not in script
    prepare = script.split("function PrepareToInstall", 1)[1].split(
        "procedure RemoveOwnedProtocol", 1
    )[0]
    post_install = script.split("procedure CurStepChanged", 1)[1].split(
        "procedure DeinitializeSetup", 1
    )[0]
    assert "PreflightProtocolRegistry(" not in prepare
    assert "RegisterPartyOpsProtocols;" not in post_install
    assert "CurUninstallStepChanged" in script
    assert 'Software\\Classes\\{protocol}' in launcher
    assert "HKEY_CURRENT_USER" in launcher
    assert "PROTOCOL_USER_REGISTRY_DENIED" in launcher
    assert "安装和主功能未回滚" in launcher


def test_win7_inno_entries_have_architecture_specific_outputs() -> None:
    root = Path(__file__).resolve().parents[2] / "packaging" / "windows"
    x64 = (root / "PartyOps-Win7-x64.iss").read_text(encoding="utf-8")
    x86 = (root / "PartyOps-Win7-x86.iss").read_text(encoding="utf-8")
    assert "PartyOpsLegacy" in x64
    assert "windows7_amd64" in x64
    assert "PartyOpsLegacy" in x86 and "PartyOpsX86" in x86
    assert "windows7_x86" in x86


def test_win7_build_and_frontend_have_hard_compatibility_gates() -> None:
    root = Path(__file__).resolve().parents[2]
    builder = (root / "packaging" / "windows" / "build-windows7.ps1").read_text(
        encoding="utf-8"
    )
    pe_gate = (root / "scripts" / "validate-win7-pe.py").read_text(encoding="utf-8")
    vite = (root / "frontend" / "vite.config.mjs").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "validate-win7-wheelhouse.py" in builder
    assert "validate-win7-pe.py" in builder
    assert '"legacy-full"' in builder and '"legacy-core"' in builder
    assert "MajorSubsystemVersion" in pe_gate
    assert "GetSystemTimePreciseAsFileTime" in pe_gate
    assert 'legacy from "@vitejs/plugin-legacy"' in vite
    assert '"Chrome >= 64"' in vite and "renderLegacyChunks: false" in vite
    assert "Internet Explorer 11 不受支持" in html


def test_frontend_has_pre_module_startup_watchdog() -> None:
    """主模块或分包加载失败时必须留下可见恢复页，不能先清空再白屏。"""

    root = Path(__file__).resolve().parents[2] / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    main = (root / "src" / "main.ts").read_text(encoding="utf-8")
    watchdog = (root / "public" / "startup-watchdog.js").read_text(encoding="utf-8")
    assert html.index("/startup-watchdog.js") < html.index('/src/main.ts')
    assert "FRONTEND_ASSET_LOAD_FAILED" in watchdog
    assert "FRONTEND_STARTUP_TIMEOUT" in watchdog
    assert "partyops:started" in watchdog
    assert "__PARTYOPS_STARTED__ = true" in main
